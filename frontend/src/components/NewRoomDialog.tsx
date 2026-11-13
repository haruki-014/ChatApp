import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { errorMessage } from "../api/client";
import { Api } from "../api/endpoints";
import type { Channel, Room, UserSummary } from "../api/types";
import { useChatActions, useChatState } from "../store/chat";
import { Modal } from "./Modal";
import { UserPicker } from "./UserPicker";

export type NewRoomTab = "dm" | "channel" | "browse";

export function NewRoomDialog({ initialTab, onClose }: { initialTab: NewRoomTab; onClose: () => void }) {
  const [tab, setTab] = useState<NewRoomTab>(initialTab);
  const actions = useChatActions();
  const navigate = useNavigate();

  const open = (room: Room) => {
    actions.upsertRoom(room); // WS の room.added を待たずに一覧へ出す（後から来ても upsert なので重複しない）
    navigate(`/rooms/${room.id}`);
    onClose();
  };

  return (
    <Modal title="会話をはじめる" onClose={onClose}>
      <div className="tabs">
        <button className={tab === "dm" ? "active" : ""} onClick={() => setTab("dm")}>
          DM・グループ
        </button>
        <button className={tab === "browse" ? "active" : ""} onClick={() => setTab("browse")}>
          チャンネルを探す
        </button>
        <button className={tab === "channel" ? "active" : ""} onClick={() => setTab("channel")}>
          チャンネルを作成
        </button>
      </div>
      {tab === "dm" && <DmTab onOpen={open} />}
      {tab === "browse" && <BrowseTab onOpen={open} />}
      {tab === "channel" && <CreateChannelTab onOpen={open} />}
    </Modal>
  );
}

function DmTab({ onOpen }: { onOpen: (room: Room) => void }) {
  const me = useChatState().me;
  const [selected, setSelected] = useState<UserSummary[]>([]);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    try {
      const room =
        selected.length === 1
          ? await Api.openDm(selected[0].id)
          : await Api.createRoom({ type: "group", name: name || undefined, member_ids: selected.map((u) => u.id) });
      onOpen(room);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  return (
    <div className="stack">
      <UserPicker selected={selected} onChange={setSelected} exclude={[me.id]} />
      {selected.length >= 2 && (
        <input placeholder="グループ名（任意）" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} />
      )}
      {error && <p className="error">{error}</p>}
      <button className="primary" disabled={selected.length === 0} onClick={() => void submit()}>
        {selected.length >= 2 ? `${selected.length + 1} 人のグループを作成` : "DM を開く"}
      </button>
    </div>
  );
}

function BrowseTab({ onOpen }: { onOpen: (room: Room) => void }) {
  const [channels, setChannels] = useState<Channel[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Api.channels().then(setChannels).catch((e) => setError(errorMessage(e)));
  }, []);

  async function join(c: Channel) {
    try {
      onOpen(await Api.joinRoom(c.id)); // 参加済みなら何もせずルーム情報が返る
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  if (!channels) return <p className="muted">読み込み中…</p>;
  return (
    <div className="stack">
      {error && <p className="error">{error}</p>}
      {channels.length === 0 && <p className="muted">公開チャンネルはまだありません</p>}
      <ul className="channel-list">
        {channels.map((c) => (
          <li key={c.id}>
            <span># {c.name}</span>
            <span className="muted small">{c.member_count} 人</span>
            <button onClick={() => void join(c)}>{c.is_member ? "開く" : "参加する"}</button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function CreateChannelTab({ onOpen }: { onOpen: (room: Room) => void }) {
  const me = useChatState().me;
  const [name, setName] = useState("");
  const [isPrivate, setIsPrivate] = useState(false);
  const [members, setMembers] = useState<UserSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      onOpen(await Api.createRoom({ type: "channel", name, is_private: isPrivate, member_ids: members.map((m) => m.id) }));
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <form className="stack" onSubmit={submit}>
      <label>
        チャンネル名
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="例: random" required maxLength={80} />
      </label>
      <label className="checkbox">
        <input type="checkbox" checked={isPrivate} onChange={(e) => setIsPrivate(e.target.checked)} />
        非公開にする（招待されたメンバーだけが参加できる）
      </label>
      <div>
        <div className="muted small">最初のメンバー（任意）</div>
        <UserPicker selected={members} onChange={setMembers} exclude={[me.id]} />
      </div>
      {error && <p className="error">{error}</p>}
      <button className="primary">作成</button>
    </form>
  );
}
