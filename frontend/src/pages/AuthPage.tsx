import { useState, type FormEvent } from "react";
import { Link, Navigate } from "react-router";
import { errorMessage } from "../api/client";
import { useAuth } from "../store/auth";

export function AuthPage({ mode }: { mode: "login" | "signup" }) {
  const auth = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (auth.status === "authenticated") return <Navigate to="/" replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "login") await auth.login(email, password);
      else await auth.signup({ email, password, username, display_name: displayName });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth">
      <form className="auth-card" onSubmit={submit}>
        <h1>Pyslack</h1>
        <p className="muted">{mode === "login" ? "ログイン" : "アカウント作成"}</p>
        <label>
          メールアドレス
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus autoComplete="email" />
        </label>
        {mode === "signup" && (
          <>
            <label>
              ユーザー名（半角英小文字・数字・. _ -）
              <input value={username} onChange={(e) => setUsername(e.target.value)} required pattern="[a-z0-9._\-]+" maxLength={30} />
            </label>
            <label>
              表示名
              <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} maxLength={80} />
            </label>
          </>
        )}
        <label>
          パスワード
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoComplete={mode === "login" ? "current-password" : "new-password"}
          />
        </label>
        {error && <p className="error">{error}</p>}
        <button className="primary" disabled={busy}>
          {mode === "login" ? "ログイン" : "登録してはじめる"}
        </button>
        <p className="muted small">
          {mode === "login" ? (
            <>
              アカウントがない場合は <Link to="/signup">新規登録</Link>
            </>
          ) : (
            <>
              登録済みの場合は <Link to="/login">ログイン</Link>
            </>
          )}
        </p>
      </form>
    </div>
  );
}
