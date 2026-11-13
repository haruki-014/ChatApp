"""
WebSocket 1 接続 = ChatConsumer のインスタンス 1 つ（docs/04-websocket-events.md）。

DRF の View との違い:
- View はリクエストごとに生まれて、レスポンスを返したら消える
- Consumer は接続している間ずっと生きていて、connect → (receive / group からのイベント)* → disconnect と呼ばれる

この Consumer の役割は「サーバー → クライアントへの通知」だけ。書き込みはすべて REST で行う（4-1）。
クライアントから受け取るのはハートビートの ping のみ。
"""

import logging
import time

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings

from accounts import presence

from . import events
from .models import RoomMember
from .services import serialize_room_for

logger = logging.getLogger(__name__)

CLOSE_UNAUTHENTICATED = 4401

# ORM は同期 API なので、非同期の Consumer からはスレッドに逃がして呼ぶ
_room_ids_of = database_sync_to_async(
    lambda user_id: set(RoomMember.objects.filter(user_id=user_id).values_list("room_id", flat=True))
)
_is_member = database_sync_to_async(
    lambda user_id, room_id: RoomMember.objects.filter(user_id=user_id, room_id=room_id).exists()
)
_serialize_room_for = database_sync_to_async(serialize_room_for)


class ChatConsumer(AsyncJsonWebsocketConsumer):
    # ------------------------------------------------------------------
    # 接続のライフサイクル
    # ------------------------------------------------------------------
    async def connect(self):
        user = self.scope["user"]  # AuthMiddlewareStack が Cookie のセッションから設定する
        if not user.is_authenticated:
            # accept 前に close すると HTTP 403 になり、ブラウザには理由の分からない 1006 しか届かない。
            # 「未ログインなので再接続しなくてよい」と伝えるため、一度 accept してから独自コードで閉じる
            await self.accept()
            await self.close(code=CLOSE_UNAUTHENTICATED)
            return

        self.user = user
        self.room_ids: set[int] = await _room_ids_of(user.id)
        await self._join_all_groups()
        await self.accept()

        if await presence.connect(user.id, self.channel_name):
            await self.channel_layer.group_send(
                events.PRESENCE_GROUP, events.presence_changed_message(user.id, is_online=True)
            )
        await self.send_event("hello", {"user_id": user.id})

    async def disconnect(self, code):
        if not hasattr(self, "user"):  # 未ログインで閉じた接続
            return
        for group in self._group_names():
            await self.channel_layer.group_discard(group, self.channel_name)
        if await presence.disconnect(self.user.id, self.channel_name):
            await self.channel_layer.group_send(
                events.PRESENCE_GROUP, events.presence_changed_message(self.user.id, is_online=False)
            )

    async def receive_json(self, content, **kwargs):
        if not hasattr(self, "user"):
            return
        if isinstance(content, dict) and content.get("type") == "ping":
            # 期限を延長する。掃除役に一度オフラインにされていたら（スリープ復帰など）オンラインに戻す
            if await presence.heartbeat(self.user.id, self.channel_name):
                await self.channel_layer.group_send(
                    events.PRESENCE_GROUP, events.presence_changed_message(self.user.id, is_online=True)
                )
            if time.monotonic() - self._groups_refreshed_at > settings.WS_GROUP_REFRESH_SECONDS:
                await self._join_all_groups()  # group_expiry（既定 24 時間）で黙って外されるのを防ぐ
            await self.send_event("pong", {})
            return
        logger.info("ignored ws message from user %s: %r", self.user.id, content)

    # ------------------------------------------------------------------
    # channel layer から届くイベント（"type" の "." が "_" に変わってメソッド名になる）
    # ------------------------------------------------------------------
    async def chat_event(self, message):
        """View や他の Consumer が group_send したイベントを、そのままクライアントへ転送する。"""
        room_id = message.get("room_id")
        if room_id is not None and room_id not in self.room_ids:
            return  # 除外された直後、group から外れるまでの短い間に届いたもの（4-9）
        await self.send_json(message["event"])

    async def chat_room_added(self, message):
        """ルームに追加された。イベントを鵜呑みにせず DB で確認してから group に入る。"""
        room_id = message["room_id"]
        data = await _serialize_room_for(self.user, room_id)
        if data is None:
            return
        if room_id not in self.room_ids:
            self.room_ids.add(room_id)
            await self.channel_layer.group_add(events.room_group(room_id), self.channel_name)
        await self.send_event("room.added", data)

    async def chat_room_removed(self, message):
        room_id = message["room_id"]
        if await _is_member(self.user.id, room_id):
            return  # 外された後にまた追加された、など
        self.room_ids.discard(room_id)
        await self.channel_layer.group_discard(events.room_group(room_id), self.channel_name)
        await self.send_event("room.removed", {"room_id": room_id})

    # ------------------------------------------------------------------
    # 補助
    # ------------------------------------------------------------------
    def _group_names(self) -> list[str]:
        return [
            events.user_group(self.user.id),
            events.PRESENCE_GROUP,
            *(events.room_group(rid) for rid in self.room_ids),
        ]

    async def _join_all_groups(self):
        for group in self._group_names():
            await self.channel_layer.group_add(group, self.channel_name)
        self._groups_refreshed_at = time.monotonic()

    async def send_event(self, type_: str, data: dict):
        await self.send_json(events.envelope(type_, data))
