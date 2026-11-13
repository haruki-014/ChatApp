/**
 * fetch の薄いラッパー。
 * - Cookie（sessionid）は同一オリジンなので credentials: "same-origin" で自動的に送られる
 * - 書き込み系では csrftoken Cookie の値を X-CSRFToken ヘッダに入れる（Django の CSRF 対策）
 * - 401 を受けたら、登録されたハンドラ（ログイン画面へ戻す）を呼ぶ
 */

export class ApiError extends Error {
  constructor(
    public status: number,
    public data: unknown,
  ) {
    super(`API error ${status}`);
  }
}

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn;
}

function getCookie(name: string): string | null {
  const match = document.cookie.split("; ").find((row) => row.startsWith(`${name}=`));
  return match ? decodeURIComponent(match.split("=")[1]) : null;
}

type Options = {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  query?: Record<string, string | number | undefined>;
};

export async function request<T>(path: string, { method = "GET", body, query }: Options = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") {
    const token = getCookie("csrftoken");
    if (token) headers["X-CSRFToken"] = token;
  }

  let url = path;
  if (query) {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) if (v !== undefined) params.set(k, String(v));
    const qs = params.toString();
    if (qs) url += `?${qs}`;
  }

  const res = await fetch(url, {
    method,
    headers,
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    if (res.status === 401 && onUnauthorized) onUnauthorized();
    throw new ApiError(res.status, data);
  }
  return data as T;
}

/** DRF のエラー（{"detail": "..."} や {"field": ["..."]}）を 1 つの文字列にする */
export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    const data = e.data as Record<string, unknown> | null;
    if (data && typeof data === "object") {
      const parts = Object.entries(data).flatMap(([key, v]) => {
        const msgs = Array.isArray(v) ? v.map(String) : typeof v === "object" ? Object.values(v ?? {}).map(String) : [String(v)];
        return key === "detail" || key === "non_field_errors" ? msgs : msgs.map((m) => `${key}: ${m}`);
      });
      if (parts.length) return parts.join(" / ");
    }
    return `エラーが発生しました（${e.status}）`;
  }
  return e instanceof Error ? e.message : String(e);
}
