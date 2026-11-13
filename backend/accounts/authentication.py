from rest_framework.authentication import SessionAuthentication


class SessionAuthentication401(SessionAuthentication):
    """
    DRF の SessionAuthentication は、未ログイン時に 403 を返す
    （WWW-Authenticate ヘッダに入れる値が無いため、DRF が 401 を使えない）。
    値を返すようにして 401 にし、フロントが「ログインが必要」と「権限が無い」を区別できるようにする。
    """

    def authenticate_header(self, request):
        return 'Session realm="api"'


class CsrfEnforcedMixin:
    """
    DRF は「セッションでログイン済みのユーザー」にしか CSRF チェックをかけない。
    ログイン・サインアップは未ログインで呼ばれるので、明示的にチェックする（ログイン CSRF 対策）。
    """

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        SessionAuthentication401().enforce_csrf(request)
