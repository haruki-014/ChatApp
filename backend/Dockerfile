# syntax=docker/dockerfile:1

# ---- 共通: 本番用の依存だけを入れる ----
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
# requirements.txt だけを先にコピーする: ソースを変えても pip install のレイヤーキャッシュが効く
COPY requirements.txt .
RUN pip install -r requirements.txt

# ---- 開発: テスト用の依存を追加。ソースは compose の bind mount で渡す ----
FROM base AS dev
COPY requirements-dev.txt .
RUN pip install -r requirements-dev.txt
EXPOSE 8000
CMD ["uvicorn", "config.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---- 本番: ソースをイメージに焼き込み、root 以外のユーザーで動かす ----
FROM base AS prod
RUN useradd --create-home --uid 1000 app
COPY --chown=app:app . .
# 管理画面の静的ファイルを集める（配信は WhiteNoise）。ビルド時だけのダミーの SECRET_KEY を使う
RUN DJANGO_SECRET_KEY=collectstatic-only python manage.py collectstatic --noinput
USER app
EXPOSE 8000
# --proxy-headers: nginx が付ける X-Forwarded-Proto を信頼し、https 判定を正しくする
CMD ["uvicorn", "config.asgi:application", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
