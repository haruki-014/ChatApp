import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from accounts import presence
from chat import events

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "期限切れの WebSocket 接続をプレゼンスから外し、offline を配信する（常駐プロセス）"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="1 回だけ掃除して終了する")

    def handle(self, *args, once: bool = False, **options):
        interval = settings.PRESENCE_SWEEP_INTERVAL_SECONDS
        logger.info("presence sweeper started (interval=%ss)", interval)
        while True:
            try:
                for user_id in presence.sweep():
                    logger.info("user %s went offline (expired)", user_id)
                    events.send_presence_changed(user_id, is_online=False)
            except Exception:  # Redis の一時的な断などで常駐プロセスごと落ちないようにする
                logger.exception("presence sweep failed")
            if once:
                return
            time.sleep(interval)
