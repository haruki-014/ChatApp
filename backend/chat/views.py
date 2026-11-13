from django.conf import settings
from django.db.models import Count, Exists, OuterRef
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from accounts.serializers import UserSummarySerializer

from . import events, services, storage
from .constants import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from .models import Attachment, Message, Room, RoomMember
from .permissions import IsRoomMember, get_membership, require_role
from .serializers import (
    AddMembersSerializer,
    AttachmentPresignSerializer,
    ChannelSerializer,
    DMCreateSerializer,
    MemberSerializer,
    MembershipUpdateSerializer,
    MessageCreateSerializer,
    MessageSerializer,
    MessageUpdateSerializer,
    ReadSerializer,
    RoomCreateSerializer,
    RoomSerializer,
    RoomUpdateSerializer,
    iso,
)


class RoomMemberAPIView(APIView):
    """URL の room_id のメンバーだけが使える View の基底クラス。self.membership が使える。"""

    permission_classes = [IsAuthenticated, IsRoomMember]  # 順番が大事: 未ログインは 404 ではなく 401 にする
    membership: RoomMember


def _int_param(request, name: str) -> int | None:
    raw = request.query_params.get(name)
    if raw in (None, ""):
        return None
    try:
        value = int(raw)
    except ValueError:
        raise ValidationError({name: "整数を指定してください。"}) from None
    if value < 0:
        raise ValidationError({name: "0 以上を指定してください。"})
    return value


# ---------------------------------------------------------------------------
# ルーム
# ---------------------------------------------------------------------------


class RoomListCreateView(APIView):
    def get(self, request):
        rooms = services.list_rooms(request.user)
        return Response(
            {
                "rooms": RoomSerializer(rooms, many=True).data,
                # この時点を基準に、以降の変更は WS で受け取る。切断したらここから /api/sync する
                "sync_token": iso(timezone.now()),
            }
        )

    def post(self, request):
        serializer = RoomCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        room = services.create_room(request.user, **serializer.validated_data)
        return Response(services.serialize_room_for(request.user, room.id), status=status.HTTP_201_CREATED)


