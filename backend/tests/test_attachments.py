import base64
import json
from urllib.parse import parse_qs, urlparse

import pytest

from chat import services
from chat.models import Attachment

pytestmark = pytest.mark.django_db

MB = 1024 * 1024


def presign(client, room, filename="a.pdf", content_type="application/pdf", size=1000):
    return client.post(
        f"/api/rooms/{room.id}/attachments/presign",
        {"filename": filename, "content_type": content_type, "size": size},
        format="json",
    )


def test_presign_returns_post_policy_with_size_limit(api, alice, channel):
    res = presign(api(alice), channel)
    assert res.status_code == 201, res.data
    upload = res.data["upload"]
    assert upload["url"].startswith("http://localhost:9000/")  # ブラウザから見えるホストで署名する
    policy = json.loads(base64.b64decode(upload["fields"]["policy"]))
    assert ["content-length-range", 1, 50 * MB] in policy["conditions"]
    assert {"Content-Type": "application/pdf"} in policy["conditions"]
    att = Attachment.objects.get(pk=res.data["attachment_id"])
    assert att.message is None  # pending
    assert att.storage_key.startswith(f"attachments/{channel.id}/")
    assert "a.pdf" not in att.storage_key  # ユーザーのファイル名はキーに使わない


@pytest.mark.parametrize(
    "filename,content_type,size",
    [
        ("big.pdf", "application/pdf", 50 * MB + 1),  # サイズ超過
        ("x.svg", "image/svg+xml", 10),  # 許可していない形式
        ("x.exe", "application/pdf", 10),  # 拡張子と形式の不一致
    ],
)
def test_presign_validation(api, alice, channel, filename, content_type, size):
    assert presign(api(alice), channel, filename, content_type, size).status_code == 400


def test_presign_strips_path_from_filename(api, alice, channel):
    res = presign(api(alice), channel, filename="C:\\fakepath\\..\\report.pdf")
    assert Attachment.objects.get(pk=res.data["attachment_id"]).original_filename == "report.pdf"


def test_non_member_cannot_presign(api, carol, channel):
    assert presign(api(carol), channel).status_code == 404


def test_message_with_attachment_requires_uploaded_object(api, alice, channel, fake_s3):
    client = api(alice)
    att_id = presign(client, channel, size=1000).data["attachment_id"]
    res = client.post(f"/api/rooms/{channel.id}/messages", {"attachment_ids": [att_id]}, format="json")
    assert res.status_code == 400  # まだ S3 に無い

    att = Attachment.objects.get(pk=att_id)
    fake_s3[att.storage_key] = 1234  # アップロード完了（実サイズは申告と違う）
    res = client.post(f"/api/rooms/{channel.id}/messages", {"attachment_ids": [att_id]}, format="json")
    assert res.status_code == 201, res.data
    assert res.data["attachments"][0]["id"] == att_id
    assert res.data["attachments"][0]["size"] == 1234  # S3 の実サイズで上書きされる
    att.refresh_from_db()
    assert att.message_id == res.data["id"]

    # 同じ添付を別のメッセージに使い回すことはできない
    res = client.post(f"/api/rooms/{channel.id}/messages", {"attachment_ids": [att_id]}, format="json")
    assert res.status_code == 400


def test_cannot_attach_someone_elses_upload(api, alice, bob, channel, fake_s3):
    att_id = presign(api(alice), channel).data["attachment_id"]
    fake_s3[Attachment.objects.get(pk=att_id).storage_key] = 10
    res = api(bob).post(f"/api/rooms/{channel.id}/messages", {"attachment_ids": [att_id]}, format="json")
    assert res.status_code == 400


def _attached(api, user, room, fake_s3, filename="photo.png", content_type="image/png"):
    client = api(user)
    att_id = presign(client, room, filename, content_type).data["attachment_id"]
    fake_s3[Attachment.objects.get(pk=att_id).storage_key] = 10
    msg = client.post(f"/api/rooms/{room.id}/messages", {"attachment_ids": [att_id]}, format="json").data
    return att_id, msg


def test_download_redirects_to_presigned_url(api, alice, bob, channel, fake_s3):
    att_id, msg = _attached(api, alice, channel, fake_s3)
    assert msg["attachments"][0]["preview_url"] == f"/api/attachments/{att_id}/download?inline=1"

    res = api(bob).get(f"/api/attachments/{att_id}/download?inline=1")
    assert res.status_code == 302
    url = urlparse(res["Location"])
    assert url.netloc == "localhost:9000"
    qs = parse_qs(url.query)
    assert qs["response-content-disposition"][0].startswith("inline;")
    assert "X-Amz-Signature" in qs


def test_non_image_is_never_inline(api, alice, channel, fake_s3):
    att_id, _ = _attached(api, alice, channel, fake_s3, "doc.pdf", "application/pdf")
    res = api(alice).get(f"/api/attachments/{att_id}/download?inline=1")
    qs = parse_qs(urlparse(res["Location"]).query)
    assert qs["response-content-disposition"][0].startswith("attachment;")


def test_download_denied_for_non_member_and_deleted_message(api, alice, carol, channel, fake_s3):
    att_id, msg = _attached(api, alice, channel, fake_s3)
    assert api(carol).get(f"/api/attachments/{att_id}/download").status_code == 404

    services.delete_message(services.message_queryset().get(pk=msg["id"]))
    assert api(alice).get(f"/api/attachments/{att_id}/download").status_code == 404


def test_pending_attachment_only_visible_to_uploader(api, alice, bob, channel):
    att_id = presign(api(alice), channel).data["attachment_id"]
    assert api(alice).get(f"/api/attachments/{att_id}/download").status_code == 302
    assert api(bob).get(f"/api/attachments/{att_id}/download").status_code == 404
