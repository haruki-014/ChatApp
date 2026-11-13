import os

from django.conf import settings
from rest_framework import serializers

from accounts.models import User
from accounts.serializers import UserSummarySerializer

from .constants import ALLOWED_ATTACHMENT_TYPES, MAX_ATTACHMENTS_PER_MESSAGE, MAX_BODY_LENGTH
from .models import Attachment, Message, Room, RoomMember

_dt = serializers.DateTimeField()


def iso(value) -> str | None:
    return _dt.to_representation(value) if value else None


# ---------------------------------------------------------------------------
# 出力（REST と WebSocket イベントで共通の型。docs/03-rest-api.md の 3-7）
# ---------------------------------------------------------------------------
class AttachmentSerializer(serializers.ModelSerializer):
    is_image = serializers.BooleanField(read_only=True)
    download_url = serializers.SerializerMethodField()
    preview_url = serializers.SerializerMethodField()

    class Meta:
        model = Attachment
        fields = ["id", "original_filename", "content_type", "size", "is_image", "download_url", "preview_url"]

    def get_download_url(self, att: Attachment) -> str:
        return f"/api/attachments/{att.id}/download"

    def get_preview_url(self, att: Attachment) -> str | None:
        return f"/api/attachments/{att.id}/download?inline=1" if att.is_image else None


class MessageSerializer(serializers.ModelSerializer):
    room_id = serializers.IntegerField(read_only=True)
    sender = UserSummarySerializer(read_only=True)
    attachments = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            "id",
            "room_id",
            "sender",
            "body",
            "attachments",
            "client_msg_id",
            "created_at",
            "updated_at",
            "edited_at",
            "deleted_at",
        ]

    def get_attachments(self, msg: Message) -> list:
        if msg.is_deleted:  # 削除済みメッセージの添付は返さない（ダウンロードも View 側で拒否する）
            return []
        return AttachmentSerializer(msg.attachments.all(), many=True, context=self.context).data


def room_display_name(room: Room, peers: list[User]) -> str:
    if room.type == Room.Type.CHANNEL:
        return room.name or ""
    if room.type == Room.Type.DM:
        return peers[0].name_for_display if peers else "(不明なユーザー)"
    if room.name:
        return room.name
    names = [p.name_for_display for p in peers]
    return "、".join(names[:3]) + (f" 他{len(names) - 3}人" if len(names) > 3 else "")


class RoomSerializer(serializers.BaseSerializer):
    """
    入力は Room ではなく「自分の RoomMember」（services.membership_queryset で注釈を付けたもの）。
    未読数・既読位置・ミュートなど、見る人によって変わる値を一緒に返すため。
    """

    def to_representation(self, m: RoomMember) -> dict:
        room = m.room
        peers = getattr(m, "peers", [])
        user_ser = UserSummarySerializer(context=self.context)
        dm_partner = user_ser.to_representation(peers[0]) if room.type == Room.Type.DM and peers else None
        return {
            "id": room.id,
            "type": room.type,
            "name": room.name,
            "display_name": room_display_name(room, peers),
            "is_private": room.is_private,
            "dm_partner": dm_partner,
            "member_count": getattr(m, "member_count", 0),
            "last_message_at": iso(room.last_message_at),
            "role": m.role,
            "unread_count": getattr(m, "unread_count", 0),
            "last_read_message_id": m.last_read_message_id,
            "is_muted": m.is_muted,
        }


class ChannelSerializer(serializers.ModelSerializer):
    """公開チャンネルの一覧（参加前に探す用）。"""

    member_count = serializers.IntegerField(read_only=True)
    is_member = serializers.BooleanField(read_only=True)

    class Meta:
        model = Room
        fields = ["id", "name", "member_count", "is_member", "created_at"]


class MemberSerializer(serializers.ModelSerializer):
    user = UserSummarySerializer(read_only=True)

    class Meta:
        model = RoomMember
        fields = ["user", "role", "joined_at"]


