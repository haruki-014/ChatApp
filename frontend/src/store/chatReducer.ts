/**
 * チャットの状態と、それを変える唯一の場所（reducer）。
 *
 * 状態は REST（初回ロード、過去ログ、/api/sync、自分の送信結果）と WebSocket（他人の変更）の
 * 2 系統から更新される。どちらから来ても、どの順で来ても、同じ結果になるように
 * 「id をキーにした upsert」＋「updated_at が新しい場合だけ上書き」で統一する（docs/04-websocket-events.md の 4-7）。
 */
import type { Me, Message, MessageDeletedData, Room, SyncResponse, UserSummary, WsEvent } from "../api/types";
import type { ConnectionStatus } from "../ws/socket";

export type RoomMessages = { ids: number[]; hasMore: boolean; loaded: boolean };

export type PendingMessage = {
  client_msg_id: string;
  room_id: number;
  body: string;
  attachment_ids: number[];
  attachment_names: string[];
  status: "sending" | "failed";
  created_at: string;
};

export type ChatState = {
  me: Me;
  ready: boolean;
  users: Record<number, UserSummary>;
  online: Record<number, boolean>;
  rooms: Record<number, Room>;
  messages: Record<number, Message>;
  roomMessages: Record<number, RoomMessages>;
  pending: Record<string, PendingMessage>;
  syncToken: string | null;
  connection: ConnectionStatus;
  activeRoomId: number | null;
};

export type Action =
  | { type: "rooms/loaded"; rooms: Room[]; syncToken: string; onlineIds: number[] }
  | { type: "sync/applied"; res: SyncResponse; onlineIds: number[] }
  | { type: "room/upsert"; room: Room }
  | { type: "messages/page"; roomId: number; messages: Message[]; hasMore: boolean; mode: "latest" | "older" }
  | { type: "message/sent"; message: Message }
  | { type: "ws"; event: WsEvent; visible: boolean }
  | { type: "read/local"; roomId: number; lastReadId: number }
  | { type: "pending/add"; pending: PendingMessage }
  | { type: "pending/status"; id: string; status: PendingMessage["status"] }
  | { type: "connection"; status: ConnectionStatus }
  | { type: "active"; roomId: number | null }
  | { type: "me/updated"; me: Me };

export function initialState(me: Me): ChatState {
  return {
    me,
    ready: false,
    users: { [me.id]: me },
    online: {},
    rooms: {},
    messages: {},
    roomMessages: {},
    pending: {},
    syncToken: null,
    connection: "connecting",
    activeRoomId: null,
  };
}

const time = (iso: string | null) => (iso ? Date.parse(iso) : 0);
const laterIso = (a: string | null, b: string | null) => (time(b) > time(a) ? b : a);

function withUsers(users: Record<number, UserSummary>, list: (UserSummary | null | undefined)[]) {
  let next = users;
  for (const u of list) {
    if (!u) continue;
    if (next === users) next = { ...users };
    next[u.id] = u;
  }
  return next;
}

function onlineFrom(ids: number[]) {
  return Object.fromEntries(ids.map((id) => [id, true]));
}

/** メッセージ 1 件を取り込む。新規なら isNew=true を返す（未読数の加算に使う） */
function upsertMessage(state: ChatState, msg: Message): { state: ChatState; isNew: boolean } {
  const prev = state.messages[msg.id];
  if (prev && time(prev.updated_at) > time(msg.updated_at)) {
    return { state, isNew: false }; // 古いイベントが後から届いた → 無視（巻き戻さない）
  }

  let roomMessages = state.roomMessages;
  const rm = roomMessages[msg.room_id];
  if (rm?.loaded && !rm.ids.includes(msg.id)) {
    const oldest = rm.ids[0];
    // 読み込み済み範囲より古いものは、スクロールで遡ったときに取得されるので入れない
    if (oldest === undefined || msg.id > oldest || !rm.hasMore) {
      roomMessages = { ...roomMessages, [msg.room_id]: { ...rm, ids: [...rm.ids, msg.id].sort((a, b) => a - b) } };
    }
  }

  let pending = state.pending;
  if (msg.client_msg_id && pending[msg.client_msg_id]) {
    pending = { ...pending };
    delete pending[msg.client_msg_id]; // 仮メッセージ → 確定メッセージに置き換え
  }

  let rooms = state.rooms;
  const room = rooms[msg.room_id];
  if (room && !prev) {
    rooms = { ...rooms, [room.id]: { ...room, last_message_at: laterIso(room.last_message_at, msg.created_at) } };
  }

  return {
    state: {
      ...state,
      messages: { ...state.messages, [msg.id]: msg },
      roomMessages,
      pending,
      rooms,
      users: withUsers(state.users, [msg.sender]),
    },
    isNew: !prev,
  };
}

