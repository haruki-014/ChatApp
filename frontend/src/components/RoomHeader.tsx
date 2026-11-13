import { useState } from "react";
import { useNavigate } from "react-router";
import { errorMessage } from "../api/client";
import { Api } from "../api/endpoints";
import type { Room } from "../api/types";
import { isOnline, userOf } from "../store/chatReducer";
import { useChatActions, useChatState } from "../store/chat";
import { PresenceDot } from "./Avatar";
import { MembersDialog } from "./MembersDialog";

export function RoomHeader({ room }: { room: Room }) {
  const state = useChatState();
  const actions = useChatActions();
  const navigate = useNavigate();
  const [showMembers, setShowMembers] = useState(false);
  const partner = room.dm_partner ? userOf(state, room.dm_partner) : null;

  async function toggleMute() {
    actions.upsertRoom(await Api.setMuted(room.id, !room.is_muted));
  }

  async function leave() {
    if (!confirm(`「${room.display_name}」から退出しますか？`)) return;
    try {
      await Api.leaveRoom(room.id); // 一覧からの削除は WS の room.removed で行われる
      navigate("/");
    } catch (e) {
      alert(errorMessage(e));
    }
  }

  return (
    <header className="room-header">
      <div className="room-title">
        <h2>
          {room.type === "channel" ? (room.is_private ? "🔒 " : "# ") : ""}
          {room.display_name}
        </h2>
        {partner && (
          <span className="room-sub">
            <PresenceDot online={isOnline(state, partner)} />
            {isOnline(state, partner) ? "オンライン" : "オフライン"}
            {(partner.status_emoji || partner.status_text) && (
              <span className="status">
                {partner.status_emoji} {partner.status_text}
              </span>
            )}
          </span>
        )}
      </div>
      <div className="room-actions">
        <button onClick={() => setShowMembers(true)} title="メンバー">
          👥 {room.member_count}
        </button>
        <button onClick={() => void toggleMute()} title="ミュートすると未読バッジを表示しません">
          {room.is_muted ? "🔕 ミュート中" : "🔔"}
        </button>
        {room.type !== "dm" && <button onClick={() => void leave()}>退出</button>}
      </div>
      {showMembers && <MembersDialog room={room} onClose={() => setShowMembers(false)} />}
    </header>
  );
}
