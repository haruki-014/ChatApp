import { useRef, useState, type DragEvent, type FormEvent, type KeyboardEvent } from "react";
import { errorMessage } from "../api/client";
import { Api } from "../api/endpoints";
import type { Room } from "../api/types";
import { formatSize } from "../lib/format";
import { ACCEPT_ATTRIBUTE, MAX_ATTACHMENT_BYTES, contentTypeFor, uploadToStorage } from "../lib/upload";
import { useChatActions } from "../store/chat";

type UploadItem = {
  key: string;
  file: File;
  progress: number;
  attachmentId?: number;
  error?: string;
};

const MAX_FILES = 10;

export function Composer({ room }: { room: Room }) {
  const actions = useChatActions();
  const [text, setText] = useState("");
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const update = (key: string, patch: Partial<UploadItem>) =>
    setUploads((list) => list.map((u) => (u.key === key ? { ...u, ...patch } : u)));

  /**
   * ファイルを選んだ時点でアップロードを始める（Slack と同じ）。送信ボタンを押す頃には終わっていることが多い。
   *   ① presign（Django: 権限・サイズ・形式をチェックし、署名付き POST を発行）
   *   ② ストレージへ直接 POST（Django を経由しない）
   *   ③ 送信時に attachment_ids としてメッセージに紐付ける
   */
  async function addFiles(files: FileList | File[]) {
    const list = [...files].slice(0, MAX_FILES - uploads.length);
    for (const file of list) {
      const key = `${file.name}-${file.size}-${Math.random()}`;
      const contentType = contentTypeFor(file);
      const item: UploadItem = { key, file, progress: 0 };
      if (!contentType) item.error = "この形式は送信できません（画像・PDF・Office・zip のみ）";
      else if (file.size > MAX_ATTACHMENT_BYTES) item.error = "50MB を超えるファイルは送信できません";
      setUploads((prev) => [...prev, item]);
      if (item.error || !contentType) continue;

      try {
        const presign = await Api.presignAttachment(room.id, { filename: file.name, content_type: contentType, size: file.size });
        await uploadToStorage(presign.upload, file, (progress) => update(key, { progress }));
        update(key, { progress: 1, attachmentId: presign.attachment_id });
      } catch (e) {
        update(key, { error: errorMessage(e) });
      }
    }
  }

  const ready = uploads.filter((u) => u.attachmentId !== undefined);
  const uploading = uploads.some((u) => u.attachmentId === undefined && !u.error);
  const canSend = !uploading && (text.trim().length > 0 || ready.length > 0);

  function submit(e?: FormEvent) {
    e?.preventDefault();
    if (!canSend) return;
    actions.send(room.id, text, ready.map((u) => ({ id: u.attachmentId!, name: u.file.name })));
    setText("");
    setUploads([]);
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    // 日本語入力の「変換確定の Enter」で送信してしまわないよう isComposing を確認する
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files.length) void addFiles(e.dataTransfer.files);
  }

  const placeholder = room.type === "channel" ? `#${room.display_name} へのメッセージ` : `${room.display_name} へのメッセージ`;

  return (
    <form
      className={`composer${dragging ? " dragging" : ""}`}
      onSubmit={submit}
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
    >
      {uploads.length > 0 && (
        <ul className="uploads">
          {uploads.map((u) => (
            <li key={u.key} className={u.error ? "error" : ""}>
              <span className="att-name">📎 {u.file.name}</span>
              <span className="muted small">{formatSize(u.file.size)}</span>
              {u.error ? (
                <span className="small">{u.error}</span>
              ) : u.attachmentId ? (
                <span className="small ok">✓</span>
              ) : (
                <progress max={1} value={u.progress} />
              )}
              <button type="button" className="icon" onClick={() => setUploads((l) => l.filter((x) => x.key !== u.key))}>
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="composer-row">
        <button type="button" className="icon attach" title="ファイルを添付" onClick={() => fileInput.current?.click()}>
          ＋
        </button>
        <input
          ref={fileInput}
          type="file"
          multiple
          hidden
          accept={ACCEPT_ATTRIBUTE}
          onChange={(e) => {
            if (e.target.files) void addFiles(e.target.files);
            e.target.value = "";
          }}
        />
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={placeholder}
          rows={1}
          maxLength={4000}
        />
        <button className="primary send" disabled={!canSend}>
          送信
        </button>
      </div>
      <div className="hint muted small">Enter で送信 / Shift+Enter で改行 / ファイルはドラッグ＆ドロップでも添付できます</div>
    </form>
  );
}
