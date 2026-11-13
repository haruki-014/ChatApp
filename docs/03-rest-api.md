# Step 3：REST API エンドポイント一覧

## 3-0. 共通ルール

| 項目 | 内容 |
|---|---|
| ベースパス | `/api/`（開発では Vite proxy、本番では nginx が backend に転送するので、フロントから見て同一オリジン） |
| 形式 | JSON。時刻は ISO 8601 の UTC（`2026-11-05T05:00:00.123Z`） |
| 認証 | Django セッション（Cookie `sessionid`、HttpOnly、SameSite=Lax） |
| CSRF | 書き込み系（POST/PATCH/DELETE）では、Cookie `csrftoken` の値をヘッダ `X-CSRFToken` に入れて送る |
| 未認証 | **401**（DRF の SessionAuthentication は標準では 403 を返すので、Step 6 で 401 を返すよう調整する） |
| 非メンバー | **404**。403 を返すと「その id の非公開ルームが存在する」ことが漏れるため |
| 権限不足 | 403（例：他人のメッセージの編集、owner/admin 以外による設定変更） |
| エラー形式 | DRF 標準：`{"detail": "..."}` またはフィールド単位の `{"body": ["..."]}` |

### 認可の考え方

DRF の Permission クラスで **「ルームのメンバーか」** をチェックする（Step 6 の `IsRoomMember`）。Spring Security の `@PreAuthorize("@roomGuard.isMember(#roomId)")` に相当する。WebSocket 側も同じ判定関数を使い、ロジックを二重に書かないようにする。

---

## 3-1. 認証：セッション（Cookie）を採用し、JWT は使わない理由

| 観点 | セッション＋Cookie（採用） | JWT |
|---|---|---|
| 状態の置き場所 | サーバー（DB の `django_session`）。Cookie にはランダムな ID だけを入れる | トークン自体に署名付きでユーザー情報を入れる（ステートレス） |
| 強制ログアウト・失効 | セッション行を消せば即座に無効になる | 有効期限まで有効。即時失効には拒否リスト（結局サーバー状態）が必要 |
| XSS 耐性 | HttpOnly なので JS から読めない | localStorage に置くと XSS で盗まれる |
| CSRF | 対策が必要（CSRF トークン） | `Authorization` ヘッダで送るなら不要 |
| **WebSocket** | ハンドシェイクで Cookie が**自動で送られる** → `AuthMiddlewareStack` がそのまま読める | ブラウザの `WebSocket` API は**任意のヘッダを付けられない**。クエリ文字列 `?token=`（アクセスログに残る）や「接続後の最初のメッセージで認証」などの工夫が要る |
| 向いている場面 | 同一オリジンの SPA ＋ API | 複数サービス間、モバイルアプリ、別ドメインの API |
| Spring での対応 | Spring Security 標準の `HttpSession`（JSESSIONID） | stateless な JWT フィルタ |

フロントと API を同一オリジンにする（開発は Vite proxy、本番は nginx）ので、セッション方式の弱点（CORS とサードパーティ Cookie）が問題にならない。そのため単純で安全なセッション方式を選ぶ。

**WebSocket で CSRF の代わりになるもの：** WS のハンドシェイクには CSRF トークンを付けられず、攻撃者のサイトからでも Cookie 付きで接続を試みられる（Cross-Site WebSocket Hijacking）。そこで Channels の `AllowedHostsOriginValidator` で `Origin` ヘッダを検証する（Step 4 / 6）。

| メソッド | パス | 説明 | 認証 |
|---|---|---|---|
| GET | `/api/auth/csrf` | `csrftoken` Cookie を発行する（SPA 起動時に最初に呼ぶ） | 不要 |
| POST | `/api/auth/signup` | `{email, username, password, display_name}` → 201、そのままログイン状態にする | 不要（CSRF は必要） |
| POST | `/api/auth/login` | `{email, password}` → 200 `User`。セッション ID を再発行する（セッション固定攻撃対策） | 不要（CSRF は必要） |
| POST | `/api/auth/logout` | 204 | 要 |
| GET | `/api/auth/me` | ログイン中のユーザー → 200 `User`／未ログインなら 401 | 要 |

ログインにも CSRF を要求するのは、攻撃者のアカウントで被害者をログインさせる「ログイン CSRF」を防ぐため。

---

## 3-2. ユーザー・プレゼンス

