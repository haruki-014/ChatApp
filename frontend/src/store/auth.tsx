import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { ApiError, setUnauthorizedHandler } from "../api/client";
import { Api } from "../api/endpoints";
import type { Me } from "../api/types";

type AuthState =
  | { status: "loading"; me: null }
  | { status: "anonymous"; me: null }
  | { status: "authenticated"; me: Me };

type AuthContextValue = AuthState & {
  login: (email: string, password: string) => Promise<void>;
  signup: (body: { email: string; username: string; password: string; display_name: string }) => Promise<void>;
  logout: () => Promise<void>;
  setMe: (me: Me) => void;
  /** 401 や WS の 4401 を受けたとき（セッション切れ）にログイン画面へ戻す */
  markLoggedOut: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading", me: null });

  const markLoggedOut = useCallback(() => setState({ status: "anonymous", me: null }), []);

  useEffect(() => {
    setUnauthorizedHandler(markLoggedOut);
    (async () => {
      await Api.csrf(); // csrftoken Cookie をもらっておく（ログインの POST に必要）
      try {
        setState({ status: "authenticated", me: await Api.me() });
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) markLoggedOut();
        else throw e;
      }
    })();
    return () => setUnauthorizedHandler(null);
  }, [markLoggedOut]);

  const value = useMemo<AuthContextValue>(
    () => ({
      ...state,
      login: async (email, password) => setState({ status: "authenticated", me: await Api.login(email, password) }),
      signup: async (body) => setState({ status: "authenticated", me: await Api.signup(body) }),
      logout: async () => {
        await Api.logout().catch(() => undefined);
        markLoggedOut();
      },
      setMe: (me) => setState({ status: "authenticated", me }),
      markLoggedOut,
    }),
    [state, markLoggedOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

/** ログイン済みの画面でだけ使う（me が必ずある） */
export function useMe(): Me {
  const { me } = useAuth();
  if (!me) throw new Error("not authenticated");
  return me;
}
