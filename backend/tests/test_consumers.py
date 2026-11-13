"""
WebSocket の結合テスト。WebsocketCommunicator は「テスト用のブラウザ」で、ASGI アプリに直接つなぐ。
REST 側の処理（services）を呼んで、別ユーザーの接続にイベントが届くかを確かめる。
"""

import pytest
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import Client

from chat import services
from config.asgi import application

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.asyncio]

ORIGIN = (b"origin", b"http://localhost:5173")


@database_sync_to_async
def session_cookie_for(user) -> bytes:
    client = Client()
    client.force_login(user)
    return f"sessionid={client.cookies['sessionid'].value}".encode()


async def connect(user=None, origin=ORIGIN):
    headers = [origin]
    if user is not None:
        headers.append((b"cookie", await session_cookie_for(user)))
    comm = WebsocketCommunicator(application, "/ws/", headers=headers)
    connected, _ = await comm.connect()
    return comm, connected


async def receive_type(comm, type_: str, timeout: float = 2, **data_match):
    """
    指定した type（と data の値）のイベントが来るまで読み飛ばす。
    group 経由のイベントと直接 send したイベントは到着順が保証されないので、順番に依存しないようにする。
    """
    while True:
        msg = await comm.receive_json_from(timeout=timeout)
        if msg["type"] == type_ and all(msg["data"].get(k) == v for k, v in data_match.items()):
            return msg


async def assert_no_event_of(comm, type_: str):
    while not await comm.receive_nothing(timeout=0.3):
        msg = await comm.receive_json_from()
        assert msg["type"] != type_, msg


post = database_sync_to_async(
    lambda room, user, body: services.post_message(
        room_id=room.id, sender=user, body=body, attachment_ids=[], client_msg_id=None
    )[0]
)


async def test_anonymous_is_closed_with_4401():
    comm, connected = await connect()
    assert connected  # accept してから…
    assert await comm.receive_output() == {"type": "websocket.close", "code": 4401}  # 理由付きで閉じる


async def test_foreign_origin_is_rejected(alice):
    comm, connected = await connect(alice, origin=(b"origin", b"http://evil.example.com"))
    assert not connected


async def test_hello_and_ping_pong(alice):
    comm, connected = await connect(alice)
    assert connected
    hello = await receive_type(comm, "hello")
    assert hello["data"] == {"user_id": alice.id}
    assert hello["ts"].endswith("Z")

    await comm.send_json_to({"type": "ping"})
    await receive_type(comm, "pong")
    await comm.disconnect()


async def test_message_is_delivered_to_members_only(alice, bob, carol, channel):
    alice_ws, _ = await connect(alice)
    carol_ws, _ = await connect(carol)  # #general のメンバーではない
    await receive_type(alice_ws, "hello")
    await receive_type(carol_ws, "hello")

    msg = await post(channel, bob, "リアルタイム！")

    event = await receive_type(alice_ws, "message.created")
    assert event["data"]["id"] == msg.id
    assert event["data"]["body"] == "リアルタイム！"
    assert event["data"]["sender"]["username"] == "bob"
    await assert_no_event_of(carol_ws, "message.created")

    await alice_ws.disconnect()
    await carol_ws.disconnect()


async def test_presence_changes_are_broadcast(alice, bob):
    bob_ws, _ = await connect(bob)
    await receive_type(bob_ws, "hello")

    alice_ws, _ = await connect(alice)
    event = await receive_type(bob_ws, "presence.changed", user_id=alice.id)
    assert event["data"] == {"user_id": alice.id, "is_online": True}

    await alice_ws.disconnect()
    event = await receive_type(bob_ws, "presence.changed", user_id=alice.id)
    assert event["data"] == {"user_id": alice.id, "is_online": False}
    await bob_ws.disconnect()


async def test_room_added_joins_group_dynamically(alice, bob, carol, channel):
    carol_ws, _ = await connect(carol)
    await receive_type(carol_ws, "hello")

    await database_sync_to_async(services.add_members)(channel, [carol.id])
    added = await receive_type(carol_ws, "room.added")
    assert added["data"]["id"] == channel.id
    assert added["data"]["unread_count"] == 0

    # 接続し直さなくても、追加されたルームのメッセージが届くようになる
    await post(channel, bob, "ようこそ")
    event = await receive_type(carol_ws, "message.created")
    assert event["data"]["body"] == "ようこそ"
    await carol_ws.disconnect()


async def test_room_removed_stops_delivery(alice, bob, channel):
    bob_ws, _ = await connect(bob)
    await receive_type(bob_ws, "hello")

    await database_sync_to_async(services.remove_member)(channel, bob.id)
    removed = await receive_type(bob_ws, "room.removed")
    assert removed["data"] == {"room_id": channel.id}

    await post(channel, alice, "bob には見えない")
    await assert_no_event_of(bob_ws, "message.created")
    await bob_ws.disconnect()


async def test_read_updated_goes_to_own_other_tabs(alice, bob, channel):
    tab1, _ = await connect(alice)
    tab2, _ = await connect(alice)
    bob_ws, _ = await connect(bob)
    for ws in (tab1, tab2, bob_ws):
        await receive_type(ws, "hello")

    msg = await post(channel, bob, "既読にして")
    membership = await database_sync_to_async(lambda: channel.memberships.get(user=alice))()
    await database_sync_to_async(services.mark_read)(membership, msg.id)

    for tab in (tab1, tab2):
        event = await receive_type(tab, "read.updated")
        assert event["data"] == {"room_id": channel.id, "last_read_message_id": msg.id, "unread_count": 0}
    await assert_no_event_of(bob_ws, "read.updated")
    for ws in (tab1, tab2, bob_ws):
        await ws.disconnect()
