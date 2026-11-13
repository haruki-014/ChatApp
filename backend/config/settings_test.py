"""pytest 用の設定。pytest.ini から読み込まれる。"""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-secret-key")

from .settings import *  # noqa: E402,F403
from .settings import REDIS_URL  # noqa: E402

DEBUG = False
ALLOWED_HOSTS = ["localhost", "testserver"]
MIDDLEWARE = [m for m in MIDDLEWARE if "whitenoise" not in m]  # noqa: F405（collectstatic 前なので不要）

# Consumer のテストでは database_sync_to_async のスレッドから DB に接続する。
# 接続を使い回すとテスト DB の削除時に「使用中」になるので、毎回閉じる
DATABASES["default"]["CONN_MAX_AGE"] = 0  # noqa: F405

# channel layer はテストプロセス内で完結するインメモリ版を使う（Redis の状態に左右されない）
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

# プレゼンスは Lua スクリプトを使うので本物の Redis を使う。開発データと混ざらないよう DB 15 に分ける
REDIS_URL = os.environ.get("REDIS_URL_TEST") or REDIS_URL.rsplit("/", 1)[0] + "/15"

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # テストを速くする

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

AWS_ACCESS_KEY_ID = "test"
AWS_SECRET_ACCESS_KEY = "test"
AWS_S3_ENDPOINT_URL = "http://minio:9000"
AWS_S3_PUBLIC_ENDPOINT_URL = "http://localhost:9000"
