import asyncio
import io
import json
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError
from asgiref.sync import sync_to_async
from django.db import connections
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase, override_settings
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from .attachments import build_input, ensure_bucket, validate_file
from .models import Attachment, Conversation, Message


def image_upload():
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "blue").save(output, format="PNG")
    return SimpleUploadedFile("example.png", output.getvalue(), content_type="image/png")


def pdf_upload(text=True):
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    if text:
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
            DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 20 200 Td (Django models store data.) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return SimpleUploadedFile("guide.pdf", output.getvalue(), content_type="application/pdf")


class AttachmentTests(TransactionTestCase):
    def setUp(self):
        self.storage = Mock()
        self.patcher = patch("chat.views.storage_client", return_value=self.storage)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def upload(self, file=None, conversation=None):
        data = {"file": file or image_upload()}
        if conversation:
            data["conversation_id"] = str(conversation.id)
        return self.client.post("/api/attachments/", data)

    def test_upload_stores_private_object_and_restores_metadata(self):
        response = self.upload()
        self.assertEqual(response.status_code, 201)
        attachment = Attachment.objects.get()
        kwargs = self.storage.put_object.call_args.kwargs
        self.assertEqual(kwargs["Key"], attachment.object_key)
        self.assertNotIn("ACL", kwargs)
        restored = self.client.get(f"/api/conversations/{attachment.conversation_id}/messages/").json()
        self.assertEqual(restored["attachments"][0]["name"], "example.png")

    def test_pdf_text_is_extracted_with_page_numbers(self):
        response = self.upload(pdf_upload())
        self.assertEqual(response.status_code, 201)
        self.assertIn("[第 1 页]", Attachment.objects.get().extracted_text)
        self.assertIn("Django models", Attachment.objects.get().extracted_text)

    def test_scanned_pdf_is_rejected_before_storage(self):
        self.assertEqual(self.upload(pdf_upload(False)).status_code, 400)
        self.storage.put_object.assert_not_called()

    def test_forged_image_is_rejected(self):
        response = self.upload(SimpleUploadedFile("fake.png", b"not an image"))
        self.assertEqual(response.status_code, 400)
        self.storage.put_object.assert_not_called()

    @override_settings(ATTACHMENT_MAX_BYTES=8)
    def test_oversized_file_is_rejected(self):
        self.assertEqual(self.upload().status_code, 400)

    def test_storage_failure_does_not_create_records(self):
        self.storage.put_object.side_effect = RuntimeError("offline")
        with self.assertLogs("chat.views", level="ERROR"):
            self.assertEqual(self.upload().status_code, 502)
        self.assertFalse(Attachment.objects.exists())
        self.assertFalse(Conversation.objects.exists())

    def test_database_failure_removes_uploaded_object(self):
        with patch.object(Attachment, "save", side_effect=RuntimeError("database failed")):
            with self.assertLogs("chat.views", level="ERROR"):
                self.assertEqual(self.upload().status_code, 502)
        self.storage.delete_object.assert_called_once()
        self.assertFalse(Conversation.objects.exists())

    def test_foreign_attachment_rejected_on_both_chat_routes(self):
        self.upload()
        attachment = Attachment.objects.get()
        other = Conversation.objects.create()
        for route in ["/api/chat/", "/api/chat/stream/"]:
            response = self.client.post(route, data=json.dumps({"message": "Explain",
                "conversation_id": str(other.id), "attachment_ids": [str(attachment.id)]}),
                content_type="application/json")
            self.assertEqual(response.status_code, 400)

    @patch("chat.views.run_agent", return_value="Hello")
    def test_new_text_chat_without_attachments_still_works(self, runner):
        response = self.client.post("/api/chat/", data=json.dumps({"message": "Hi"}), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Message.objects.count(), 2)
        self.assertEqual(runner.call_args.args[0], [{"role": "user", "content": "Hi"}])

    @patch("chat.views.run_agent", return_value="Explanation")
    def test_pdf_chat_includes_selected_reference(self, runner):
        self.upload(pdf_upload())
        attachment = Attachment.objects.get()
        response = self.client.post("/api/chat/", data=json.dumps({"message": "Explain",
            "conversation_id": str(attachment.conversation_id), "attachment_ids": [str(attachment.id)]}),
            content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Django models", runner.call_args.args[0][-1]["content"][1]["text"])

    def test_image_input_uses_inline_data_and_closes_storage_body(self):
        self.upload()
        attachment = Attachment.objects.get()
        body = io.BytesIO(image_upload().read())
        self.storage.get_object.return_value = {"Body": body}
        with patch("chat.attachments.storage_client", return_value=self.storage):
            items = build_input([], "Explain", [attachment])
        self.assertTrue(items[-1]["content"][-1]["image_url"].startswith("data:image/png;base64,"))
        self.assertTrue(body.closed)

    def test_download_streams_original_bytes(self):
        self.upload()
        attachment = Attachment.objects.get()
        self.storage.get_object.return_value = {"Body": io.BytesIO(b"original"), "ContentLength": 8}
        response = self.client.get(f"/api/attachments/{attachment.id}/download/")
        self.assertEqual(b"".join(response.streaming_content), b"original")
        self.assertIn("attachment", response["Content-Disposition"])
        response.close()

    def test_streaming_chat_persists_completed_response(self):
        self.upload(pdf_upload())
        attachment = Attachment.objects.get()
        async def fake_stream(items):
            self.assertIn("Django models", items[-1]["content"][1]["text"])
            yield {"type": "delta", "text": "Answer"}
            yield {"type": "complete", "text": "Answer"}
        with patch("chat.views.stream_agent", fake_stream):
            response = self.client.post("/api/chat/stream/", data=json.dumps({"message": "Explain",
                "conversation_id": str(attachment.conversation_id), "attachment_ids": [str(attachment.id)]}),
                content_type="application/json")
            async def consume():
                try:
                    return b"".join([chunk async for chunk in response.streaming_content])
                finally:
                    await sync_to_async(connections.close_all, thread_sensitive=True)()
            events = asyncio.run(consume())
        self.assertIn(b"event: done", events)
        self.assertEqual(Message.objects.count(), 2)

    def test_bucket_permission_error_is_not_mistaken_for_missing_bucket(self):
        self.storage.head_bucket.side_effect = ClientError({"Error": {"Code": "403"}}, "HeadBucket")
        with self.assertRaises(ClientError):
            ensure_bucket(self.storage)
        self.storage.create_bucket.assert_not_called()
