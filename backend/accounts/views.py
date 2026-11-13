import uuid

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.db.models import Q
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from chat import events, storage
from chat.constants import IMAGE_EXTENSIONS

from . import presence
from .authentication import CsrfEnforcedMixin
from .models import User
from .serializers import (
    AvatarPresignSerializer,
    LoginSerializer,
    MeSerializer,
    ProfileUpdateSerializer,
    SignupSerializer,
    UserSummarySerializer,
)

# ---------------------------------------------------------------------------
# 認証
# ---------------------------------------------------------------------------


@ensure_csrf_cookie
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def csrf_view(request):
    """SPA の起動時に呼び、csrftoken Cookie を受け取る。"""
    return Response({"detail": "ok"})


class SignupView(CsrfEnforcedMixin, APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        return Response(MeSerializer(user).data, status=status.HTTP_201_CREATED)


class LoginView(CsrfEnforcedMixin, APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(
            request,
            email=serializer.validated_data["email"],
            password=serializer.validated_data["password"],
        )
        if user is None:
            return Response(
                {"detail": "メールアドレスまたはパスワードが正しくありません。"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        login(request, user)  # セッション ID を作り直す（セッション固定攻撃対策）
        return Response(MeSerializer(user).data)


class LogoutView(APIView):
    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    def get(self, request):
        return Response(MeSerializer(request.user).data)


# ---------------------------------------------------------------------------
# ユーザー
# ---------------------------------------------------------------------------


class UserListView(APIView):
    def get(self, request):
        q = request.query_params.get("q", "").strip()
        try:
            limit = max(1, min(int(request.query_params.get("limit", 20)), 50))
        except ValueError:
            limit = 20
        users = User.objects.filter(is_active=True)
        if q:
            users = users.filter(Q(username__icontains=q) | Q(display_name__icontains=q))
        users = users.order_by("username")[:limit]
        return Response(UserSummarySerializer(users, many=True).data)


class UserDetailView(APIView):
    def get(self, request, user_id: int):
        user = get_object_or_404(User, pk=user_id, is_active=True)
        return Response(UserSummarySerializer(user).data)


class MyProfileView(APIView):
    def patch(self, request):
        serializer = ProfileUpdateSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        data = MeSerializer(user).data
        summary = {k: v for k, v in data.items() if k != "email"}
        events.broadcast_presence_group("user.updated", {"user": summary})
        return Response(data)


class AvatarPresignView(APIView):
    def post(self, request):
        serializer = AvatarPresignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        content_type = serializer.validated_data["content_type"]
        ext = IMAGE_EXTENSIONS[content_type]
        key = f"avatars/{request.user.id}/{uuid.uuid4().hex}{ext}"
        upload = storage.presign_upload(key, content_type, settings.AVATAR_MAX_BYTES)
        return Response(
            {"avatar_key": key, "upload": upload, "expires_in": settings.PRESIGNED_UPLOAD_EXPIRES},
            status=status.HTTP_201_CREATED,
        )


class AvatarView(APIView):
    def get(self, request, user_id: int):
        user = get_object_or_404(User, pk=user_id)
        if not user.avatar_key:
            return Response(status=status.HTTP_404_NOT_FOUND)
        url = storage.presign_download(user.avatar_key, filename="avatar", inline=True)
        response = HttpResponseRedirect(url)
        response["Cache-Control"] = "private, max-age=60"
        return response


class PresenceView(APIView):
    def get(self, request):
        return Response({"online_user_ids": sorted(presence.online_user_ids())})