| メソッド | パス | 説明 |
|---|---|---|
| GET | `/api/users?q=ali&limit=20` | ユーザー検索（DM の開始やメンバー招待に使う）。`is_active=True` のユーザーのみ |
| GET | `/api/users/{id}` | ユーザー詳細 |
| PATCH | `/api/users/me` | `{display_name?, status_emoji?, status_text?, avatar_key?}` → `user.updated` を WS で全体に配信。`avatar_key` は自分用のプレフィックス `avatars/{自分のid}/` で始まるものだけ受け付ける |
| POST | `/api/users/me/avatar/presign` | アバター画像のアップロード用署名（3-6 と同じ仕組み。画像のみ・5MB まで）→ `{avatar_key, upload}`。アップロード後に PATCH で `avatar_key` を設定する |
| GET | `/api/users/{id}/avatar` | 302 で署名付き URL へリダイレクト（`<img src>` にそのまま書ける） |
| GET | `/api/presence` | `{"online_user_ids": [1, 5, 9]}`。初回表示用のスナップショット。以降の変化は WS の `presence.changed` で受け取る |

---

## 3-3. ルーム

| メソッド | パス | 説明 | 認可 |
|---|---|---|---|
| GET | `/api/rooms` | 自分が参加しているルームの一覧（未読数つき、`last_message_at` の降順）。100人規模なのでページネーションしない。`sync_token` も返す（3-5） | 要ログイン |
| POST | `/api/rooms` | グループ DM またはチャンネルを作成する。`{type: "group"\|"channel", name?, is_private?, member_ids: []}` → 201。作成者は owner になる | 要ログイン |
| POST | `/api/rooms/dm` | `{user_id}` → 既存の DM があれば 200、新規作成なら 201（2-3 の `get_or_create_dm`） | 要ログイン。自分自身は 400 |
| GET | `/api/channels` | 公開チャンネルの一覧（参加前に探す用）。各項目に `is_member` を付ける | 要ログイン |
| GET | `/api/rooms/{id}` | ルームの詳細 | メンバー |
| PATCH | `/api/rooms/{id}` | `{name?}`。channel と group のみ | owner / admin |
| POST | `/api/rooms/{id}/join` | 公開チャンネルに参加する。既読位置を参加時点の最新 id にする | 公開チャンネルのみ |
| POST | `/api/rooms/{id}/leave` | 退出する（DM は不可） | メンバー |
| GET | `/api/rooms/{id}/members` | メンバー一覧（`role` と `is_online` つき） | メンバー |
| POST | `/api/rooms/{id}/members` | `{user_ids: []}` で追加する。DM は不可 | メンバー |
| DELETE | `/api/rooms/{id}/members/{user_id}` | メンバーを外す | owner / admin |
| PATCH | `/api/rooms/{id}/membership` | 自分の設定を変える `{is_muted}` | メンバー |
| POST | `/api/rooms/{id}/read` | `{last_read_message_id}` → 既読位置を更新し、`read.updated` を自分の他のタブに配信する | メンバー |

**既読の更新は「増える方向だけ」にする**：`UPDATE ... SET last_read_message_id = GREATEST(last_read_message_id, :id)`。複数タブから前後して届いても、既読位置が巻き戻らない。

メンバーの追加・退出・除外が起きたら、WS の `room.member_joined` / `room.member_left` を配信する。追加されたユーザー本人には `user.{id}` group 経由で `room.added` を送り、その接続が `room.{id}` group に参加し直す（Step 4）。

---

## 3-4. メッセージ

| メソッド | パス | 説明 | 認可 |
|---|---|---|---|
| GET | `/api/rooms/{id}/messages?before=<id>&limit=50` | 過去ログ（3-4-1）。`before` を省略すると最新から取得する | メンバー |
| GET | `/api/rooms/{id}/messages?after=<id>&limit=50` | 指定 id より新しいメッセージ（未読位置からの表示用） | メンバー |
| POST | `/api/rooms/{id}/messages` | `{body, attachment_ids?: [], client_msg_id}` → 201 `Message`。コミット後に `message.created` を配信する | メンバー |
| PATCH | `/api/messages/{id}` | `{body}` → 200。`edited_at` を設定し、`message.updated` を配信する | 送信者本人かつ現メンバー。削除済みは 409 |
| DELETE | `/api/messages/{id}` | 論理削除（body を空にする）→ 204。`message.deleted` を配信する | 送信者本人かつ現メンバー |

- `body` は最大 4,000 文字。`body` が空で添付もない場合は 400。添付は最大 10 件。
- `client_msg_id`：クライアントが送信前に生成する UUID。**同じ値で再送されたら、既存のメッセージを 200 で返す**（二重投稿しない）。通信が切れて 201 を受け取れず、再送するケースへの備え（Stripe の `Idempotency-Key` と同じ考え方）。
- 送信処理の中身（2-5 の再掲）：`select_for_update` でルームの行をロック → INSERT → `last_message_at` を更新 → `transaction.on_commit` で配信。

