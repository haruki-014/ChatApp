import { useEffect, useState } from "react";
import { Api } from "../api/endpoints";
import type { UserSummary } from "../api/types";
import { isOnline } from "../store/chatReducer";
import { useChatState } from "../store/chat";
import { Avatar, PresenceDot } from "./Avatar";

/** ユーザーを検索して選ぶ。exclude に入っている id（自分や既存メンバー）は候補に出さない */
export function UserPicker({
  selected,
  onChange,
  exclude = [],
}: {
  selected: UserSummary[];
  onChange: (users: UserSummary[]) => void;
  exclude?: number[];
}) {
  const state = useChatState();
  const [q, setQ] = useState("");
  const [results, setResults] = useState<UserSummary[]>([]);

  useEffect(() => {
    const t = setTimeout(() => {
      Api.searchUsers(q.trim()).then(setResults).catch(() => setResults([]));
    }, 250); // 入力のたびに API を呼ばないよう少し待つ（デバウンス）
    return () => clearTimeout(t);
  }, [q]);

  const toggle = (u: UserSummary) =>
    onChange(selected.some((s) => s.id === u.id) ? selected.filter((s) => s.id !== u.id) : [...selected, u]);

  const candidates = results.filter((u) => !exclude.includes(u.id));

  return (
    <div className="user-picker">
      {selected.length > 0 && (
        <div className="chips">
          {selected.map((u) => (
            <button key={u.id} className="chip" onClick={() => toggle(u)}>
              {u.display_name} ✕
            </button>
          ))}
        </div>
      )}
      <input placeholder="名前やユーザー名で検索" value={q} onChange={(e) => setQ(e.target.value)} autoFocus />
      <ul className="user-list">
        {candidates.map((u) => (
          <li key={u.id}>
            <label>
              <input type="checkbox" checked={selected.some((s) => s.id === u.id)} onChange={() => toggle(u)} />
              <Avatar user={u} size={24} />
              <PresenceDot online={isOnline(state, u)} />
              <span>{u.display_name}</span>
              <span className="muted">@{u.username}</span>
            </label>
          </li>
        ))}
        {candidates.length === 0 && <li className="muted">該当するユーザーがいません</li>}
      </ul>
    </div>
  );
}
