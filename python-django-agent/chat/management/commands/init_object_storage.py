import time

from django.core.management.base import BaseCommand, CommandError

from chat.attachments import ensure_bucket, storage_client


class Command(BaseCommand):
    help = "Wait for local MinIO and create the private attachment bucket."

    def handle(self, *args, **options):
        client = storage_client()
        for attempt in range(15):
            try:
                ensure_bucket(client)
                self.stdout.write(self.style.SUCCESS("Object storage is ready."))
                return
            except Exception as exc:
                if attempt == 14:
                    raise CommandError("MinIO is unavailable; check endpoint and credentials.") from exc
                time.sleep(2)
