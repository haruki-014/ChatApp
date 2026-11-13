import itertools

import pytest
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from rest_framework.test import APIClient

from accounts import presence
from accounts.models import User
from chat import services
from chat.models import Room

PASSWORD = "correct-horse-battery-staple"
_seq = itertools.count(1)


@pytest.fixture(autouse=True)
def _clean_redis_and_layer():
    """テストごとに Redis（テスト用 DB 15）と、インメモリの channel layer を空にする。"""
    presence.sync_client().flushdb()
    async_to_sync(get_channel_layer().flush)()
    yield
    presence.sync_client().flushdb()


@pytest.fixture
def make_user(db):
    def _make(username: str | None = None, **extra) -> User:
        username = username or f"user{next(_seq)}"
        return User.objects.create_user(
            username=username, email=f"{username}@example.com", password=PASSWORD, **extra
        )

    return _make


@pytest.fixture
def alice(make_user):
    return make_user("alice", display_name="Alice")


@pytest.fixture
def bob(make_user):
    return make_user("bob", display_name="Bob")


@pytest.fixture
def carol(make_user):
    return make_user("carol", display_name="Carol")


@pytest.fixture
def api():
    """api(user) でその人としてログイン済みのクライアントを返す（セッション Cookie を使う）。"""

    def _client(user: User | None = None, *, enforce_csrf: bool = False) -> APIClient:
        client = APIClient(enforce_csrf_checks=enforce_csrf)
        if user is not None:
            client.force_login(user)
        return client

    return _client


@pytest.fixture
def channel(alice, bob):
    """alice（owner）と bob が参加している公開チャンネル #general。"""
    return services.create_room(alice, type=Room.Type.CHANNEL, name="general", is_private=False, member_ids=[bob.id])


@pytest.fixture
def sent(monkeypatch):
    """channel layer への group_send を記録する（REST 側から何が配信されたかを確かめる用）。"""
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr("chat.events._group_send", lambda group, message: calls.append((group, message)))
    return calls


@pytest.fixture
def fake_s3(monkeypatch):
    """S3 の HEAD / DELETE を差し替える。objects[key] = size を入れると「アップロード済み」になる。"""
    objects: dict[str, int] = {}
    monkeypatch.setattr("chat.storage.head_object", lambda key: objects.get(key))
    monkeypatch.setattr("chat.storage.delete_object", lambda key: objects.pop(key, None))
    return objects
