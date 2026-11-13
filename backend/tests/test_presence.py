import pytest
from django.core.management import call_command

from accounts import presence

pytestmark = pytest.mark.django_db


@pytest.mark.asyncio
async def test_online_until_last_connection_closes():
    """2 タブ開いて 1 つ閉じてもオンラインのまま。最後の 1 つを閉じたらオフライン。"""
    assert await presence.connect(1, "tab-a") is True  # 0 → 1 本: オンラインになった
    assert await presence.connect(1, "tab-b") is False  # 1 → 2 本: 変化なし
    assert await presence.disconnect(1, "tab-a") is False  # 2 → 1 本: まだオンライン
    assert presence.online_user_ids() == {1}
    assert await presence.disconnect(1, "tab-b") is True  # 1 → 0 本: オフラインになった
    assert presence.online_user_ids() == set()


def test_sweep_removes_expired_connections(sent):
    client = presence.sync_client()
    client.zadd(presence.conn_key(7), {"crashed-tab": 0})  # 期限切れの接続（プロセスがクラッシュした想定）
    client.sadd(presence.ONLINE_KEY, 7)
    client.zadd(presence.conn_key(8), {"alive-tab": 2**40})  # まだ有効な接続
    client.sadd(presence.ONLINE_KEY, 8)

    call_command("presence_sweeper", "--once")

    assert presence.online_user_ids() == {8}
    assert sent == [("presence", sent[0][1])]
    assert sent[0][1]["event"]["data"] == {"user_id": 7, "is_online": False}


def test_presence_endpoint(api, alice):
    presence.sync_client().sadd(presence.ONLINE_KEY, alice.id)
    res = api(alice).get("/api/presence")
    assert res.data == {"online_user_ids": [alice.id]}
    assert api(alice).get("/api/auth/me").data["is_online"] is True
