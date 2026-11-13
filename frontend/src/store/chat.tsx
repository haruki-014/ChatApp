import { createContext, useContext, useEffect, useMemo, useReducer, useRef, type ReactNode } from "react";
import { errorMessage } from "../api/client";
import { Api } from "../api/endpoints";
import type { Me, Room } from "../api/types";
import { uuid } from "../lib/format";
import { ChatSocket } from "../ws/socket";
import { useAuth, useMe } from "./auth";
import { initialState, reducer, type ChatState, type PendingMessage } from "./chatReducer";

type ChatActions = {
  setActive: (roomId: number | null) => void;
  loadLatest: (roomId: number) => Promise<void>;
  /** 古いメッセージを読み込む。すでに読み込み中・これ以上無い場合は null */
  loadOlder: (roomId: number) => Promise<void> | null;
  send: (roomId: number, body: string, attachments: { id: number; name: string }[]) => void;
  retry: (clientMsgId: string) => void;
  edit: (messageId: number, body: string) => Promise<void>;
  remove: (messageId: number) => Promise<void>;
  markRead: (roomId: number, messageId: number) => void;
  upsertRoom: (room: Room) => void;
  updateMe: (me: Me) => void;
};

const StateContext = createContext<ChatState | null>(null);
const ActionsContext = createContext<ChatActions | null>(null);

export function ChatProvider({ children }: { children: ReactNode }) {
  const me = useMe();
  const { markLoggedOut, setMe } = useAuth();
  const [state, dispatch] = useReducer(reducer, me, initialState);

  // 非同期処理（WS のコールバックなど）から「いまの」state を読むための参照
  const stateRef = useRef(state);
  useEffect(() => {
    stateRef.current = state;
  });

  // ---- 初回ロード → WebSocket 接続 ----
  useEffect(() => {
    let disposed = false;
    const socket = new ChatSocket({
      onStatus: (status) => dispatch({ type: "connection", status }),
      onEvent: (event) => dispatch({ type: "ws", event, visible: document.visibilityState === "visible" }),
      onUnauthorized: markLoggedOut,
      onHello: async () => {
        // 接続・再接続のたびに、前回の sync_token 以降の変更を取り直す（初回ロード〜接続までの隙間も埋まる）
        const since = stateRef.current.syncToken;
        if (!since) return;
        try {
          const [res, presence] = await Promise.all([Api.sync(since), Api.presence()]);
          if (!disposed) dispatch({ type: "sync/applied", res, onlineIds: presence.online_user_ids });
        } catch (e) {
          console.warn("sync failed", errorMessage(e));
        }
      },
    });

    (async () => {
      const [rooms, presence] = await Promise.all([Api.rooms(), Api.presence()]);
      if (disposed) return;
      dispatch({ type: "rooms/loaded", rooms: rooms.rooms, syncToken: rooms.sync_token, onlineIds: presence.online_user_ids });
      socket.start();
    })().catch((e) => console.error(e));

    return () => {
      disposed = true;
      socket.stop();
    };
  }, [markLoggedOut]);

  // ---- 未読の合計をタブのタイトルに出す ----
  useEffect(() => {
    const total = Object.values(state.rooms).reduce((n, r) => n + (r.is_muted ? 0 : r.unread_count), 0);
    document.title = total > 0 ? `(${total}) Pyslack` : "Pyslack";
  }, [state.rooms]);

  const actions = useMemo<ChatActions>(() => {
    const loadingOlder = new Set<number>();
    const readInFlight = new Map<number, number>();

    const postPending = async (p: PendingMessage) => {
      try {
        const message = await Api.postMessage(p.room_id, {
          body: p.body,
          attachment_ids: p.attachment_ids,
          client_msg_id: p.client_msg_id, // 再送しても同じ値 → サーバー側で二重投稿にならない
        });
        dispatch({ type: "message/sent", message });
      } catch (e) {
        console.warn("send failed", errorMessage(e));
        dispatch({ type: "pending/status", id: p.client_msg_id, status: "failed" });
      }
    };

    return {
      setActive: (roomId) => dispatch({ type: "active", roomId }),

      loadLatest: async (roomId) => {
        const page = await Api.messages(roomId);
        dispatch({ type: "messages/page", roomId, messages: page.results, hasMore: page.has_more, mode: "latest" });
      },

      loadOlder: (roomId) => {
        const rm = stateRef.current.roomMessages[roomId];
        if (!rm?.loaded || !rm.hasMore || loadingOlder.has(roomId) || rm.ids.length === 0) return null;
        loadingOlder.add(roomId);
        return Api.messages(roomId, { before: rm.ids[0] })
          .then((page) =>
            dispatch({ type: "messages/page", roomId, messages: page.results, hasMore: page.has_more, mode: "older" }),
          )
          .finally(() => loadingOlder.delete(roomId));
      },

      send: (roomId, body, attachments) => {
        const pending: PendingMessage = {
          client_msg_id: uuid(),
          room_id: roomId,
          body,
          attachment_ids: attachments.map((a) => a.id),
          attachment_names: attachments.map((a) => a.name),
          status: "sending",
          created_at: new Date().toISOString(),
        };
        dispatch({ type: "pending/add", pending }); // 楽観的 UI: 確定前に画面に出す
        void postPending(pending);
      },

      retry: (clientMsgId) => {
        const p = stateRef.current.pending[clientMsgId];
        if (!p) return;
        dispatch({ type: "pending/status", id: clientMsgId, status: "sending" });
        void postPending(p);
      },

      edit: async (messageId, body) => {
        const message = await Api.editMessage(messageId, body);
        dispatch({ type: "message/sent", message });
      },

      remove: async (messageId) => {
        await Api.deleteMessage(messageId);
        // 画面の更新は WS の message.deleted に任せる（自分の他のタブとも同じ経路になる）
      },

      markRead: (roomId, messageId) => {
        const room = stateRef.current.rooms[roomId];
        if (!room || messageId <= room.last_read_message_id || (readInFlight.get(roomId) ?? 0) >= messageId) return;
        readInFlight.set(roomId, messageId);
        dispatch({ type: "read/local", roomId, lastReadId: messageId }); // 先に未読を 0 にしておく
        Api.markRead(roomId, messageId)
          .then((data) => dispatch({ type: "ws", visible: true, event: { type: "read.updated", ts: "", data } }))
          .catch((e) => console.warn("markRead failed", errorMessage(e)));
      },

      upsertRoom: (room) => dispatch({ type: "room/upsert", room }),

      updateMe: (next) => {
        setMe(next);
        dispatch({ type: "me/updated", me: next });
      },
    };
  }, [setMe]);

  return (
    <ActionsContext.Provider value={actions}>
      <StateContext.Provider value={state}>{children}</StateContext.Provider>
    </ActionsContext.Provider>
  );
}

export function useChatState(): ChatState {
  const ctx = useContext(StateContext);
  if (!ctx) throw new Error("useChatState must be used within ChatProvider");
  return ctx;
}

export function useChatActions(): ChatActions {
  const ctx = useContext(ActionsContext);
  if (!ctx) throw new Error("useChatActions must be used within ChatProvider");
  return ctx;
}
