from django.core.management.base import BaseCommand
from django.utils import timezone

from chat import storage
from chat.constants import PENDING_ATTACHMENT_TTL
from chat.models import Attachment


class Command(BaseCommand):
    help = "presign されたまま、メッセージに紐付かなかった添付（孤児）を S3 と DB から削除する（cron で定期実行する想定）"

    def handle(self, *args, **options):
        # 部分インデックス idx_attachment_pending（message IS NULL）が効く
        orphans = Attachment.objects.filter(
            message__isnull=True, created_at__lt=timezone.now() - PENDING_ATTACHMENT_TTL
        )
        count = 0
        for att in orphans.iterator():
            storage.delete_object(att.storage_key)
            att.delete()
            count += 1
        self.stdout.write(f"deleted {count} orphan attachments")
