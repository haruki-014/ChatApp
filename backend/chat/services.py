"""
業務ロジック（Spring でいう @Service 層）。View は入力チェックとレスポンス作成に専念し、
「DB を更新して、コミット後にイベントを配信する」処理はここにまとめる。
"""

from collections import defaultdict

from django.db import IntegrityError, transaction
from django.db.models import Count, F, IntegerField, Max, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce, Greatest
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from accounts.models import User
from accounts.serializers import UserSummarySerializer

from . import events, storage
from .constants import SYNC_MAX_MESSAGES, SYNC_OVERLAP
from .models import Attachment, Message, Room, RoomMember
from .serializers import MessageSerializer, RoomSerializer, iso

# ---------------------------------------------------------------------------
# 読み取り
# ---------------------------------------------------------------------------


def membership_queryset(user):
    """
    自分の RoomMember に unread_count と member_count を注釈として付ける（docs/02-data-model.md の 2-8）。
    相関サブクエリなので、ルームがいくつあっても SQL は 1 本で済む。
    """
    unread = (
        Message.objects.filter(
            ~Q(sender_id=OuterRef("user_id")),  # 自分の投稿は未読に数えない
            room_id=OuterRef("room_id"),
            id__gt=OuterRef("last_read_message_id"),  # (room_id, id) インデックスの範囲スキャン
            deleted_at__isnull=True,
        )
        .order_by()
        .values("room_id")
        .annotate(c=Count("id"))
        .values("c")
    )
    members = (
        RoomMember.objects.filter(room_id=OuterRef("room_id"))
        .order_by()
        .values("room_id")
        .annotate(c=Count("id"))
        .values("c")
    )
    return (
        RoomMember.objects.filter(user=user)
        .select_related("room")
        .annotate(
            unread_count=Coalesce(Subquery(unread, output_field=IntegerField()), Value(0)),
            member_count=Coalesce(Subquery(members, output_field=IntegerField()), Value(0)),
        )
    )


def attach_peers(memberships: list[RoomMember], user) -> list[RoomMember]:
    """DM・グループの表示名を作るため、自分以外のメンバーを m.peers に入れる（N+1 にならないよう 1 クエリで）。"""
    room_ids = [m.room_id for m in memberships if m.room.type != Room.Type.CHANNEL]
    peers: dict[int, list[User]] = defaultdict(list)
    if room_ids:
        rows = (
            RoomMember.objects.filter(room_id__in=room_ids)
            .exclude(user=user)
            .select_related("user")
            .order_by("joined_at", "id")
        )
        for rm in rows:
            peers[rm.room_id].append(rm.user)
    for m in memberships:
        m.peers = peers.get(m.room_id, [])
    return memberships


def list_rooms(user) -> list[RoomMember]:
    memberships = list(
        membership_queryset(user).order_by(F("room__last_message_at").desc(nulls_last=True), "-room__created_at")
    )
    return attach_peers(memberships, user)


def get_room_for(user, room_id: int) -> RoomMember | None:
    m = membership_queryset(user).filter(room_id=room_id).first()
    return attach_peers([m], user)[0] if m else None


def serialize_room_for(user, room_id: int) -> dict | None:
    m = get_room_for(user, room_id)
    return RoomSerializer(m).data if m else None


def latest_message_id(room_id: int) -> int:
    return Message.objects.filter(room_id=room_id).aggregate(m=Max("id"))["m"] or 0


def message_queryset():
    return Message.objects.select_related("sender").prefetch_related("attachments")


def unread_count(membership: RoomMember) -> int:
    return (
        Message.objects.filter(
            room_id=membership.room_id, id__gt=membership.last_read_message_id, deleted_at__isnull=True
        )
        .exclude(sender_id=membership.user_id)
        .count()
    )


# ---------------------------------------------------------------------------
# ルーム
# ---------------------------------------------------------------------------


def create_room(creator, *, type: str, name: str | None, is_private: bool, member_ids: list[int]) -> Room:
    try:
        with transaction.atomic():
            room = Room.objects.create(type=type, name=name, is_private=is_private, created_by=creator)
            RoomMember.objects.create(room=room, user=creator, role=RoomMember.Role.OWNER)
            others = list(User.objects.filter(id__in=member_ids, is_active=True))
            RoomMember.objects.bulk_create([RoomMember(room=room, user=u) for u in others])
            for uid in [creator.id, *(u.id for u in others)]:
                events.notify_room_added(uid, room.id)
    except IntegrityError as e:  # 同名チャンネルが同時に作られた場合（DB の部分ユニーク制約が最後の砦）
        raise ValidationError({"name": "同じ名前のチャンネルが既にあります。"}) from e
    return room