function patchRoom(state: ChatState, roomId: number, patch: Partial<Room>): ChatState {
  const room = state.rooms[roomId];
  if (!room) return state;
  return { ...state, rooms: { ...state.rooms, [roomId]: { ...room, ...patch } } };
}

function applyDeleted(state: ChatState, d: MessageDeletedData): ChatState {
  const prev = state.messages[d.id];
  if (!prev) return state;
  if (time(prev.updated_at) > time(d.updated_at)) return state;
  let next: ChatState = {
    ...state,
    messages: { ...state.messages, [d.id]: { ...prev, body: "", attachments: [], deleted_at: d.deleted_at, updated_at: d.updated_at } },
  };
  // 未読だったメッセージが消えたら未読数も減らす（サーバーの未読数も削除済みを数えない）
  const room = state.rooms[d.room_id];
  if (room && !prev.deleted_at && prev.sender.id !== state.me.id && d.id > room.last_read_message_id && room.unread_count > 0) {
    next = patchRoom(next, d.room_id, { unread_count: room.unread_count - 1 });
  }
  return next;
}

function applyEvent(state: ChatState, event: WsEvent, visible: boolean): ChatState {
  switch (event.type) {
    case "message.created": {
      const msg = event.data;
      const { state: next, isNew } = upsertMessage(state, msg);
      const room = next.rooms[msg.room_id];
      const viewing = visible && state.activeRoomId === msg.room_id; // 見ているルームは既読処理側で 0 にする
      if (isNew && room && msg.sender.id !== state.me.id && msg.id > room.last_read_message_id && !viewing) {
        return patchRoom(next, room.id, { unread_count: room.unread_count + 1 });
      }
      return next;
    }
    case "message.updated":
      return upsertMessage(state, event.data).state;
    case "message.deleted":
      return applyDeleted(state, event.data);
    case "read.updated": {
      const room = state.rooms[event.data.room_id];
      if (!room || event.data.last_read_message_id < room.last_read_message_id) return state;
      return patchRoom(state, room.id, {
        last_read_message_id: event.data.last_read_message_id,
        unread_count: event.data.unread_count,
      });
    }
    case "room.added":
      return {
        ...state,
        rooms: { ...state.rooms, [event.data.id]: event.data },
        users: withUsers(state.users, [event.data.dm_partner]),
      };
    case "room.removed": {
      const rooms = { ...state.rooms };
      delete rooms[event.data.room_id];
      const roomMessages = { ...state.roomMessages };
      delete roomMessages[event.data.room_id];
      return { ...state, rooms, roomMessages };
    }
    case "room.updated":
      return patchRoom(state, event.data.id, { name: event.data.name, display_name: event.data.display_name });
    case "room.member_joined":
      return {
        ...patchRoom(state, event.data.room_id, { member_count: event.data.member_count }),
        users: withUsers(state.users, [event.data.user]),
      };
    case "room.member_left":
      return patchRoom(state, event.data.room_id, { member_count: event.data.member_count });
    case "presence.changed":
      return { ...state, online: { ...state.online, [event.data.user_id]: event.data.is_online } };
    case "user.updated": {
      const u = event.data.user;
      const me = u.id === state.me.id ? { ...state.me, ...u } : state.me;
      // DM の相手の表示名も変わるので、ルームの display_name も直す
      const rooms = Object.fromEntries(
        Object.values(state.rooms).map((r) =>
          r.dm_partner?.id === u.id ? [r.id, { ...r, dm_partner: u, display_name: u.display_name }] : [r.id, r],
        ),
      );
      return { ...state, me, rooms, users: withUsers(state.users, [u]) };
    }
    default:
      return state;
  }
}