class DMCreateView(APIView):
    def post(self, request):
        serializer = DMCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        other = User.objects.get(pk=serializer.validated_data["user_id"])
        room, created = services.get_or_create_dm(request.user, other)
        return Response(
            services.serialize_room_for(request.user, room.id),
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class ChannelBrowseView(APIView):
    def get(self, request):
        channels = (
            Room.objects.filter(type=Room.Type.CHANNEL, is_private=False)
            .annotate(
                member_count=Count("memberships", distinct=True),
                is_member=Exists(RoomMember.objects.filter(room=OuterRef("pk"), user=request.user)),
            )
            .order_by("name")
        )
        return Response(ChannelSerializer(channels, many=True).data)


class RoomDetailView(RoomMemberAPIView):
    def get(self, request, room_id: int):
        return Response(services.serialize_room_for(request.user, room_id))

    def patch(self, request, room_id: int):
        room = self.membership.room
        if room.type == Room.Type.DM:
            raise ValidationError({"detail": "DM の名前は変更できません。"})
        require_role(self.membership, RoomMember.Role.OWNER, RoomMember.Role.ADMIN)
        serializer = RoomUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        name = serializer.validated_data["name"].strip() or None
        if room.type == Room.Type.CHANNEL:
            if not name:
                raise ValidationError({"name": "チャンネル名は必須です。"})
            if Room.objects.filter(type=Room.Type.CHANNEL, name__iexact=name).exclude(pk=room.pk).exists():
                raise ValidationError({"name": "同じ名前のチャンネルが既にあります。"})
        room.name = name
        room.save(update_fields=["name", "updated_at"])
        data = services.serialize_room_for(request.user, room_id)
        events.broadcast_room(
            room_id,
            "room.updated",
            {"id": room.id, "name": room.name, "display_name": data["display_name"], "is_private": room.is_private},
        )
        return Response(data)


class RoomJoinView(APIView):
    """公開チャンネルへの参加。メンバーでなくても呼べるので RoomMemberAPIView ではない。"""

    def post(self, request, room_id: int):
        room = Room.objects.filter(pk=room_id, type=Room.Type.CHANNEL, is_private=False).first()
        if room is None:
            raise NotFound("チャンネルが見つかりません。")
        services.add_members(room, [request.user.id])
        return Response(services.serialize_room_for(request.user, room_id))


class RoomLeaveView(RoomMemberAPIView):
    def post(self, request, room_id: int):
        if self.membership.room.type == Room.Type.DM:
            raise ValidationError({"detail": "DM からは退出できません。"})
        services.remove_member(self.membership.room, request.user.id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class RoomMembersView(RoomMemberAPIView):
    def get(self, request, room_id: int):
        members = RoomMember.objects.filter(room_id=room_id).select_related("user").order_by("joined_at", "id")
        return Response(MemberSerializer(members, many=True).data)

    def post(self, request, room_id: int):
        serializer = AddMembersSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        added = services.add_members(self.membership.room, serializer.validated_data["user_ids"])
        return Response(UserSummarySerializer(added, many=True).data, status=status.HTTP_201_CREATED)


class RoomMemberDeleteView(RoomMemberAPIView):
    def delete(self, request, room_id: int, user_id: int):
        if self.membership.room.type == Room.Type.DM:
            raise ValidationError({"detail": "DM のメンバーは変更できません。"})
        require_role(self.membership, RoomMember.Role.OWNER, RoomMember.Role.ADMIN)
        if not services.remove_member(self.membership.room, user_id):
            raise NotFound("メンバーが見つかりません。")
        return Response(status=status.HTTP_204_NO_CONTENT)


class MembershipView(RoomMemberAPIView):
    def patch(self, request, room_id: int):
        serializer = MembershipUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.membership.is_muted = serializer.validated_data["is_muted"]
        self.membership.save(update_fields=["is_muted"])
        return Response(services.serialize_room_for(request.user, room_id))


class ReadView(RoomMemberAPIView):
    def post(self, request, room_id: int):
        serializer = ReadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        m = services.mark_read(self.membership, serializer.validated_data["last_read_message_id"])
        return Response(
            {
                "room_id": room_id,
                "last_read_message_id": m.last_read_message_id,
                "unread_count": services.unread_count(m),
            }
        )


# ---------------------------------------------------------------------------
# メッセージ
# ---------------------------------------------------------------------------


class MessageListCreateView(RoomMemberAPIView):
    def get(self, request, room_id: int):
        """
        カーソル方式のページネーション（docs/03-rest-api.md の 3-4-1）。
          ?before=<id>  それより古いもの（過去ログを遡る）。省略すると最新から
          ?after=<id>   それより新しいもの
        どちらの場合も results は古い順に並べて返す。
        """
        before = _int_param(request, "before")
        after = _int_param(request, "after")
        limit = _int_param(request, "limit") or DEFAULT_PAGE_SIZE
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        if before is not None and after is not None:
            raise ValidationError({"detail": "before と after は同時に指定できません。"})

        qs = services.message_queryset().filter(room_id=room_id)
        if after is not None:
            # WHERE room_id=? AND id > ? ORDER BY id LIMIT n+1
            rows = list(qs.filter(id__gt=after).order_by("id")[: limit + 1])
            has_more = len(rows) > limit
            rows = rows[:limit]
            return Response(
                {
                    "results": MessageSerializer(rows, many=True).data,
                    "has_more": has_more,
                    "next_after": rows[-1].id if rows else after,
                }
            )

        if before is not None:
            qs = qs.filter(id__lt=before)
        # WHERE room_id=? AND id < ? ORDER BY id DESC LIMIT n+1  ← 1 件多く取って has_more を判定する
        rows = list(qs.order_by("-id")[: limit + 1])
        has_more = len(rows) > limit
        rows = rows[:limit]
        rows.reverse()  # 古い順にしてから返す（クライアントは配列の先頭に連結するだけで済む）
        return Response(
            {
                "results": MessageSerializer(rows, many=True).data,
                "has_more": has_more,
                "next_before": rows[0].id if rows else None,
            }
        )

    def post(self, request, room_id: int):
        serializer = MessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        msg, created = services.post_message(room_id=room_id, sender=request.user, **serializer.validated_data)
        return Response(
            MessageSerializer(msg).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class MessageDetailView(APIView):
    def _get_own_message(self, request, message_id: int) -> Message:
        msg = get_object_or_404(services.message_queryset(), pk=message_id)
        if get_membership(request.user, msg.room_id) is None:
            raise NotFound("メッセージが見つかりません。")
        if msg.sender_id != request.user.id:
            raise PermissionDenied("自分のメッセージだけを編集・削除できます。")
        return msg

    def patch(self, request, message_id: int):
        msg = self._get_own_message(request, message_id)
        if msg.is_deleted:
            return Response({"detail": "削除済みのメッセージは編集できません。"}, status=status.HTTP_409_CONFLICT)
        serializer = MessageUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.edit_message(msg, serializer.validated_data["body"])
        return Response(MessageSerializer(services.message_queryset().get(pk=msg.pk)).data)

    def delete(self, request, message_id: int):
        msg = self._get_own_message(request, message_id)
        services.delete_message(msg)
        return Response(status=status.HTTP_204_NO_CONTENT)


class SyncView(APIView):
    def get(self, request):
        since = parse_datetime(request.query_params.get("since", ""))
        if since is None or since.tzinfo is None:
            raise ValidationError({"since": "ISO 8601 形式（タイムゾーン付き）で指定してください。"})
        result = services.sync_changes(request.user, since)
        return Response(
            {
                "sync_token": iso(result["sync_token"]),
                "reset": result["reset"],
                "rooms": RoomSerializer(result["rooms"], many=True).data,
                "messages": MessageSerializer(result["messages"], many=True).data,
            }
        )


# ---------------------------------------------------------------------------
# ファイル
# ---------------------------------------------------------------------------


class AttachmentPresignView(RoomMemberAPIView):
    def post(self, request, room_id: int):
        serializer = AttachmentPresignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        v = serializer.validated_data
        key = storage.new_attachment_key(room_id)
        att = Attachment.objects.create(
            room_id=room_id,
            uploaded_by=request.user,
            storage_key=key,
            original_filename=v["filename"],
            content_type=v["content_type"],
            size=v["size"],  # 申告値。メッセージに紐付けるときに S3 の実サイズで上書きする
        )
        upload = storage.presign_upload(key, v["content_type"], settings.ATTACHMENT_MAX_BYTES)
        return Response(
            {"attachment_id": att.id, "upload": upload, "expires_in": settings.PRESIGNED_UPLOAD_EXPIRES},
            status=status.HTTP_201_CREATED,
        )


class AttachmentDownloadView(APIView):
    """
    権限を確認してから、期限付きの署名 URL へ 302 でリダイレクトする。
    同一オリジンなので <img src="/api/attachments/1/download?inline=1"> と書くだけで Cookie が付き、
    ブラウザはリダイレクト先の S3 / MinIO から直接画像を取得する。
    """

    def get(self, request, attachment_id: int):
        att = Attachment.objects.select_related("message").filter(pk=attachment_id).first()
        not_found = NotFound("ファイルが見つかりません。")
        if att is None or get_membership(request.user, att.room_id) is None:
            raise not_found
        if att.message is None:
            if att.uploaded_by_id != request.user.id:  # 投稿前のファイルはアップロードした本人だけ
                raise not_found
        elif att.message.is_deleted:
            raise not_found

        inline = request.query_params.get("inline") == "1" and att.is_image
        url = storage.presign_download(
            att.storage_key, filename=att.original_filename, inline=inline, content_type=att.content_type
        )
        response = HttpResponseRedirect(url)
        response["Cache-Control"] = "private, max-age=60"  # 署名の有効期限（300 秒）より短く
        return response
