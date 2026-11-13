"""
REST の View（同期）から WebSocket へイベントを配信する（docs/04-websocket-events.md）。

  View ──group_send──▶ Redis（channel layer）──▶ 各プロセスの ChatConsumer ──▶ ブラウザ

ポイント
- 必ず transaction.on_commit の中で送る。コミット前に送ると「通知は来たのに DB に無い（ロールバックされた）」
  状態を作りうる。トランザクションの外で呼ばれた場合、on_commit はその場で実行される。
- View は同期関数、group_send は async 関数なので、async_to_sync で同期の世界から呼び出す。
- channel layer に渡す dict の "type" は、受け取った Consumer のメソッド名になる（"chat.event" → chat_event）。
"""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

PRESENCE_GROUP = "presence"


def room_group(room_id: int) -> str:
    return f"room.{room_id}"


def user_group(user_id: int) -> str:
    return f"user.{user_id}"


def now_iso() -> str:
    return timezone.now().isoformat().replace("+00:00", "Z")


def envelope(type_: str, data: dict) -> dict:
    """クライアントに届く JSON の共通の形（4-4）。ts は sync_token として使われる。"""
    return {"type": type_, "ts": now_iso(), "data": data}


def _group_send(group: str, message: dict) -> None:
    async_to_sync(get_channel_layer().group_send)(group, message)


def _on_commit(fn) -> None:
    # robust=True: 配信に失敗しても（Redis の一時的な断など）、コミット済みのリクエストを 500 にしない
    transaction.on_commit(fn, robust=True)


# ---------------------------------------------------------------------------
# クライアントへそのまま転送するイベント（Consumer.chat_event が送る）
# ---------------------------------------------------------------------------
def broadcast_room(room_id: int, type_: str, data: dict) -> None:
    """ルームのメンバー全員（の全タブ）へ。"""
    _on_commit(
        lambda: _group_send(
            room_group(room_id),
            {"type": "chat.event", "room_id": room_id, "event": envelope(type_, data)},
        )
    )


def send_to_user(user_id: int, type_: str, data: dict) -> None:
    """特定ユーザーの全タブへ（read.updated など、受信者ごとに値が違うもの）。"""
    _on_commit(lambda: _group_send(user_group(user_id), {"type": "chat.event", "event": envelope(type_, data)}))


def broadcast_presence_group(type_: str, data: dict) -> None:
    """全員へ（user.updated）。"""
    _on_commit(lambda: _group_send(PRESENCE_GROUP, {"type": "chat.event", "event": envelope(type_, data)}))


def presence_changed_message(user_id: int, is_online: bool) -> dict:
    return {"type": "chat.event", "event": envelope("presence.changed", {"user_id": user_id, "is_online": is_online})}


def send_presence_changed(user_id: int, is_online: bool) -> None:
    """presence_sweeper（同期）から使う。DB を触らないので on_commit は不要。"""
    _group_send(PRESENCE_GROUP, presence_changed_message(user_id, is_online))


# ---------------------------------------------------------------------------
# Consumer に処理をさせる制御イベント（4-9）
#   View は他人の WS 接続を直接 group に出し入れできない（channel_name を知らず、別プロセスにあるため）。
#   そこで本人の Consumer に「参加しなおして」と頼み、Consumer 自身が DB を確認して group_add / group_discard する。
# ---------------------------------------------------------------------------
def notify_room_added(user_id: int, room_id: int) -> None:
    _on_commit(lambda: _group_send(user_group(user_id), {"type": "chat.room_added", "room_id": room_id}))


def notify_room_removed(user_id: int, room_id: int) -> None:
    _on_commit(lambda: _group_send(user_group(user_id), {"type": "chat.room_removed", "room_id": room_id}))
