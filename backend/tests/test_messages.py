import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from chat import services
from chat.models import Message

pytestmark = pytest.mark.django_db


def post(room, user, body="hi", **kw):
    return services.post_message(
        room_id=room.id, sender=user, body=body, attachment_ids=kw.get("attachment_ids", []),
        client_msg_id=kw.get("client_msg_id"),
    )[0]


# ---------------------------------------------------------------------------
# 認可
# ---------------------------------------------------------------------------


def test_non_member_gets_404_for_read_and_write(api, carol, channel):
    client = api(carol)
    assert client.get(f"/api/rooms/{channel.id}/messages").status_code == 404
    assert client.post(f"/api/rooms/{channel.id}/messages", {"body": "x"}, format="json").status_code == 404
    assert client.get(f"/api/rooms/{channel.id}").status_code == 404


def test_unauthenticated_gets_401_not_404(api, channel):
    assert api().get(f"/api/rooms/{channel.id}/messages").status_code == 401


# ---------------------------------------------------------------------------
# 送信
# ---------------------------------------------------------------------------


def test_post_message_broadcasts_after_commit(api, alice, channel, sent, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        res = api(alice).post(f"/api/rooms/{channel.id}/messages", {"body": "hello"}, format="json")
        assert res.status_code == 201
        assert sent == []  # コミット前には配信しない
    for cb in callbacks:
        cb()
    group, message = sent[0]
    assert group == f"room.{channel.id}"
    assert message["type"] == "chat.event"
    assert message["event"]["type"] == "message.created"
    assert message["event"]["data"]["id"] == res.data["id"]
    assert message["event"]["data"]["sender"]["username"] == "alice"
    channel.refresh_from_db()
    assert channel.last_message_at is not None


def test_post_requires_body_or_attachment(api, alice, channel):
    res = api(alice).post(f"/api/rooms/{channel.id}/messages", {"body": "   "}, format="json")
    assert res.status_code == 400


def test_client_msg_id_makes_post_idempotent(api, alice, channel):
    key = str(uuid.uuid4())
    client = api(alice)
    r1 = client.post(f"/api/rooms/{channel.id}/messages", {"body": "once", "client_msg_id": key}, format="json")
    r2 = client.post(f"/api/rooms/{channel.id}/messages", {"body": "once", "client_msg_id": key}, format="json")
    assert (r1.status_code, r2.status_code) == (201, 200)
    assert r1.data["id"] == r2.data["id"]
    assert Message.objects.count() == 1


# ---------------------------------------------------------------------------
# 履歴（カーソル方式）
# ---------------------------------------------------------------------------


def test_cursor_pagination(api, alice, channel):
    ids = [post(channel, alice, str(i)).id for i in range(120)]
    client = api(alice)

    page1 = client.get(f"/api/rooms/{channel.id}/messages").data
    assert [m["id"] for m in page1["results"]] == ids[-50:]  # 最新 50 件を古い順で
    assert page1["has_more"] is True
    assert page1["next_before"] == ids[-50]

    page2 = client.get(f"/api/rooms/{channel.id}/messages", {"before": page1["next_before"]}).data
    assert [m["id"] for m in page2["results"]] == ids[-100:-50]

    page3 = client.get(f"/api/rooms/{channel.id}/messages", {"before": page2["next_before"]}).data
    assert [m["id"] for m in page3["results"]] == ids[:20]
    assert page3["has_more"] is False

    newer = client.get(f"/api/rooms/{channel.id}/messages", {"after": ids[-3]}).data
    assert [m["id"] for m in newer["results"]] == ids[-2:]


def test_new_messages_do_not_shift_cursor_pages(api, alice, channel):
    """オフセット方式だとずれるケース: 過去ログを読んでいる間に新着が来ても重複しない。"""
    for i in range(60):
        post(channel, alice, str(i))
    client = api(alice)
    page1 = client.get(f"/api/rooms/{channel.id}/messages").data
    for i in range(5):
        post(channel, alice, f"new{i}")
    page2 = client.get(f"/api/rooms/{channel.id}/messages", {"before": page1["next_before"]}).data
    assert not {m["id"] for m in page1["results"]} & {m["id"] for m in page2["results"]}
    assert len(page2["results"]) == 10


def test_invalid_cursor_is_400(api, alice, channel):
    assert api(alice).get(f"/api/rooms/{channel.id}/messages", {"before": "abc"}).status_code == 400


# ---------------------------------------------------------------------------
# 編集・削除
# ---------------------------------------------------------------------------


def test_only_sender_can_edit_or_delete(api, alice, bob, channel):
    msg = post(channel, alice, "original")
    assert api(bob).patch(f"/api/messages/{msg.id}", {"body": "hacked"}, format="json").status_code == 403
    assert api(bob).delete(f"/api/messages/{msg.id}").status_code == 403


def test_edit_sets_edited_at_and_broadcasts(api, alice, channel, sent, django_capture_on_commit_callbacks):
    msg = post(channel, alice, "original")
    with django_capture_on_commit_callbacks(execute=True):
        res = api(alice).patch(f"/api/messages/{msg.id}", {"body": "edited"}, format="json")
    assert res.status_code == 200
    assert res.data["body"] == "edited"
    assert res.data["edited_at"] is not None
    assert sent[-1][1]["event"]["type"] == "message.updated"


def test_delete_is_soft_and_clears_body(api, alice, channel, sent, django_capture_on_commit_callbacks):
    msg = post(channel, alice, "secret")
    client = api(alice)
    with django_capture_on_commit_callbacks(execute=True):
        assert client.delete(f"/api/messages/{msg.id}").status_code == 204
    msg.refresh_from_db()
    assert msg.deleted_at is not None
    assert msg.body == ""
    event = sent[-1][1]["event"]
    assert event["type"] == "message.deleted"
    assert event["data"]["id"] == msg.id

    listed = client.get(f"/api/rooms/{channel.id}/messages").data["results"]
    assert listed[0]["body"] == "" and listed[0]["deleted_at"] is not None
    assert client.patch(f"/api/messages/{msg.id}", {"body": "x"}, format="json").status_code == 409


def test_member_who_left_cannot_edit(api, bob, channel):
    msg = post(channel, bob, "x")
    services.remove_member(channel, bob.id)
    assert api(bob).patch(f"/api/messages/{msg.id}", {"body": "y"}, format="json").status_code == 404


# ---------------------------------------------------------------------------
# 再同期
# ---------------------------------------------------------------------------


def test_sync_returns_created_edited_and_deleted_messages(api, alice, bob, channel):
    old = post(channel, bob, "old")
    Message.objects.filter(pk=old.pk).update(updated_at=timezone.now() - timedelta(minutes=5))
    since = timezone.now()

    created = post(channel, bob, "new")
    edited = post(channel, bob, "to-edit")
    services.edit_message(edited, "edited!")
    deleted = post(channel, bob, "to-delete")
    services.delete_message(deleted)

    res = api(alice).get("/api/sync", {"since": since.isoformat()})
    assert res.status_code == 200
    by_id = {m["id"]: m for m in res.data["messages"]}
    assert set(by_id) == {created.id, edited.id, deleted.id}  # 5 分前の old は含まれない
    assert by_id[edited.id]["body"] == "edited!"
    assert by_id[deleted.id]["deleted_at"] is not None
    assert res.data["reset"] is False
    assert res.data["rooms"][0]["unread_count"] == 3  # old・new・to-edit（削除済みは数えない）


def test_sync_overlap_window(api, alice, bob, channel):
    """since の直前（重なり幅の内側）に更新されたものも返す。"""
    msg = post(channel, bob, "edge")
    since = msg.updated_at + timedelta(seconds=5)
    res = api(alice).get("/api/sync", {"since": since.isoformat()})
    assert [m["id"] for m in res.data["messages"]] == [msg.id]


def test_sync_reset_when_too_many(api, alice, bob, channel, monkeypatch):
    monkeypatch.setattr("chat.services.SYNC_MAX_MESSAGES", 3)
    since = timezone.now() - timedelta(minutes=1)
    for i in range(4):
        post(channel, bob, str(i))
    res = api(alice).get("/api/sync", {"since": since.isoformat()})
    assert res.data["reset"] is True
    assert res.data["messages"] == []


def test_sync_requires_timezone(api, alice):
    assert api(alice).get("/api/sync", {"since": "2026-01-01T00:00:00"}).status_code == 400
