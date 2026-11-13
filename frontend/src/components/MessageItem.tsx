import { useState, type KeyboardEvent } from "react";
import { errorMessage } from "../api/client";
import type { Attachment, Message } from "../api/types";
import { formatFull, formatSize, formatTime } from "../lib/format";
import { userOf, type PendingMessage } from "../store/chatReducer";
import { useChatActions, useChatState } from "../store/chat";
import { Avatar } from "./Avatar";

function AttachmentView({ att }: { att: Attachment }) {
  if (att.is_image && att.preview_url) {
    // 同一オリジンの URL → Cookie 付きで Django が権限チェック → 302 で署名付き URL へ
    return (
      <a className="att-image" href={att.download_url} target="_blank" rel="noreferrer" title={att.original_filename}>
        <img src={att.preview_url} alt={att.original_filename} loading="lazy" />
      </a>
    );
  }
  return (
    <a className="att-file" href={att.download_url}>
      <span className="att-icon">📄</span>
      <span>
        <span className="att-name">{att.original_filename}</span>
        <span className="muted small">{formatSize(att.size)}</span>
      </span>
    </a>
  );
}

export function MessageItem({ message, mine, compact }: { message: Message; mine: boolean; compact: boolean }) {
  const state = useChatState();
  const actions = useChatActions();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(message.body);
  const [error, setError] = useState<string | null>(null);
  const sender = userOf(state, message.sender);

  async function save() {
    try {
      await actions.edit(message.id, draft);
      setEditing(false);
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function remove() {
    if (!confirm("このメッセージを削除しますか？")) return;
    try {
      await actions.remove(message.id);
    } catch (e) {
      alert(errorMessage(e));
    }
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    // 日本語入力の変換確定の Enter（isComposing）では保存しない
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void save();
    } else if (e.key === "Escape") {
      setEditing(false);
      setDraft(message.body);
    }
  }

  return (
    <div className={`message${compact ? " compact" : ""}`}>
      <div className="message-gutter">
        {compact ? (
          <span className="hover-time" title={formatFull(message.created_at)}>
            {formatTime(message.created_at)}
          </span>
        ) : (
          <Avatar user={sender} />
        )}
      </div>
      <div className="message-main">
        {!compact && (
          <div className="message-meta">
            <strong>{sender.display_name}</strong>
            {sender.status_emoji && <span title={sender.status_text}>{sender.status_emoji}</span>}
            <time className="muted small" title={formatFull(message.created_at)}>
              {formatTime(message.created_at)}
            </time>
          </div>
        )}

        {message.deleted_at ? (
          <p className="deleted muted">このメッセージは削除されました</p>
        ) : editing ? (
          <div className="edit-box">
            <textarea value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={onKeyDown} autoFocus rows={2} />
            <div className="muted small">
              Enter で保存・Esc でキャンセル
              <button className="link" onClick={() => void save()}>
                保存
              </button>
            </div>
            {error && <p className="error">{error}</p>}
          </div>
        ) : (
          <>
            {message.body && (
              <p className="body">
                {message.body}
                {message.edited_at && (
                  <span className="muted small" title={formatFull(message.edited_at)}>
                    {" "}
                    (編集済み)
                  </span>
                )}
              </p>
            )}
            {message.attachments.length > 0 && (
              <div className="attachments">
                {message.attachments.map((a) => (
                  <AttachmentView key={a.id} att={a} />
                ))}
              </div>
            )}
          </>
        )}
      </div>

      {mine && !message.deleted_at && !editing && (
        <div className="message-actions">
          <button
            onClick={() => {
              setDraft(message.body);
              setEditing(true);
            }}
          >
            編集
          </button>
          <button className="danger" onClick={() => void remove()}>
            削除
          </button>
        </div>
      )}
    </div>
  );
}

/** 送信中（サーバーの確定待ち）・送信失敗のメッセージ */
export function PendingItem({ pending }: { pending: PendingMessage }) {
  const state = useChatState();
  const actions = useChatActions();
  return (
    <div className={`message pending ${pending.status}`}>
      <div className="message-gutter">
        <Avatar user={state.me} />
      </div>
      <div className="message-main">
        <div className="message-meta">
          <strong>{state.me.display_name}</strong>
          <span className="muted small">{pending.status === "sending" ? "送信中…" : "送信に失敗しました"}</span>
          {pending.status === "failed" && (
            <button className="link" onClick={() => actions.retry(pending.client_msg_id)}>
              再送信
            </button>
          )}
        </div>
        {pending.body && <p className="body">{pending.body}</p>}
        {pending.attachment_names.map((n) => (
          <div key={n} className="muted small">
            📎 {n}
          </div>
        ))}
      </div>
    </div>
  );
}