def get_or_create_dm(me, other) -> tuple[Room, bool]:
    """
    同じ 2 人の DM を 1 つに保つ（docs/02-data-model.md の 2-3）。
    同時に呼ばれても、後の INSERT は部分ユニーク制約で IntegrityError になり、
    get_or_create がそれを捕まえて SELECT し直すので、両者が同じルームを受け取る。
    """
    with transaction.atomic():
        room, created = Room.objects.get_or_create(
            type=Room.Type.DM,
            dm_key=Room.make_dm_key(me.id, other.id),
            defaults={"created_by": me, "is_private": True},
        )
        if created:
            RoomMember.objects.bulk_create([RoomMember(room=room, user=me), RoomMember(room=room, user=other)])
            events.notify_room_added(me.id, room.id)
            events.notify_room_added(other.id, room.id)
    return room, created


def add_members(room: Room, user_ids: list[int]) -> list[User]:
    if room.type == Room.Type.DM:
        raise ValidationError({"detail": "DM にメンバーは追加できません。"})
    existing = set(RoomMember.objects.filter(room=room).values_list("user_id", flat=True))
    new_users = list(User.objects.filter(id__in=set(user_ids) - existing, is_active=True))
    if not new_users:
        return []
    # 参加時点の最新メッセージまでを既読扱いにし、過去ログ全部が未読になるのを防ぐ
    latest = latest_message_id(room.id)
    with transaction.atomic():
        RoomMember.objects.bulk_create(
            [RoomMember(room=room, user=u, last_read_message_id=latest) for u in new_users],
            ignore_conflicts=True,
        )
        member_count = RoomMember.objects.filter(room=room).count()
        for u in new_users:
            events.broadcast_room(
                room.id,
                "room.member_joined",
                {"room_id": room.id, "user": UserSummarySerializer(u).data, "member_count": member_count},
            )
            events.notify_room_added(u.id, room.id)
    return new_users


def remove_member(room: Room, user_id: int) -> bool:
    with transaction.atomic():
        deleted, _ = RoomMember.objects.filter(room=room, user_id=user_id).delete()
        if deleted:
            member_count = RoomMember.objects.filter(room=room).count()
            events.notify_room_removed(user_id, room.id)
            events.broadcast_room(
                room.id, "room.member_left", {"room_id": room.id, "user_id": user_id, "member_count": member_count}
            )
    return bool(deleted)


# ---------------------------------------------------------------------------
# メッセージ
# ---------------------------------------------------------------------------


def post_message(*, room_id: int, sender, body: str, attachment_ids: list[int], client_msg_id) -> tuple[Message, bool]:
    """
    メッセージを保存し、コミット後に message.created を配信する。戻り値は (message, created)。

    1. 冪等キー: 同じ client_msg_id の再送なら、既存のメッセージを返す（二重投稿しない）
    2. 添付の確認: S3 に HEAD して、実在と実サイズを確認する（クライアントの申告を信じない）
    3. ルームの行を select_for_update でロックしてから INSERT する
       → 同じルームへの投稿が直列化され、id の順にコミットされる（docs/02-data-model.md の 2-5）
       → id をカーソルにした ?after= で取りこぼしが起きない
    """
    if client_msg_id:
        existing = message_queryset().filter(sender=sender, client_msg_id=client_msg_id).first()
        if existing:
            if existing.room_id != room_id:
                raise ValidationError({"client_msg_id": "別のルームで使われた値です。"})
            return existing, False

    # HEAD は通信を伴うので、ロックを取る前（トランザクションの外）で行う
    sizes: dict[int, int] = {}
    if attachment_ids:
        candidates = Attachment.objects.filter(
            id__in=attachment_ids, room_id=room_id, uploaded_by=sender, message__isnull=True
        )
        if candidates.count() != len(attachment_ids):
            raise ValidationError({"attachment_ids": "使用できない添付ファイルが含まれています。"})
        for att in candidates:
            size = storage.head_object(att.storage_key)
            if size is None:
                raise ValidationError({"attachment_ids": f"{att.original_filename} のアップロードが完了していません。"})
            sizes[att.id] = size

    try:
        with transaction.atomic():
            room = Room.objects.select_for_update().get(pk=room_id)
            msg = Message.objects.create(room=room, sender=sender, body=body, client_msg_id=client_msg_id)
            if attachment_ids:
                # 2 つのメッセージが同じ添付を取り合わないよう、ここでもロックして未使用かを確かめる
                atts = list(
                    Attachment.objects.select_for_update().filter(id__in=attachment_ids, message__isnull=True)
                )
                if len(atts) != len(attachment_ids):
                    raise ValidationError({"attachment_ids": "使用できない添付ファイルが含まれています。"})
                for att in atts:
                    att.message = msg
                    att.size = sizes[att.id]
                Attachment.objects.bulk_update(atts, ["message", "size"])
            room.last_message_at = msg.created_at
            room.save(update_fields=["last_message_at", "updated_at"])

            msg = message_queryset().get(pk=msg.pk)
            data = MessageSerializer(msg).data
            events.broadcast_room(room_id, "message.created", data)
    except IntegrityError:
        # 同じ client_msg_id の送信が同時に来た場合: 先にコミットした方を返す
        existing = message_queryset().filter(sender=sender, client_msg_id=client_msg_id).first()
        if existing is None:
            raise
        return existing, False
    return msg, True


