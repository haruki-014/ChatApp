// サーバーは UTC（ISO 8601）で返す。表示するときだけ JST に変換する
const TZ = "Asia/Tokyo";

const timeFmt = new Intl.DateTimeFormat("ja-JP", { timeZone: TZ, hour: "2-digit", minute: "2-digit" });
const dayFmt = new Intl.DateTimeFormat("ja-JP", { timeZone: TZ, year: "numeric", month: "long", day: "numeric", weekday: "short" });
const fullFmt = new Intl.DateTimeFormat("ja-JP", { timeZone: TZ, dateStyle: "medium", timeStyle: "medium" });
const dayKeyFmt = new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" });

export const formatTime = (iso: string) => timeFmt.format(new Date(iso));
export const formatDay = (iso: string) => dayFmt.format(new Date(iso));
export const formatFull = (iso: string) => fullFmt.format(new Date(iso));
/** JST での日付（"2026-11-05"）。日付の区切り線を出す判定に使う */
export const dayKey = (iso: string) => dayKeyFmt.format(new Date(iso));

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** crypto.randomUUID は安全なコンテキスト（https / localhost）でしか使えないので代替を用意する */
export function uuid(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}
