import { useState, type ChangeEvent, type FormEvent } from "react";
import { errorMessage } from "../api/client";
import { Api } from "../api/endpoints";
import { MAX_AVATAR_BYTES, contentTypeFor, isImageType, uploadToStorage } from "../lib/upload";
import { useChatActions, useChatState } from "../store/chat";
import { Avatar } from "./Avatar";
import { Modal } from "./Modal";

const PRESETS: [string, string][] = [
  ["📅", "会議中"],
  ["🍵", "休憩中"],
  ["🚃", "移動中"],
  ["🤒", "体調不良"],
  ["🏠", "リモートワーク"],
];

export function ProfileDialog({ onClose }: { onClose: () => void }) {
  const me = useChatState().me;
  const actions = useChatActions();
  const [displayName, setDisplayName] = useState(me.display_name);
  const [emoji, setEmoji] = useState(me.status_emoji);
  const [text, setText] = useState(me.status_text);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function save(e?: FormEvent, override?: { status_emoji: string; status_text: string }) {
    e?.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const updated = await Api.updateProfile({
        display_name: displayName,
        status_emoji: override?.status_emoji ?? emoji,
        status_text: override?.status_text ?? text,
      });
      actions.updateMe(updated); // 他の人の画面には WS の user.updated で届く
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function changeAvatar(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    const type = contentTypeFor(file);
    if (!type || !isImageType(type)) return setError("画像ファイル（png / jpg / gif / webp）を選んでください");
    if (file.size > MAX_AVATAR_BYTES) return setError("アバター画像は 5MB までです");
    setBusy(true);
    setError(null);
    try {
      const presign = await Api.presignAvatar(type, file.size); // ① 署名をもらう
      await uploadToStorage(presign.upload, file); // ② ストレージへ直接アップロード
      actions.updateMe(await Api.updateProfile({ avatar_key: presign.avatar_key })); // ③ キーを登録
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="プロフィールとステータス" onClose={onClose}>
      <form className="stack" onSubmit={save}>
        <div className="avatar-row">
          <Avatar user={me} size={56} />
          <label className="button-like">
            画像を変更
            <input type="file" accept="image/png,image/jpeg,image/gif,image/webp" hidden onChange={(e) => void changeAvatar(e)} />
          </label>
        </div>
        <label>
          表示名
          <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} maxLength={80} />
        </label>
        <div className="status-row">
          <input className="emoji-input" value={emoji} onChange={(e) => setEmoji(e.target.value)} placeholder="😀" maxLength={16} />
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder="ステータスを入力" maxLength={100} />
        </div>
        <div className="presets">
          {PRESETS.map(([em, label]) => (
            <button
              type="button"
              key={label}
              onClick={() => {
                setEmoji(em);
                setText(label);
              }}
            >
              {em} {label}
            </button>
          ))}
        </div>
        {error && <p className="error">{error}</p>}
        <div className="row-end">
          <button type="button" disabled={busy} onClick={() => void save(undefined, { status_emoji: "", status_text: "" })}>
            ステータスをクリア
          </button>
          <button className="primary" disabled={busy}>
            保存
          </button>
        </div>
      </form>
    </Modal>
  );
}