def edit_message(msg: Message, body: str) -> Message:
    if not body.strip() and not msg.attachments.exists():
        raise ValidationError({"body": "本文を空にはできません。"})
    msg.body = body
    msg.edited_at = timezone.now()
    with transaction.atomic():
        msg.save(update_fields=["body", "edited_at", "updated_at"])
        data = MessageSerializer(message_queryset().get(pk=msg.pk)).data
        events.broadcast_room(msg.room_id, "message.updated", data)
    return msg


def delete_message(msg: Message) -> None:
    """論理削除。行は残し（カーソルと未読数の計算を壊さない）、本文は消す。"""
    if msg.is_deleted:
        return
    msg.body = ""
    msg.deleted_at = timezone.now()
    keys = list(msg.attachments.values_list("storage_key", flat=True))
    with transaction.atomic():
        msg.save(update_fields=["body", "deleted_at", "updated_at"])
        events.broadcast_room(
            msg.room_id,
            "message.deleted",
            {
                "id": msg.id,
                "room_id": msg.room_id,
                "deleted_at": iso(msg.deleted_at),
                "updated_at": iso(msg.updated_at),  # auto_now により save() でインスタンスにも反映済み
            },
        )
        # S3 の実体はコミット後に消す（ロールバックされたのにファイルだけ消える事態を防ぐ）
        for key in keys:
            transaction.on_commit(lambda k=key: storage.delete_object(k))


def mark_read(membership: RoomMember, message_id: int) -> RoomMember:
    """
    既読位置を「増える方向にだけ」更新する。複数タブから前後して届いても巻き戻らない。
    UPDATE ... SET last_read_message_id = GREATEST(last_read_message_id, :id)
    """
    target = min(message_id, latest_message_id(membership.room_id))
    RoomMember.objects.filter(pk=membership.pk).update(
        last_read_message_id=Greatest(F("last_read_message_id"), Value(target))
    )
    membership.refresh_from_db(fields=["last_read_message_id"])
    events.send_to_user(
        membership.user_id,
        "read.updated",
        {
            "room_id": membership.room_id,
            "last_read_message_id": membership.last_read_message_id,
            "unread_count": unread_count(membership),
        },
    )
    return membership


# ---------------------------------------------------------------------------
# 再同期（docs/03-rest-api.md の 3-5）
# ---------------------------------------------------------------------------


def sync_changes(user, since) -> dict:
    now = timezone.now()
    memberships = list_rooms(user)
    room_ids = [m.room_id for m in memberships]
    # SYNC_OVERLAP だけ遡る: updated_at（Python 側で採番）とコミット時刻のずれで境界のメッセージを落とさないため
    changed = list(
        message_queryset()
        .filter(room_id__in=room_ids, updated_at__gt=since - SYNC_OVERLAP)
        .order_by("updated_at", "id")[: SYNC_MAX_MESSAGES + 1]
    )
    reset = len(changed) > SYNC_MAX_MESSAGES
    return {
        "sync_token": now,
        "reset": reset,
        "rooms": memberships,
        "messages": [] if reset else changed,
    }
