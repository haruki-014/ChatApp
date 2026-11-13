import { useCallback, useEffect, useState } from "react";
import { errorMessage } from "../api/client";
import { Api } from "../api/endpoints";
import type { Member, Room, UserSummary } from "../api/types";
import { isOnline, userOf } from "../store/chatReducer";
import { useChatState } from "../store/chat";
import { Avatar, PresenceDot } from "./Avatar";
import { Modal } from "./Modal";
import { UserPicker } from "./UserPicker";

const ROLE_LABEL = { owner: "オーナー", admin: "管理者", member: "" } as const;

export function MembersDialog({ room, onClose }: { room: Room; onClose: () => void }) {
  const state = useChatState();
  const [members, setMembers] = useState<Member[]>([]);
  const [adding, setAdding] = useState<UserSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const canManage = room.type !== "dm" && (room.role === "owner" || room.role === "admin");

  const reload = useCallback(() => {
    Api.members(room.id).then(setMembers).catch((e) => setError(errorMessage(e)));
  }, [room.id]);

  useEffect(reload, [reload, room.member_count]); // 誰かが参加・退出したら（WS で member_count が変わる）取り直す

  async function add() {
    try {
      await Api.addMembers(room.id, adding.map((u) => u.id));
      setAdding([]);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function remove(userId: number) {
    try {
      await Api.removeMember(room.id, userId);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  return (
    <Modal title={`メンバー（${members.length}）`} onClose={onClose}>
      <ul className="member-list">
        {members.map((m) => {
          const u = userOf(state, m.user);
          return (
            <li key={u.id}>
              <Avatar user={u} size={28} />
              <PresenceDot online={isOnline(state, u)} />
              <span>{u.display_name}</span>
              <span className="muted">
                {u.status_emoji} {u.status_text}
              </span>
              <span className="role">{ROLE_LABEL[m.role]}</span>
              {canManage && u.id !== state.me.id && (
                <button className="link danger" onClick={() => void remove(u.id)}>
                  外す
                </button>
              )}
            </li>
          );
        })}
      </ul>
      {room.type !== "dm" && (
        <div className="stack add-members">
          <div className="muted small">メンバーを追加</div>
          <UserPicker selected={adding} onChange={setAdding} exclude={members.map((m) => m.user.id)} />
          <button className="primary" disabled={adding.length === 0} onClick={() => void add()}>
            追加
          </button>
        </div>
      )}
      {error && <p className="error">{error}</p>}
    </Modal>
  );
}
