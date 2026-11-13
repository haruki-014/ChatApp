from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

User = settings.AUTH_USER_MODEL


class Room(models.Model):
    """DM・グループDM・チャンネルを1テーブルで扱う。"""

    class Type(models.TextChoices):
        DM = "dm", "Direct message"  # 2人。名前なし。常に非公開
        GROUP = "group", "Group DM"  # 3人以上。名前は任意。常に非公開
        CHANNEL = "channel", "Channel"  # 名前必須。公開/非公開を選べる

    type = models.CharField(max_length=10, choices=Type.choices)
    name = models.CharField(max_length=80, null=True, blank=True)
    is_private = models.BooleanField(default=True)
    # DM の重複防止用キー "{小さいuser_id}:{大きいuser_id}"。DM 以外は NULL
    dm_key = models.CharField(max_length=41, null=True, blank=True, editable=False)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_rooms")
    members = models.ManyToManyField(User, through="RoomMember", related_name="rooms")
    # ルーム一覧の並び替え用の非正規化列。メッセージ投稿と同じトランザクションで更新する
    last_message_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # 同じ2人の DM は1つだけ（DB で保証するので、同時に作成されても重複しない）
            models.UniqueConstraint(
                fields=["dm_key"], condition=Q(type="dm"), name="uniq_room_dm_key"
            ),
            # チャンネル名は大文字小文字を区別せずにユニーク（Slack と同じ）
            models.UniqueConstraint(
                Lower("name"), condition=Q(type="channel"), name="uniq_room_channel_name"
            ),
            # type ごとの列の組み合わせを DB で強制する
            models.CheckConstraint(
                condition=(
                    Q(type="dm", dm_key__isnull=False, name__isnull=True, is_private=True)
                    | Q(type="group", dm_key__isnull=True, is_private=True)
                    | Q(type="channel", dm_key__isnull=True, name__isnull=False)
                ),
                name="room_type_consistency",
            ),
        ]

    @staticmethod
    def make_dm_key(user_a_id: int, user_b_id: int) -> str:
        low, high = sorted((user_a_id, user_b_id))
        return f"{low}:{high}"

    def __str__(self) -> str:
        return f"{self.type}:{self.name or self.dm_key or self.pk}"


class RoomMember(models.Model):
    """User と Room の多対多の中間テーブル。既読位置もここに持つ。"""

    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"

    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER)
    # FK ではなく値として持つ。0 = 何も読んでいない。
    # 参加時には「その時点の最新 message_id」を入れ、過去ログが全部未読になるのを防ぐ
    last_read_message_id = models.BigIntegerField(default=0)
    is_muted = models.BooleanField(default=False)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # (room, user) の複合インデックスも兼ねる。「このルームのメンバー一覧」に使う
            models.UniqueConstraint(fields=["room", "user"], name="uniq_room_member"),
        ]
        indexes = [
            # 「自分が参加しているルーム一覧」は user 起点で引くので、逆順の複合インデックスも作る
            models.Index(fields=["user", "room"], name="idx_member_user_room"),
        ]


class Message(models.Model):
    # id はカーソル（?before= / ?after=）として使う。単調増加かつ一意なので created_at より向いている
    id = models.BigAutoField(primary_key=True)
    # 単独の room_id インデックスは下の (room, id) 複合インデックスで代用できるので作らない
    room = models.ForeignKey(
        Room, on_delete=models.CASCADE, related_name="messages", db_index=False
    )
    # ユーザーは削除せず is_active=False で無効化する前提。履歴を守るため PROTECT
    sender = models.ForeignKey(User, on_delete=models.PROTECT, related_name="messages")
    body = models.TextField(blank=True)  # 添付のみのメッセージでは空になる。最大長は API 側で検証する
    # クライアントが生成する UUID（冪等キー）。送信リトライでの二重投稿防止と、楽観的 UI の突き合わせに使う
    client_msg_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)  # 再接続時に「切断中の編集・削除」を拾うためのカーソル
    edited_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)  # 論理削除。削除時に body も空にする

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["sender", "client_msg_id"],
                condition=Q(client_msg_id__isnull=False),
                name="uniq_message_client_msg_id",
            ),
        ]
        indexes = [
            # 履歴取得: WHERE room_id=? AND id < ? ORDER BY id DESC LIMIT 50
            # 未読数:   WHERE room_id=? AND id > last_read
            models.Index(fields=["room", "id"], name="idx_message_room_id"),
            # 再同期:   WHERE room_id=? AND updated_at > ?
            models.Index(fields=["room", "updated_at"], name="idx_message_room_updated"),
        ]

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class Attachment(models.Model):
    """
    ライフサイクル:
      1. presign 時に message=NULL（pending）で作成
      2. ブラウザが S3/MinIO に直接 PUT
      3. メッセージ投稿時に message を紐付ける（S3 の HEAD で実在とサイズを確認する）
      4. 一定時間たっても NULL のものは孤児として定期的に削除する
    """

    # 権限チェック（メンバーか）を message 紐付け前でも行えるよう room を直接持つ
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="attachments")
    message = models.ForeignKey(
        Message, on_delete=models.CASCADE, null=True, blank=True, related_name="attachments"
    )
    uploaded_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="attachments")
    # 例: "attachments/42/3f2b...e9"（uuid4）。ユーザーが付けたファイル名はキーに使わない
    storage_key = models.CharField(max_length=255, unique=True)
    original_filename = models.CharField(max_length=255)  # ダウンロード時の Content-Disposition に使う
    content_type = models.CharField(max_length=100)
    size = models.PositiveBigIntegerField()  # バイト数
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            # 孤児を掃除するバッチ用の部分インデックス
            models.Index(
                fields=["created_at"],
                condition=Q(message__isnull=True),
                name="idx_attachment_pending",
            ),
        ]

    @property
    def is_image(self) -> bool:
        return self.content_type.startswith("image/")