### 3-4-1. カーソル方式のページネーション（オフセット方式を使わない理由）

```http
GET /api/rooms/42/messages?before=1024&limit=50
```

```sql
SELECT ... FROM chat_message
WHERE room_id = 42 AND id < 1024        -- (room_id, id) インデックスで目的の位置へ直接飛べる
ORDER BY id DESC
LIMIT 51;                                -- 1件多めに取り、has_more を判定する
```

| 観点 | `?page=3`（OFFSET） | `?before=<id>`（カーソル） |
|---|---|---|
| 性能 | `OFFSET 10000` は 10,000 行を読んで捨てるので、深く遡るほど遅くなる | インデックスで目的の位置へ直接飛ぶので、どこまで遡っても一定 |
| **データの増減** | 過去ログを読んでいる間に新着が来ると、ページの境界がずれて**同じメッセージが重複**する。削除されると**1件読み飛ばす** | 「この id より前」という基準は動かないので、ずれない |
| 総件数・ページ番号 | 出せる | 出せない（無限スクロールなので不要） |
| Spring Data での対応 | `Pageable` / `Page` | `Slice`、`KeysetScrollPosition`（Spring Data 3.1+） |

チャットは「先頭（最新側）にデータが増え続ける」ので、オフセット方式とは根本的に相性が悪い。

**レスポンス**（`results` は**古い順**に並べて返す。クライアントは配列の先頭に連結するだけで済む）：

```json
{
  "results": [ { "id": 975, "...": "..." }, { "id": 1023, "...": "..." } ],
  "has_more": true,
  "next_before": 975
}
```

---

## 3-5. 再同期（WebSocket 再接続時の取りこぼし対策）

当初の案「最後に受け取った message_id 以降を REST で取得」をベースに、次の2点を補強する。

1. **編集・削除の取りこぼし**：id は増えないので `after=` では拾えない → `Message.updated_at` で拾う。
2. **ルームごとにリクエストが必要**：参加ルームが20あると20回呼ぶことになる → 1回のリクエストで全ルーム分をまとめて返す。

```http
GET /api/sync?since=<sync_token>
```

```json
{
  "sync_token": "2026-11-05T05:10:00.000Z",
  "reset": false,
  "rooms": [ { "...Room（未読数つき）": "" } ],
  "messages": [ { "...Message（作成・編集・削除されたもの）": "" } ]
}
```

- サーバーは `updated_at > since - 10秒` で検索する（`(room_id, updated_at)` インデックスを使う）。**10秒戻す**のは、「採番やタイムスタンプの時刻」と「コミットの時刻」がずれて、境界にあるメッセージを取りこぼすのを防ぐため（2-5 と同じ問題の時刻版）。
- 重複して返ってくることはあり得るが、クライアントは **id をキーにして、`updated_at` が新しいほうで上書き（upsert）** するので問題ない。
- 変更が 500 件を超えたら `reset: true` を返し、クライアントはルーム一覧と表示中ルームの最新ページを取り直す（長時間スリープしていた PC など）。
- `sync_token` の初期値は `GET /api/rooms` が返す。それ以降は、WS イベントに付いている `ts` と、sync のレスポンスのうち新しいほうを保存しておく。

---

## 3-6. ファイル

### 3-6-1. アップロード：署名付き URL による直接アップロード（Django 経由の multipart は使わない理由）

| 観点 | Django 経由の multipart（`FileField`） | **署名付き URL で S3 に直接（採用）** |
|---|---|---|
| データの経路 | ブラウザ → nginx → Uvicorn → Django → S3（同じ 50MB が2回流れる） | ブラウザ → S3（Django は数百バイトの JSON だけ扱う） |
| サーバー負荷 | アップロード中はワーカーが占有される。ASGI では大きなボディの受信がイベントループを圧迫する | ほぼゼロ |
| 上限設定 | nginx の `client_max_body_size`、Django の `DATA_UPLOAD_MAX_MEMORY_SIZE` など複数箇所 | S3 のポリシー条件で強制できる |
| 実装の手間 | 少ない（いつもの Django） | 少し多い（3段階） |
| Spring での対応 | `MultipartFile` を受け取って `s3Client.putObject` | `S3Presigner` |

**PUT 用の署名ではなく POST ポリシーにする理由**：署名付き PUT ではファイルサイズの上限を S3 側で強制できない。署名付き POST（`generate_presigned_post`）なら `content-length-range` 条件を付けられ、**50MB を超えるファイルは S3 自体が拒否する**。

