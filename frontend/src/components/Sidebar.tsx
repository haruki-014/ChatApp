import { useState } from "react";
import { NavLink } from "react-router";
import type { Room } from "../api/types";
import { useAuth } from "../store/auth";
import { isOnline, sortedRooms, type ChatState } from "../store/chatReducer";
import { useChatState } from "../store/chat";
import { Avatar, PresenceDot } from "./Avatar";
import { NewRoomDialog, type NewRoomTab } from "./NewRoomDialog";
import { ProfileDialog } from "./ProfileDialog";

function RoomIcon({ room, state }: { room: Room; state: ChatState }) {
  if (room.type === "channel") return <span className="room-icon">{room.is_private ? "🔒" : "#"}</span>;
  if (room.type === "dm") return <PresenceDot online={isOnline(state, room.dm_partner)} />;
  return <span className="room-icon group">{room.member_count}</span>;
}

function RoomLink({ room, state }: { room: Room; state: ChatState }) {
  const unread = room.unread_count > 0 && !room.is_muted;
  return (
    <NavLink to={`/rooms/${room.id}`} className={({ isActive }) => `room-link${isActive ? " active" : ""}${unread ? " unread" : ""}${room.is_muted ? " muted-room" : ""}`}>
      <RoomIcon room={room} state={state} />
      <span className="room-name">{room.display_name}</span>
      {room.type === "dm" && room.dm_partner?.status_emoji && <span>{room.dm_partner.status_emoji}</span>}
      {unread && <span className="badge">{room.unread_count}</span>}
    </NavLink>
  );
}

const CONNECTION_LABEL = { connecting: "接続中…", open: "", closed: "再接続しています…" } as const;

export function Sidebar() {
  const state = useChatState();
  const { logout } = useAuth();
  const [dialog, setDialog] = useState<NewRoomTab | "profile" | null>(null);

  const rooms = sortedRooms(state);
  const channels = rooms.filter((r) => r.type === "channel");
  const dms = rooms.filter((r) => r.type !== "channel");
  const me = state.me;

  return (
    <aside className="sidebar">
      <header className="sidebar-header">
        <strong>Pyslack</strong>
        {CONNECTION_LABEL[state.connection] && (
          <span className={`conn conn-${state.connection}`}>{CONNECTION_LABEL[state.connection]}</span>
        )}
      </header>

      <nav className="sidebar-scroll">
        <div className="section">
          <div className="section-title">
            <span>チャンネル</span>
            <button className="icon" title="チャンネルを作成・参加" onClick={() => setDialog("browse")}>
              ＋
            </button>
          </div>
          {channels.map((r) => (
            <RoomLink key={r.id} room={r} state={state} />
          ))}
        </div>

        <div className="section">
          <div className="section-title">
            <span>ダイレクトメッセージ</span>
            <button className="icon" title="DM・グループを開始" onClick={() => setDialog("dm")}>
              ＋
            </button>
          </div>
          {dms.map((r) => (
            <RoomLink key={r.id} room={r} state={state} />
          ))}
        </div>
      </nav>

      <footer className="sidebar-footer">
        <button className="me" onClick={() => setDialog("profile")} title="プロフィール・ステータスを編集">
          <Avatar user={me} size={28} />
          <span className="me-text">
            <span className="me-name">{me.display_name}</span>
            <span className="me-status">
              {me.status_emoji} {me.status_text || "ステータスを設定"}
            </span>
          </span>
        </button>
        <button className="link" onClick={() => void logout()}>
          ログアウト
        </button>
      </footer>

      {dialog === "profile" && <ProfileDialog onClose={() => setDialog(null)} />}
      {dialog && dialog !== "profile" && <NewRoomDialog initialTab={dialog} onClose={() => setDialog(null)} />}
    </aside>
  );
}