# ---------------------------------------------------------------------------
# 入力
# ---------------------------------------------------------------------------
class RoomCreateSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=[Room.Type.GROUP, Room.Type.CHANNEL])
    name = serializers.CharField(max_length=80, required=False, allow_blank=True, allow_null=True)
    is_private = serializers.BooleanField(default=False)
    member_ids = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)

    def validate(self, attrs):
        creator = self.context["request"].user
        name = (attrs.get("name") or "").strip() or None
        others = set(attrs["member_ids"]) - {creator.id}
        if User.objects.filter(id__in=others, is_active=True).count() != len(others):
            raise serializers.ValidationError({"member_ids": "存在しないユーザーが含まれています。"})

        if attrs["type"] == Room.Type.CHANNEL:
            if not name:
                raise serializers.ValidationError({"name": "チャンネル名は必須です。"})
            if Room.objects.filter(type=Room.Type.CHANNEL, name__iexact=name).exists():
                raise serializers.ValidationError({"name": "同じ名前のチャンネルが既にあります。"})
        else:  # group DM
            if len(others) < 2:
                raise serializers.ValidationError({"member_ids": "グループは自分以外に 2 人以上が必要です。2 人なら DM を使ってください。"})
            attrs["is_private"] = True
        attrs["name"] = name
        attrs["member_ids"] = sorted(others)
        return attrs


class RoomUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=80, allow_blank=True)


class DMCreateSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()

    def validate_user_id(self, value: int) -> int:
        if value == self.context["request"].user.id:
            raise serializers.ValidationError("自分自身との DM は作成できません。")
        if not User.objects.filter(id=value, is_active=True).exists():
            raise serializers.ValidationError("ユーザーが見つかりません。")
        return value


class AddMembersSerializer(serializers.Serializer):
    user_ids = serializers.ListField(child=serializers.IntegerField(), min_length=1)


class MembershipUpdateSerializer(serializers.Serializer):
    is_muted = serializers.BooleanField()


class ReadSerializer(serializers.Serializer):
    last_read_message_id = serializers.IntegerField(min_value=0)


class MessageCreateSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=MAX_BODY_LENGTH, required=False, allow_blank=True, default="", trim_whitespace=False)
    attachment_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, default=list, max_length=MAX_ATTACHMENTS_PER_MESSAGE
    )
    client_msg_id = serializers.UUIDField(required=False, allow_null=True, default=None)

    def validate(self, attrs):
        if not attrs["body"].strip() and not attrs["attachment_ids"]:
            raise serializers.ValidationError({"body": "本文か添付ファイルのどちらかが必要です。"})
        attrs["attachment_ids"] = list(dict.fromkeys(attrs["attachment_ids"]))  # 重複を除く（順序は保つ）
        return attrs


class MessageUpdateSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=MAX_BODY_LENGTH, allow_blank=True, trim_whitespace=False)


class AttachmentPresignSerializer(serializers.Serializer):
    filename = serializers.CharField(max_length=255)
    content_type = serializers.ChoiceField(choices=sorted(ALLOWED_ATTACHMENT_TYPES))
    size = serializers.IntegerField(min_value=1)

    def validate_filename(self, value: str) -> str:
        # "C:\\fakepath\\a.pdf" や "../a.pdf" のようなパスを取り除き、ファイル名部分だけを残す
        name = os.path.basename(value.replace("\\", "/")).strip()
        if not name:
            raise serializers.ValidationError("ファイル名が不正です。")
        return name

    def validate_size(self, value: int) -> int:
        if value > settings.ATTACHMENT_MAX_BYTES:
            raise serializers.ValidationError("ファイルサイズは 50MB までです。")
        return value

    def validate(self, attrs):
        ext = os.path.splitext(attrs["filename"])[1].lower()
        if ext not in ALLOWED_ATTACHMENT_TYPES[attrs["content_type"]]:
            raise serializers.ValidationError({"filename": "拡張子とファイル形式が一致しません。"})
        return attrs
