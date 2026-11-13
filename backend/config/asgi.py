"""
ASGI のエントリポイント（Uvicorn が読み込む）。

  HTTP      → Django（DRF の View）
  WebSocket → Origin 検証 → セッション認証 → ルーティング → ChatConsumer
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# 先に Django を初期化する（アプリのレジストリを用意してからでないと、モデルを import する Consumer を読み込めない）
django_asgi_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402

from chat.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AllowedHostsOriginValidator(  # ① Origin ヘッダが ALLOWED_HOSTS に含まれるか（CSRF の代わり）
            AuthMiddlewareStack(  # ② Cookie のセッションを読んで scope["user"] を設定する
                URLRouter(websocket_urlpatterns)  # ③ パスで Consumer を選ぶ（urls.py の WebSocket 版）
            )
        ),
    }
)
