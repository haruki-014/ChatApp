from django.contrib.auth import password_validation
from rest_framework import serializers

from chat import storage
from chat.constants import IMAGE_CONTENT_TYPES

from . import presence
from .models import User, normalize_email


def online_ids_from_context(context: dict) -> set[int]:
    """
    is_online のために Redis を引くのは 1 リクエストにつき 1 回だけにする（N+1 の Redis 版を避ける）。
    ネストした Serializer も root と同じ context の dict を共有するので、ここに結果を置いておく。
    """
    if "online_ids" not in context:
        context["online_ids"] = presence.online_user_ids()
    return context["online_ids"]


class UserSummarySerializer(serializers.ModelSerializer):
    display_name = serializers.SerializerMethodField()
    avatar_url = serializers.SerializerMethodField()
    is_online = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "display_name", "avatar_url", "status_emoji", "status_text", "is_online"]

    def get_display_name(self, user: User) -> str:
        return user.name_for_display

    def get_avatar_url(self, user: User) -> str | None:
        if not user.avatar_key:
            return None
        # 画像を差し替えたときにブラウザのキャッシュが効き続けないよう、更新時刻をクエリに付ける
        return f"/api/users/{user.id}/avatar?v={int(user.updated_at.timestamp())}"

    def get_is_online(self, user: User) -> bool:
        return user.id in online_ids_from_context(self.context)


class MeSerializer(UserSummarySerializer):
    class Meta(UserSummarySerializer.Meta):
        fields = UserSummarySerializer.Meta.fields + ["email"]


class SignupSerializer(serializers.Serializer):
    email = serializers.EmailField()
    username = serializers.CharField(max_length=30)
    password = serializers.CharField(write_only=True, style={"input_type": "password"})
    display_name = serializers.CharField(max_length=80, required=False, allow_blank=True)

    def validate_email(self, value: str) -> str:
        # 小文字にそろえてから重複を確認する（ModelSerializer 標準の UniqueValidator は正規化前の値で調べてしまう）
        value = normalize_email(value)
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("このメールアドレスは既に登録されています。")
        return value

    def validate_username(self, value: str) -> str:
        field = User._meta.get_field("username")
        for validator in field.validators:
            try:
                validator(value)
            except Exception as e:  # django.core.exceptions.ValidationError
                raise serializers.ValidationError(list(getattr(e, "messages", [str(e)]))) from e
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("このユーザー名は既に使われています。")
        return value

    def validate(self, attrs):
        candidate = User(email=attrs["email"], username=attrs["username"])
        try:
            password_validation.validate_password(attrs["password"], user=candidate)
        except Exception as e:
            raise serializers.ValidationError({"password": list(getattr(e, "messages", [str(e)]))}) from e
        return attrs

    def create(self, validated_data):
        return User.objects.create_user(
            username=validated_data["username"],
            email=validated_data["email"],
            password=validated_data["password"],
            display_name=validated_data.get("display_name", ""),
        )


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, style={"input_type": "password"})


class ProfileUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["display_name", "status_emoji", "status_text", "avatar_key"]

    def validate_avatar_key(self, value: str) -> str:
        if not value:
            return ""  # 空文字 = アバターを外す
        user = self.instance
        # 他人のファイルや添付ファイルのキーを自分のアバターにできないよう、プレフィックスで縛る
        if not value.startswith(f"avatars/{user.id}/"):
            raise serializers.ValidationError("不正なキーです。")
        if storage.head_object(value) is None:
            raise serializers.ValidationError("アップロードが完了していません。")
        return value


class AvatarPresignSerializer(serializers.Serializer):
    content_type = serializers.ChoiceField(choices=sorted(IMAGE_CONTENT_TYPES))
    size = serializers.IntegerField(min_value=1)

    def validate_size(self, value: int) -> int:
        from django.conf import settings

        if value > settings.AVATAR_MAX_BYTES:
            raise serializers.ValidationError("アバター画像は 5MB までです。")
        return value