export function reducer(state: ChatState, action: Action): ChatState {
  switch (action.type) {
    case "rooms/loaded":
      return {
        ...state,
        ready: true,
        rooms: Object.fromEntries(action.rooms.map((r) => [r.id, r])),
        users: withUsers(state.users, action.rooms.map((r) => r.dm_partner)),
        online: onlineFrom(action.onlineIds),
        syncToken: action.syncToken,
      };

    case "sync/applied": {
      // rooms はサーバーの値で丸ごと置き換える（切断中に退出・除外されたルームも消える）
      let next: ChatState = {
        ...state,
        rooms: Object.fromEntries(action.res.rooms.map((r) => [r.id, r])),
        users: withUsers(state.users, action.res.rooms.map((r) => r.dm_partner)),
        online: onlineFrom(action.onlineIds),
        syncToken: laterIso(state.syncToken, action.res.sync_token),
      };
      if (action.res.reset) {
        next = { ...next, roomMessages: {} }; // 変更が多すぎた → 表示中のルームは取り直してもらう
      } else {
        for (const m of action.res.messages) next = upsertMessage(next, m).state;
      }
      // 上の upsert で last_message_at が書き換わることがあるので、サーバーの値で戻す
      return { ...next, rooms: Object.fromEntries(action.res.rooms.map((r) => [r.id, r])) };
    }

    case "room/upsert":
      return {
        ...state,
        rooms: { ...state.rooms, [action.room.id]: action.room },
        users: withUsers(state.users, [action.room.dm_partner]),
      };

    case "messages/page": {
      const messages = { ...state.messages };
      for (const m of action.messages) {
        const prevMsg = messages[m.id];
        if (!prevMsg || time(m.updated_at) >= time(prevMsg.updated_at)) messages[m.id] = m;
      }
      const next = { ...state, messages };
      const prev = state.roomMessages[action.roomId];
      const pageIds = action.messages.map((m) => m.id);
      const ids =
        action.mode === "latest"
          ? pageIds
          : [...new Set([...pageIds, ...(prev?.ids ?? [])])].sort((a, b) => a - b);
      return {
        ...next,
        users: withUsers(next.users, action.messages.map((m) => m.sender)),
        roomMessages: { ...next.roomMessages, [action.roomId]: { ids, hasMore: action.hasMore, loaded: true } },
      };
    }

    case "message/sent":
      return upsertMessage(state, action.message).state;

    case "ws": {
      const next = applyEvent(state, action.event, action.visible);
      return next.syncToken === laterIso(next.syncToken, action.event.ts)
        ? next
        : { ...next, syncToken: action.event.ts };
    }

    case "read/local": {
      const room = state.rooms[action.roomId];
      if (!room || action.lastReadId <= room.last_read_message_id) return state;
      return patchRoom(state, room.id, { last_read_message_id: action.lastReadId, unread_count: 0 });
    }

    case "pending/add":
      return { ...state, pending: { ...state.pending, [action.pending.client_msg_id]: action.pending } };

    case "pending/status": {
      const p = state.pending[action.id];
      return p ? { ...state, pending: { ...state.pending, [action.id]: { ...p, status: action.status } } } : state;
    }

    case "connection":
      return { ...state, connection: action.status };

    case "active":
      return { ...state, activeRoomId: action.roomId };

    case "me/updated":
      return { ...state, me: action.me, users: withUsers(state.users, [action.me]) };
  }
}

// ---------------------------------------------------------------------------
// セレクタ
// ---------------------------------------------------------------------------
export function sortedRooms(state: ChatState): Room[] {
  return Object.values(state.rooms).sort(
    (a, b) => time(b.last_message_at) - time(a.last_message_at) || b.id - a.id,
  );
}

export function isOnline(state: ChatState, user: UserSummary | null | undefined): boolean {
  if (!user) return false;
  return state.online[user.id] ?? false;
}

export function userOf(state: ChatState, user: UserSummary): UserSummary {
  return state.users[user.id] ?? user; // 表示名やステータスは最新のものを優先する
}
