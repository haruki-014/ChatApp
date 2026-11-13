from django.conf import settings
from django.contrib import admin
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("accounts.urls")),
    path("api/", include("chat.urls")),
]

if settings.DEBUG:
    # 開発時（Uvicorn）でも管理画面の CSS / JS を配信する。本番は WhiteNoise が担当する
    urlpatterns += staticfiles_urlpatterns()
