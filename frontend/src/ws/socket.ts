import type { WsEvent } from "../api/types";

/**
 * WebSocket 接続の管理（docs/04-websocket-events.md の 4-3, 4-8）。
 *
 * - 接続先は同一オリジンの /ws/。Cookie（sessionid）はハンドシェイクで自動的に送られる
 * - 25 秒ごとに ping を送る（サーバーはプレゼンスの期限を延長し、nginx / ALB のアイドル切断も防ぐ）
 * - 切れたら 1s, 2s, 4s … 最大 30s ＋ 揺らぎ（ジッター）で再接続する
 *   （サーバー再起動時に全員が同じ瞬間に再接続して負荷が集中するのを防ぐ）
 * - close code 4401（未認証）では再接続しない
 * - hello を受け取ったら onHello を呼ぶ。呼び出し側はここで /api/sync を叩いて取りこぼしを埋める
 */

export type ConnectionStatus = "connecting" | "open" | "closed";

type Handlers = {
  onEvent: (event: WsEvent) => void;
  onHello: () => void;
  onStatus: (status: ConnectionStatus) => void;
  onUnauthorized: () => void;
};

const PING_INTERVAL_MS = 25_000;
const MAX_BACKOFF_MS = 30_000;
const CLOSE_UNAUTHENTICATED = 4401;

export class ChatSocket {
  private ws: WebSocket | null = null;
  private attempt = 0;
  private stopped = true;
  private pingTimer: ReturnType<typeof setInterval> | undefined;
  private retryTimer: ReturnType<typeof setTimeout> | undefined;
  private readonly handlers: Handlers;

  constructor(handlers: Handlers) {
    this.handlers = handlers;
  }

  start() {
    this.stopped = false;
    window.addEventListener("online", this.reconnectNow);
    this.open();
  }

  stop() {
    this.stopped = true;
    window.removeEventListener("online", this.reconnectNow);
    clearInterval(this.pingTimer);
    clearTimeout(this.retryTimer);
    this.ws?.close(1000);
    this.ws = null;
  }

  /** OS のネットワークが復帰したら、バックオフを待たずにすぐ再接続する */
  private reconnectNow = () => {
    if (this.stopped || this.ws?.readyState === WebSocket.OPEN) return;
    clearTimeout(this.retryTimer);
    this.attempt = 0;
    this.open();
  };

  private open() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/`);
    this.ws = ws;
    this.handlers.onStatus("connecting");

    ws.onopen = () => {
      this.attempt = 0;
      this.handlers.onStatus("open");
      clearInterval(this.pingTimer);
      this.pingTimer = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "ping" }));
      }, PING_INTERVAL_MS);
    };

    ws.onmessage = (msg) => {
      let event: WsEvent;
      try {
        event = JSON.parse(msg.data);
      } catch {
        return;
      }
      if (event.type === "pong") return;
      if (event.type === "hello") this.handlers.onHello();
      this.handlers.onEvent(event);
    };

    ws.onclose = (e) => {
      if (this.ws !== ws) return; // stop() 後や、すでに新しい接続に切り替わった後の古い close
      clearInterval(this.pingTimer);
      this.handlers.onStatus("closed");
      if (e.code === CLOSE_UNAUTHENTICATED) {
        this.handlers.onUnauthorized();
        return;
      }
      if (this.stopped) return;
      const base = Math.min(MAX_BACKOFF_MS, 1000 * 2 ** this.attempt);
      const delay = base * (0.8 + Math.random() * 0.4); // ±20% のジッター
      this.attempt += 1;
      this.retryTimer = setTimeout(() => this.open(), delay);
    };
  }
}
