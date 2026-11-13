from datetime import timedelta

MAX_BODY_LENGTH = 4000
MAX_ATTACHMENTS_PER_MESSAGE = 10

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100

# /api/sync（docs/03-rest-api.md の 3-5）
SYNC_MAX_MESSAGES = 500  # これを超えたら reset=true を返し、クライアントに取り直してもらう
SYNC_OVERLAP = timedelta(seconds=10)  # タイムスタンプとコミット時刻のずれによる取りこぼしを防ぐ重なり幅

# 添付を消さずに放置された pending の Attachment を掃除するまでの時間
PENDING_ATTACHMENT_TTL = timedelta(hours=24)

IMAGE_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    # SVG は中にスクリプトを書けるので許可しない
}
IMAGE_CONTENT_TYPES = frozenset(IMAGE_EXTENSIONS)

# Content-Type → 許可する拡張子
ALLOWED_ATTACHMENT_TYPES: dict[str, frozenset[str]] = {
    "image/png": frozenset({".png"}),
    "image/jpeg": frozenset({".jpg", ".jpeg"}),
    "image/gif": frozenset({".gif"}),
    "image/webp": frozenset({".webp"}),
    "application/pdf": frozenset({".pdf"}),
    "application/msword": frozenset({".doc"}),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": frozenset({".docx"}),
    "application/vnd.ms-excel": frozenset({".xls"}),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": frozenset({".xlsx"}),
    "application/vnd.ms-powerpoint": frozenset({".ppt"}),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": frozenset({".pptx"}),
    "application/zip": frozenset({".zip"}),
    "application/x-zip-compressed": frozenset({".zip"}),  # Windows のブラウザが送ってくる値
}
