"""Exercise real HTTP, PostgreSQL and MinIO inside the Compose web container."""

import hashlib
import json
from pathlib import Path

import requests
from django.core.management.base import BaseCommand, CommandError

from chat.attachments import build_input, storage_client
from chat.models import Attachment, Conversation
from chat.tests import image_upload, pdf_upload


class Command(BaseCommand):
    help = "Docker smoke test: seed uploads, verify after restart, then clean up only smoke-test records."

    def add_arguments(self, parser):
        parser.add_argument("phase", choices=["seed", "verify", "cleanup", "model"])
        parser.add_argument("--state", default="/tmp/django-agent-smoke.json")

    def handle(self, *args, **options):
        state_path = Path(options["state"])
        client = requests.Session()
        base = "http://127.0.0.1:8000"
        homepage = client.get(base, timeout=15)
        homepage.raise_for_status()
        client.headers["X-CSRFToken"] = client.cookies["csrftoken"]
        phase = options["phase"]
        if phase == "seed":
            state = {"conversation_id": None, "attachments": []}
            for upload in [pdf_upload(), image_upload()]:
                content = upload.read()
                data = {"conversation_id": state["conversation_id"]} if state["conversation_id"] else {}
                response = client.post(base + "/api/attachments/", data=data,
                    files={"file": (upload.name, content, upload.content_type)}, timeout=30)
                if response.status_code != 201:
                    raise CommandError(f"Upload failed: HTTP {response.status_code}")
                result = response.json()
                state["conversation_id"] = result["conversation_id"]
                state["attachments"].append({**result["attachment"], "sha256": hashlib.sha256(content).hexdigest()})
                state_path.write_text(json.dumps(state))
            self.stdout.write("PASS: PDF and PNG uploaded over HTTP with CSRF protection.")
        else:
            state = json.loads(state_path.read_text())
        if phase in {"seed", "verify"}:
            response = client.get(base + f"/api/conversations/{state['conversation_id']}/messages/", timeout=15)
            response.raise_for_status()
            assert len(response.json()["attachments"]) == 2
            for item in state["attachments"]:
                response = client.get(base + item["download_url"], timeout=30)
                response.raise_for_status()
                assert hashlib.sha256(response.content).hexdigest() == item["sha256"]
                anonymous = requests.get(f"http://minio:9000/{Attachment.objects.get(pk=item['id']).bucket}/"
                    + Attachment.objects.get(pk=item["id"]).object_key, timeout=15)
                assert anonymous.status_code == 403
            attachments = list(Attachment.objects.filter(conversation_id=state["conversation_id"]))
            inputs = build_input([], "Explain the references", attachments)
            assert any(part["type"] == "input_image" for part in inputs[-1]["content"])
            assert any("Django models" in part.get("text", "") for part in inputs[-1]["content"])
            self.stdout.write("PASS: PG metadata, PDF text, image input, byte-identical downloads and private bucket.")
        elif phase == "model":
            for item in state["attachments"]:
                response = client.post(base + "/api/chat/stream/", json={
                    "message": "用一句话描述这个附件的内容。", "conversation_id": state["conversation_id"],
                    "attachment_ids": [item["id"]]}, timeout=120)
                if response.status_code != 200 or "event: done" not in response.text:
                    raise CommandError(f"Model streaming did not complete for {item['content_type']} (HTTP {response.status_code}). Check web logs.")
                self.stdout.write(f"PASS: real model streaming completed for {item['content_type']}.")
        elif phase == "cleanup":
            storage = storage_client()
            for item in state["attachments"]:
                attachment = Attachment.objects.get(pk=item["id"], conversation_id=state["conversation_id"])
                storage.delete_object(Bucket=attachment.bucket, Key=attachment.object_key)
            Conversation.objects.filter(pk=state["conversation_id"]).delete()
            state_path.unlink()
            self.stdout.write("Smoke-test records and objects removed.")
