"""
認可: 「ルームのメンバーか」。REST（Permission クラス）と WebSocket（Consumer）で同じ関数を使う。
Spring Security の @PreAuthorize("@roomGuard.isMember(#roomId)") に相当する。
"""

from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import BasePermission

from .models import RoomMember


def get_membership(user, room_id) -> RoomMember | None:
    if not user.is_authenticated:
        return None
    return RoomMember.objects.select_related("room").filter(room_id=room_id, user=user).first()


def is_member(user_id: int, room_id: int) -> bool:
    return RoomMember.objects.filter(room_id=room_id, user_id=user_id).exists()


class IsRoomMember(BasePermission):
    """
    URL の room_id のメンバーでなければ 404。
    403 を返すと「その id の非公開ルームが存在する」ことが分かってしまうため。
    通ったら view.membership に RoomMember（room 付き）を置いておく。
    """

    def has_permission(self, request, view) -> bool:
        membership = get_membership(request.user, view.kwargs.get("room_id"))
        if membership is None:
            raise NotFound("ルームが見つかりません。")
        view.membership = membership
        return True


def require_role(membership: RoomMember, *roles: str) -> None:
    if membership.role not in roles:
        raise PermissionDenied("この操作を行う権限がありません。")
