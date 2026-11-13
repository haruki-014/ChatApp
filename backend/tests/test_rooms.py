import pytest

from chat import services
from chat.models import Message, Room, RoomMember

pytestmark = pytest.mark.django_db


def test_create_channel_and_reject_duplicate_name(api, alice, bob):
    client = api(alice)
    res = client.post("/api/rooms", {"type": "channel", "name": "dev", "member_ids": [bob.id]}, format="json")
    assert res.status_code == 201, res.data
    assert res.data["role"] == "owner"
    assert res.data["member_count"] == 2

    res = client.post("/api/rooms", {"type": "channel", "name": "DEV"}, format="json")
    assert res.status_code == 400  # 大文字小文字を区別せずユニーク


def test_group_requires_two_others_and_is_always_private(api, alice, bob, carol):
    client = api(alice)
    assert client.post("/api/rooms", {"type": "group", "member_ids": [bob.id]}, format="json").status_code == 400
    res = client.post(
        "/api/rooms", {"type": "group", "member_ids": [bob.id, carol.id], "is_private": False}, format="json"
    )
    assert res.status_code == 201
    assert res.data["is_private"] is True
    assert res.data["display_name"] == "Bob、Carol"


def test_dm_is_unique_per_pair(api, alice, bob):
    res1 = api(alice).post("/api/rooms/dm", {"user_id": bob.id}, format="json")
    res2 = api(bob).post("/api/rooms/dm", {"user_id": alice.id}, format="json")  # 逆向きでも同じ DM
    assert res1.status_code == 201
    assert res2.status_code == 200
    assert res1.data["id"] == res2.data["id"]
    assert res1.data["display_name"] == "Bob"
    assert res2.data["dm_partner"]["username"] == "alice"
    assert Room.objects.filter(type="dm").count() == 1


def test_dm_with_self_is_rejected(api, alice):
    assert api(alice).post("/api/rooms/dm", {"user_id": alice.id}, format="json").status_code == 400


def test_room_list_has_unread_counts_and_is_sorted(api, alice, bob, carol, channel):
    dm, _ = services.get_or_create_dm(alice, carol)
    services.post_message(room_id=channel.id, sender=bob, body="1", attachment_ids=[], client_msg_id=None)
    services.post_message(room_id=channel.id, sender=bob, body="2", attachment_ids=[], client_msg_id=None)
    services.post_message(room_id=channel.id, sender=alice, body="mine", attachment_ids=[], client_msg_id=None)
    services.post_message(room_id=dm.id, sender=carol, body="hi", attachment_ids=[], client_msg_id=None)

    res = api(alice).get("/api/rooms")
    assert res.status_code == 200
    rooms = res.data["rooms"]
    assert [r["id"] for r in rooms] == [dm.id, channel.id]  # 最後に発言があった順
    assert {r["id"]: r["unread_count"] for r in rooms} == {dm.id: 1, channel.id: 2}  # 自分の投稿は数えない
    assert res.data["sync_token"]


def test_read_updates_unread_and_never_goes_backwards(api, alice, bob, channel, sent, django_capture_on_commit_callbacks):
    ids = [
        services.post_message(room_id=channel.id, sender=bob, body=str(i), attachment_ids=[], client_msg_id=None)[0].id
        for i in range(3)
    ]
    client = api(alice)
    with django_capture_on_commit_callbacks(execute=True):
        res = client.post(f"/api/rooms/{channel.id}/read", {"last_read_message_id": ids[1]}, format="json")
    assert res.data["unread_count"] == 1
    assert sent[-1][0] == f"user.{alice.id}"  # 自分の他のタブにだけ送る
    assert sent[-1][1]["event"]["type"] == "read.updated"

    res = client.post(f"/api/rooms/{channel.id}/read", {"last_read_message_id": ids[0]}, format="json")
    assert res.data["last_read_message_id"] == ids[1]  # 巻き戻らない

    res = client.post(f"/api/rooms/{channel.id}/read", {"last_read_message_id": 10**9}, format="json")
    assert res.data["last_read_message_id"] == ids[2]  # 存在する最新 id で頭打ち
    assert res.data["unread_count"] == 0


def test_deleted_messages_are_not_counted_as_unread(api, alice, bob, channel):
    msg, _ = services.post_message(room_id=channel.id, sender=bob, body="x", attachment_ids=[], client_msg_id=None)
    services.delete_message(msg)
    rooms = api(alice).get("/api/rooms").data["rooms"]
    assert rooms[0]["unread_count"] == 0


def test_join_public_channel_starts_with_no_unread(api, alice, bob, carol, channel, sent, django_capture_on_commit_callbacks):
    services.post_message(room_id=channel.id, sender=bob, body="old", attachment_ids=[], client_msg_id=None)
    client = api(carol)
    browse = client.get("/api/channels").data
    assert browse[0]["is_member"] is False

    with django_capture_on_commit_callbacks(execute=True):
        res = client.post(f"/api/rooms/{channel.id}/join")
    assert res.status_code == 200
    assert res.data["unread_count"] == 0  # 過去ログは未読にしない
    groups_and_types = [(g, m["type"]) for g, m in sent]
    assert (f"user.{carol.id}", "chat.room_added") in groups_and_types
    assert (f"room.{channel.id}", "chat.event") in groups_and_types  # room.member_joined


def test_private_channel_cannot_be_joined(api, alice, carol):
    room = services.create_room(alice, type="channel", name="secret", is_private=True, member_ids=[])
    assert api(carol).post(f"/api/rooms/{room.id}/join").status_code == 404


def test_only_owner_or_admin_can_remove_members(api, alice, bob, carol, channel):
    services.add_members(channel, [carol.id])
    assert api(bob).delete(f"/api/rooms/{channel.id}/members/{carol.id}").status_code == 403
    assert api(alice).delete(f"/api/rooms/{channel.id}/members/{carol.id}").status_code == 204
    assert not RoomMember.objects.filter(room=channel, user=carol).exists()


def test_leave_channel_but_not_dm(api, alice, bob, channel):
    dm, _ = services.get_or_create_dm(alice, bob)
    assert api(bob).post(f"/api/rooms/{dm.id}/leave").status_code == 400
    assert api(bob).post(f"/api/rooms/{channel.id}/leave").status_code == 204
    assert api(bob).get(f"/api/rooms/{channel.id}").status_code == 404


def test_rename_requires_role_and_broadcasts(api, alice, bob, channel, sent, django_capture_on_commit_callbacks):
    assert api(bob).patch(f"/api/rooms/{channel.id}", {"name": "x"}, format="json").status_code == 403
    with django_capture_on_commit_callbacks(execute=True):
        res = api(alice).patch(f"/api/rooms/{channel.id}", {"name": "random"}, format="json")
    assert res.data["name"] == "random"
    assert sent[-1][1]["event"]["type"] == "room.updated"


def test_mute(api, alice, channel):
    res = api(alice).patch(f"/api/rooms/{channel.id}/membership", {"is_muted": True}, format="json")
    assert res.data["is_muted"] is True


def test_seed_demo_is_idempotent(db):
    from django.core.management import call_command

    call_command("seed_demo")
    call_command("seed_demo")
    assert Room.objects.filter(type="channel", name="general").count() == 1
    assert Room.objects.filter(type="dm").count() == 1
    assert Message.objects.count() == 0
