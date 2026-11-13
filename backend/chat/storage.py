"""
S3 / MinIO の操作。Django はファイル本体を扱わず、「署名」と「確認（HEAD）」と「削除」だけを行う。

boto3 のクライアントを 2 つ使い分ける（docs/05-docker.md の 5-2 ①）:
  internal: backend コンテナ → MinIO（http://minio:9000）。HEAD / DELETE のように実際に通信する操作
  public  : ブラウザ → MinIO（http://localhost:9000）の URL に署名するだけ（署名はローカル計算で通信しない）
SigV4 の署名には Host が含まれるので、internal で作った URL をあとから書き換えることはできない。
本番ではどちらのエンドポイントも空になり、本物の S3 を使う。
"""

import logging
import os
import uuid
from functools import lru_cache
from urllib.parse import quote

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from django.conf import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=None)
def _client(public: bool):
    endpoint = (settings.AWS_S3_PUBLIC_ENDPOINT_URL if public else "") or settings.AWS_S3_ENDPOINT_URL
    kwargs = {
        "region_name": settings.AWS_S3_REGION_NAME,
        # MinIO は http://host:9000/<bucket>/<key> のパス形式でアクセスする
        "config": Config(signature_version="s3v4", s3={"addressing_style": "path" if endpoint else "auto"}),
    }
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    if settings.AWS_ACCESS_KEY_ID:  # 本番では空にして IAM ロール（インスタンスプロファイル）を使う
        kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
        kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
    return boto3.client("s3", **kwargs)


def new_attachment_key(room_id: int) -> str:
    # ユーザーが付けたファイル名はキーに使わない（文字コードやパス区切りの問題を避ける）
    return f"attachments/{room_id}/{uuid.uuid4().hex}"


def presign_upload(key: str, content_type: str, max_bytes: int) -> dict:
    """
    ブラウザから直接アップロードするための署名付き POST を作る。
    PUT 用の署名と違い、POST ポリシーには content-length-range 条件を書けるので、
    上限を超えるファイルは S3 自体が拒否する。
    戻り値: {"url": "...", "fields": {...}}（fields をそのまま FormData に入れ、最後に file を足す）
    """
    return _client(public=True).generate_presigned_post(
        Bucket=settings.AWS_STORAGE_BUCKET_NAME,
        Key=key,
        Fields={"Content-Type": content_type},
        Conditions=[
            {"Content-Type": content_type},  # 申告と違う Content-Type では上げられない
            ["content-length-range", 1, max_bytes],
        ],
        ExpiresIn=settings.PRESIGNED_UPLOAD_EXPIRES,
    )


def _content_disposition(filename: str, inline: bool) -> str:
    kind = "inline" if inline else "attachment"
    # 古いクライアント向けの ASCII 名。日本語だけのファイル名なら "download.png" のようにする
    stem, ext = os.path.splitext(filename)
    ascii_stem = stem.encode("ascii", "ignore").decode().replace('"', "").strip() or "download"
    ascii_fallback = ascii_stem + ext.encode("ascii", "ignore").decode()
    # filename* (RFC 5987) で日本語のファイル名を正しく伝える
    return f"{kind}; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"


def presign_download(key: str, *, filename: str, inline: bool = False, content_type: str | None = None) -> str:
    params = {
        "Bucket": settings.AWS_STORAGE_BUCKET_NAME,
        "Key": key,
        "ResponseContentDisposition": _content_disposition(filename, inline),
    }
    if content_type:
        params["ResponseContentType"] = content_type
    return _client(public=True).generate_presigned_url(
        "get_object", Params=params, ExpiresIn=settings.PRESIGNED_DOWNLOAD_EXPIRES
    )


def head_object(key: str) -> int | None:
    """オブジェクトが実在すればサイズ（バイト）を、無ければ None を返す。"""
    try:
        res = _client(public=False).head_object(Bucket=settings.AWS_STORAGE_BUCKET_NAME, Key=key)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return None
        raise
    return int(res["ContentLength"])


def delete_object(key: str) -> None:
    try:
        _client(public=False).delete_object(Bucket=settings.AWS_STORAGE_BUCKET_NAME, Key=key)
    except Exception:  # 削除は後片付けなので、失敗してもリクエスト自体は失敗させない
        logger.exception("failed to delete s3 object %s", key)