| メソッド | パス | 説明 | 認可 |
|---|---|---|---|
| POST | `/api/rooms/{id}/attachments/presign` | `{filename, content_type, size}` → 201（下記）。Attachment を pending で作成する | メンバー |
| GET | `/api/attachments/{id}/download` | 302 で署名付き GET URL（5分有効、`Content-Disposition: attachment; filename*=UTF-8''...`）へリダイレクト | メンバーかつメッセージが削除されていない（pending の場合はアップロードした本人のみ） |
| GET | `/api/attachments/{id}/download?inline=1` | 画像プレビュー用（`Content-Disposition: inline`）。画像の場合だけ許可する | 同上 |

```json
// POST /api/rooms/42/attachments/presign のレスポンス
{
  "attachment_id": 77,
  "upload": {
    "url": "http://localhost:9000/pyslack",
    "fields": { "key": "attachments/42/3f2b...", "Content-Type": "image/png", "policy": "...", "x-amz-signature": "..." }
  },
  "expires_in": 300
}
```

クライアントは `fields` と最後に `file` を入れた `FormData` を `upload.url` に POST し、成功したら `POST /api/rooms/42/messages` に `attachment_ids: [77]` を付けて投稿する。投稿時にサーバーが S3 に HEAD を送り、実在と実サイズを確認する。

**許可する形式**（拡張子と Content-Type の両方をチェックする）：

| 種類 | Content-Type |
|---|---|
| 画像 | `image/png`, `image/jpeg`, `image/gif`, `image/webp`（**SVG は除外**。中にスクリプトを埋め込めるため） |
| PDF | `application/pdf` |
| Office | `application/msword`, `application/vnd.openxmlformats-officedocument.wordprocessingml.document`, `application/vnd.ms-excel`, `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, `application/vnd.ms-powerpoint`, `application/vnd.openxmlformats-officedocument.presentationml.presentation` |
| zip | `application/zip`, `application/x-zip-compressed`（Windows のブラウザはこちらを送ってくる） |

**画像プレビューを `<img src="/api/attachments/77/download?inline=1">` にする理由**：同一オリジンなのでセッション Cookie が付き、Django が権限をチェックしたうえで 302 を返し、ブラウザが MinIO/S3 からそのまま画像を取得する。署名付き URL を JSON で受け取って差し込む方式より、フロントが簡単になる。

---

## 3-7. レスポンスの型（WS イベントでも同じ型を使う）

```ts
type UserSummary = {
  id: number;
  username: string;
  display_name: string;          // 空の場合は username を入れて返す
  avatar_url: string | null;     // "/api/users/1/avatar"
  status_emoji: string;
  status_text: string;
  is_online: boolean;            // Redis から取得（DB には持たない）
};

type Attachment = {
  id: number;
  original_filename: string;
  content_type: string;
  size: number;                  // バイト数
  is_image: boolean;
  download_url: string;          // "/api/attachments/77/download"
  preview_url: string | null;    // 画像の場合のみ "...?inline=1"
};

type Message = {
  id: number;
  room_id: number;
  sender: UserSummary;
  body: string;                  // 削除済みなら ""
  attachments: Attachment[];     // 削除済みなら []
  client_msg_id: string | null;
  created_at: string;
  updated_at: string;
  edited_at: string | null;
  deleted_at: string | null;
};

type Room = {
  id: number;
  type: "dm" | "group" | "channel";
  name: string | null;
  display_name: string;          // DM なら相手の名前、名前のないグループならメンバー名を連結
  is_private: boolean;
  dm_partner: UserSummary | null;
  member_count: number;
  last_message_at: string | null;
  // 以下はリクエストしたユーザー自身のメンバー情報
  role: "owner" | "admin" | "member";
  unread_count: number;
  last_read_message_id: number;
  is_muted: boolean;
};
```

---

## 3-8. 一覧（早見表）

```
認証       GET  /api/auth/csrf          POST /api/auth/signup|login|logout     GET /api/auth/me
ユーザー   GET  /api/users              GET  /api/users/{id}                   PATCH /api/users/me
           POST /api/users/me/avatar/presign                                    GET /api/users/{id}/avatar
プレゼンス GET  /api/presence
ルーム     GET  /api/rooms              POST /api/rooms                        POST /api/rooms/dm
           GET  /api/channels           GET|PATCH /api/rooms/{id}
           POST /api/rooms/{id}/join    POST /api/rooms/{id}/leave
           GET|POST /api/rooms/{id}/members                                     DELETE /api/rooms/{id}/members/{uid}
           PATCH /api/rooms/{id}/membership                                     POST /api/rooms/{id}/read
メッセージ GET|POST /api/rooms/{id}/messages                                    PATCH|DELETE /api/messages/{id}
同期       GET  /api/sync?since=
ファイル   POST /api/rooms/{id}/attachments/presign                             GET /api/attachments/{id}/download
```
