"""WebSocket 版の urls.py。config/asgi.py の URLRouter から読み込まれる。"""

from django.urls import path

from .consumers import ChatConsumer

websocket_urlpatterns = [
    path("ws/", ChatConsumer.as_asgi()),
]
