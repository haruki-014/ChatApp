from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import UserManager as DjangoUserManager
from django.core.validators import RegexValidator
from django.db import models


def normalize_email(email: str) -> str:
    """ログインIDとして使うので、前後の空白を除いて全体を小文字にそろえる。"""
    return email.strip().lower()


class UserManager(DjangoUserManager):
    def get_by_natural_key(self, email):
        # ModelBackend（ログイン処理）が呼ぶ。"Foo@Example.com" でもログインできるようにする
        return self.get(email=normalize_email(email))


class User(AbstractUser):
    """
    AbstractUser を継承したカスタムユーザー。
    - ログインIDは email（USERNAME_FIELD）
    - AbstractUser の username を Slack の @handle（要件の user_name）として使う
    - 作成日時は AbstractUser.date_joined を使う（created_at は重複するので持たない）
    - オンライン/オフラインは DB に持たず Redis で管理する
    """

    # 使わない列は None で消す（管理画面は Step 6 で専用のものを用意する）
    first_name = None
    last_name = None

    username = models.CharField(
        "user name",
        max_length=30,
        unique=True,
        validators=[
            RegexValidator(
                r"^[a-z0-9._-]+$",
                "半角英小文字・数字・. _ - のみ使用できます。",
            )
        ],
        error_messages={"unique": "このユーザー名は既に使われています。"},
    )
    email = models.EmailField("email", unique=True)
    display_name = models.CharField(max_length=80, blank=True)
    avatar_key = models.CharField(max_length=255, blank=True)  # S3 のキー（URL は保存しない）
    status_emoji = models.CharField(max_length=64, blank=True)  # 絵文字（ZWJ 結合を考慮して長め）
    status_text = models.CharField(max_length=100, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]  # createsuperuser で email の次に聞かれる

    objects = UserManager()

    def save(self, *args, **kwargs):
        if self.email:
            self.email = normalize_email(self.email)
        super().save(*args, **kwargs)

    @property
    def name_for_display(self) -> str:
        return self.display_name or self.username

    def __str__(self) -> str:
        return f"@{self.username}"
