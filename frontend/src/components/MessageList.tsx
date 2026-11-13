import { Fragment, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { Message, Room } from "../api/types";
import { dayKey, formatDay } from "../lib/format";
import { useChatActions, useChatState } from "../store/chat";
import { MessageItem, PendingItem } from "./MessageItem";

const GROUP_WINDOW_MS = 5 * 60 * 1000; // 同じ人の 5 分以内の連投は名前とアイコンを省略する

export function MessageList({ room }: { room: Room }) {
  const state = useChatState();
  const actions = useChatActions();
  const rm = state.roomMessages[room.id];
  const scrollRef = useRef<HTMLDivElement>(null);
  const anchorHeight = useRef<number | null>(null); // 過去ログを足す前の高さ（スクロール位置を保つため）
  const stickToBottom = useRef(true);
  const [loadingOlder, setLoadingOlder] = useState(false);

  const messages = (rm?.ids ?? []).map((id) => state.messages[id]).filter((m): m is Message => Boolean(m));
  const pendings = Object.values(state.pending).filter((p) => p.room_id === room.id);
  const lastId = messages.length ? messages[messages.length - 1].id : 0;

  // ---- 最新ページの読み込み（初回、または sync の reset 後）----
  useEffect(() => {
    if (!rm?.loaded) {
      stickToBottom.current = true;
      void actions.loadLatest(room.id);
    }
  }, [actions, room.id, rm?.loaded]);

  // ---- 既読: 画面に見えている状態で、最新のメッセージまで読んだことにする ----
  useEffect(() => {
    if (!lastId || lastId <= room.last_read_message_id) return;
    const mark = () => {
      if (document.visibilityState === "visible") actions.markRead(room.id, lastId);
    };
    const t = setTimeout(mark, 300);
    document.addEventListener("visibilitychange", mark); // 別タブから戻ってきたときにも既読にする
    return () => {
      clearTimeout(t);
      document.removeEventListener("visibilitychange", mark);
    };
  }, [actions, room.id, lastId, room.last_read_message_id]);

  // ---- スクロール: 上端に近づいたら過去ログを読む ----
  function onScroll() {
    const el = scrollRef.current;
    if (!el) return;
    stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (el.scrollTop < 150) {
      const height = el.scrollHeight;
      const p = actions.loadOlder(room.id);
      if (p) {
        anchorHeight.current = height;
        setLoadingOlder(true);
        void p.finally(() => setLoadingOlder(false));
      }
    }
  }

  // DOM 更新の直後・描画の前にスクロール位置を調整する（useEffect だと一瞬ガタつく）
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (anchorHeight.current !== null) {
      // 上に過去ログが増えた分だけ下げ、読んでいた位置を保つ
      el.scrollTop += el.scrollHeight - anchorHeight.current;
      anchorHeight.current = null;
    } else if (stickToBottom.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages.length, pendings.length, lastId]);

  // 画像の読み込みで高さが変わったときも、最下部にいたなら最下部を保つ（load はバブリングしないので capture で拾う）
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onLoad = () => {
      if (stickToBottom.current) el.scrollTop = el.scrollHeight;
    };
    el.addEventListener("load", onLoad, true);
    return () => el.removeEventListener("load", onLoad, true);
  }, []);

  const sendingMine = (m: Message) => m.sender.id === state.me.id;

  return (
    <div className="messages" ref={scrollRef} onScroll={onScroll}>
      <div className="history-edge muted small">
        {!rm?.loaded
          ? "読み込み中…"
          : rm.hasMore
            ? loadingOlder
              ? "過去のメッセージを読み込み中…"
              : " "
            : room.type === "channel"
              ? `#${room.display_name} のはじまり`
              : `${room.display_name} とのやりとりのはじまり`}
      </div>
      {messages.map((m, i) => {
        const prev = messages[i - 1];
        const newDay = !prev || dayKey(prev.created_at) !== dayKey(m.created_at);
        const compact =
          !newDay &&
          prev.sender.id === m.sender.id &&
          !prev.deleted_at &&
          Date.parse(m.created_at) - Date.parse(prev.created_at) < GROUP_WINDOW_MS;
        return (
          <Fragment key={m.id}>
            {newDay && (
              <div className="day-divider">
                <span>{formatDay(m.created_at)}</span>
              </div>
            )}
            <MessageItem message={m} mine={sendingMine(m)} compact={compact} />
          </Fragment>
        );
      })}
      {pendings.map((p) => (
        <PendingItem key={p.client_msg_id} pending={p} />
      ))}
    </div>
  );
}
