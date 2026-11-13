import pytest

from tests.conftest import PASSWORD

pytestmark = pytest.mark.django_db


def test_csrf_endpoint_sets_cookie(api):
    res = api().get("/api/auth/csrf")
    assert res.status_code == 200
    assert "csrftoken" in res.cookies


def test_signup_logs_in_and_normalizes_email(api):
    client = api()
    res = client.post(
        "/api/auth/signup",
        {"email": "New.User@Example.COM", "username": "newuser", "password": PASSWORD, "display_name": "New"},
        format="json",
    )
    assert res.status_code == 201, res.data
    assert res.data["email"] == "new.user@example.com"
    assert client.get("/api/auth/me").status_code == 200  # そのままログイン状態


def test_signup_rejects_duplicate_email_case_insensitively(api, alice):
    res = api().post(
        "/api/auth/signup",
        {"email": "ALICE@example.com", "username": "alice2", "password": PASSWORD},
        format="json",
    )
    assert res.status_code == 400
    assert "email" in res.data


def test_signup_rejects_invalid_username(api):
    res = api().post(
        "/api/auth/signup", {"email": "x@example.com", "username": "Bad Name", "password": PASSWORD}, format="json"
    )
    assert res.status_code == 400
    assert "username" in res.data


def test_login_is_case_insensitive_and_wrong_password_fails(api, alice):
    client = api()
    assert client.post("/api/auth/login", {"email": "alice@example.com", "password": "nope"}, format="json").status_code == 400
    res = client.post("/api/auth/login", {"email": "Alice@Example.com", "password": PASSWORD}, format="json")
    assert res.status_code == 200
    assert res.data["username"] == "alice"


def test_login_requires_csrf_token(api, alice):
    """未ログインのリクエストでも CSRF を要求する（ログイン CSRF 対策）。"""
    client = api(enforce_csrf=True)
    res = client.post("/api/auth/login", {"email": "alice@example.com", "password": PASSWORD}, format="json")
    assert res.status_code == 403

    token = client.get("/api/auth/csrf").cookies["csrftoken"].value
    res = client.post(
        "/api/auth/login",
        {"email": "alice@example.com", "password": PASSWORD},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert res.status_code == 200


def test_unauthenticated_returns_401(api):
    res = api().get("/api/auth/me")
    assert res.status_code == 401
    assert res.headers["WWW-Authenticate"]


def test_logout(api, alice):
    client = api(alice)
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_profile_update_broadcasts_user_updated(api, alice, sent, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        res = api(alice).patch("/api/users/me", {"status_emoji": "🍵", "status_text": "休憩中"}, format="json")
    assert res.status_code == 200
    assert res.data["status_text"] == "休憩中"
    group, message = sent[-1]
    assert group == "presence"
    assert message["event"]["type"] == "user.updated"
    assert message["event"]["data"]["user"]["status_emoji"] == "🍵"


def test_avatar_key_must_be_own_prefix(api, alice, bob, fake_s3):
    fake_s3[f"avatars/{bob.id}/x.png"] = 100
    res = api(alice).patch("/api/users/me", {"avatar_key": f"avatars/{bob.id}/x.png"}, format="json")
    assert res.status_code == 400

    fake_s3[f"avatars/{alice.id}/y.png"] = 100
    res = api(alice).patch("/api/users/me", {"avatar_key": f"avatars/{alice.id}/y.png"}, format="json")
    assert res.status_code == 200
    assert res.data["avatar_url"].startswith(f"/api/users/{alice.id}/avatar")


def test_user_search(api, alice, bob, carol):
    res = api(alice).get("/api/users", {"q": "bo"})
    assert [u["username"] for u in res.data] == ["bob"]
