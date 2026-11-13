# Step 7：フロントエンドの実装

React 19 + TypeScript + Vite 7 + React Router 7。依存ライブラリはこの 3 つだけで、状態管理は React 標準の `useReducer` ＋ Context を使う。

> バージョンについて：2026 年 11 月時点では Vite 8 / React Router 8 / TypeScript 7 も出ているが、情報が多く安定している 1 つ前のメジャー版を選んだ。

## 7-1. ディレクトリとレイヤー

```
frontend/src/
├─ api/
│  ├─ types.ts        REST と WS で共通の型（docs/03 の 3-7、docs/04 の 4-5）
│  ├─ client.ts       fetch ラッパー（Cookie・CSRF ヘッダ・401 の処理）
│  └─ endpoints.ts    API 関数の一覧
├─ ws/socket.ts       ★WebSocket の接続管理（ping、指数バックオフで再接続、4401）
├─ store/
│  ├─ auth.tsx        ログイン状態
│  ├─ chatReducer.ts  ★状態と、それを変える唯一の場所（純粋関数）
│  └─ chat.tsx        ★初回ロード → WS 接続 → 再接続時の sync、操作（送信・既読など）
├─ lib/
│  ├─ upload.ts       署名付き POST で S3 / MinIO に直接アップロード（進捗つき）
│  └─ format.ts       UTC → JST の表示、UUID
├─ components/        Sidebar / RoomView / MessageList / MessageItem / Composer / 各ダイアログ
├─ pages/AuthPage.tsx ログイン・サインアップ
└─ App.tsx / main.tsx ルーティング
```

| ファイル | 役割 | 既存知識での対応 |
|---|---|---|
| `api/client.ts` | HTTP 通信 | axios インスタンス ＋ インターセプタ |
| `ws/socket.ts` | 常時接続の管理 | （通常の CRUD には無い層） |
| `store/chatReducer.ts` | 状態遷移 | Redux の reducer |
| `store/chat.tsx` | 副作用（通信）と状態をつなぐ | Redux の thunk / saga、またはカスタムフック |

## 7-2. 状態設計の要点：2 系統の入力を 1 つにまとめる

```
                 ┌─ 初回ロード      GET /api/rooms, /api/presence
REST ────────────┼─ 過去ログ        GET /api/rooms/{id}/messages?before=
                 ├─ 自分の送信結果  POST の 201
                 └─ 再同期          GET /api/sync?since=   （hello を受けるたび）
                                             │
WebSocket ───── 他人の変更（message.created / presence.changed …）
                                             ▼
                               reducer（id で upsert、updated_at が新しいときだけ上書き）
                                             ▼
                                         1 つの state
```

| ルール | 理由 |
|---|---|
| メッセージは `messages[id]` に正規化し、ルームごとには `ids` だけを持つ | 同じメッセージが REST と WS の両方から来ても 1 件になる |
| `updated_at` が古いものでは上書きしない | 「編集」イベントが「作成」より後から届いても、データが巻き戻らない |
| ユーザーは `users[id]` に集約し、表示時はそちらを優先する | ステータスや表示名の変更（`user.updated`）を、過去のメッセージの表示にも反映する |
| オンライン状態は `online[id]` に分ける | 頻繁に変わる値を、ユーザー情報の本体と切り離す |
| `syncToken` は WS イベントの `ts` と sync の応答のうち新しい方 | 再接続時に「どこから取り直すか」の基準 |

reducer は純粋関数なので、「どの順で何が来たら、どういう状態になるか」を追いやすい（React の StrictMode は開発中に reducer を 2 回呼んで、副作用が無いかを確かめる）。

## 7-3. 接続と再同期（store/chat.tsx と ws/socket.ts）

```
ChatProvider のマウント
  ├ GET /api/rooms（sync_token も受け取る）と GET /api/presence
  └ ChatSocket.start()
      ├ onopen   → 25 秒ごとに {"type":"ping"}
      ├ hello    → GET /api/sync?since=<syncToken>    ← 初回ロード〜接続までの隙間も、再接続時の取りこぼしも、ここで埋まる
      ├ その他   → dispatch({type: "ws", event})
      └ onclose  → 4401 ならログイン画面へ。それ以外は 1s, 2s, 4s … 30s ＋ ±20% の揺らぎで再接続
                   （OS のネットワークが戻ったときは online イベントで即座に再接続）
```

## 7-4. 送信：楽観的 UI と冪等キー

1. 送信ボタン → `client_msg_id = crypto.randomUUID()` を付けて「送信中…」の仮メッセージを表示する
2. `POST /api/rooms/{id}/messages`
3. 201 が返る、または WS の `message.created` が届く（どちらが先でもよい）→ `client_msg_id` が一致する仮メッセージを確定メッセージで置き換える
4. 失敗したら「送信に失敗しました／再送信」。**再送信でも同じ `client_msg_id` を送る**ので、実は最初のリクエストがサーバーで成功していても、二重投稿にならない

## 7-5. ファイル添付（components/Composer.tsx、lib/upload.ts）

- ファイルを選んだ（またはドラッグ＆ドロップした）時点でアップロードを始める：presign → S3 / MinIO へ直接 POST（進捗バー）→ ✓
- 送信時は `attachment_ids` を付けるだけ
- 進捗を表示するために `XMLHttpRequest` を使う（`fetch` にはアップロード進捗の API が無い）
- Content-Type はブラウザの `file.type` ではなく拡張子から決める（OS によって空だったり、zip が `application/x-zip-compressed` になったりするため）
- 画像は `<img src="/api/attachments/{id}/download?inline=1">`。同一オリジンなので Cookie が付き、Django が権限を確認したうえで 302 を返し、ブラウザは MinIO / S3 から直接取得する

## 7-6. 細かいが大事な実装

| 箇所 | 内容 |
|---|---|
| 日本語入力 | `Enter` で送信するとき `e.nativeEvent.isComposing` を確認する。変換確定の Enter で送信されてしまう事故を防ぐ |
| 既読 | 表示中のルームで、タブが見えている（`visibilityState === "visible"`）ときだけ、最新の id を `POST /read` する。別タブから戻ったときにも既読にする |
| 未読バッジ | ミュート中のルームは数えない。合計をタブのタイトル `(3) Pyslack` に出す |
| 過去ログのスクロール | 上端に近づいたら `?before=` で取得する。追加する前の `scrollHeight` を覚えておき、`useLayoutEffect`（描画前）で差分だけ `scrollTop` を下げて、読んでいた位置を保つ |
| 最下部への追従 | 最下部にいたときだけ、新着で最下部へスクロールする。画像の読み込みで高さが変わったときも、`load` イベントを capture で拾って追従する |
| 時刻 | `Intl.DateTimeFormat` の `timeZone: "Asia/Tokyo"` で表示する。日付の区切り線も JST の日付で判定する |

## 7-7. ブラウザでの動作確認（2 ユーザー同時）

アプリ内ブラウザで `http://localhost:5173`（alice）と `http://127.0.0.1:5173`（bob）を開いた。Cookie はホスト名ごとに別なので、別ユーザーとして同時にログインできる。

| # | 確認内容 | 結果 |
|---|---|---|
| 1 | bob が #general に投稿 → alice の画面にリロードなしで表示される | ✅ |
| 2 | bob が DM を 2 通送る → alice のサイドバーに未読 2、タブのタイトルが `(2) Pyslack` | ✅ |
| 3 | alice が DM を開く → 未読が消える | ✅ |
| 4 | alice が編集 → bob の画面に「(編集済み)」付きで反映される／削除 →「削除されました」 | ✅ |
| 5 | 画像と PDF をドロップ → MinIO に直接アップロード → bob の画面に画像プレビューと PDF カード | ✅ |
| 6 | bob の WS を切断したまま、alice が投稿・編集・削除 → bob が再接続すると `/api/sync` ですべて反映される。切断中は「再接続しています…」と表示される | ✅ |
| 7 | alice がステータスを「📅 会議中」に変更 → bob の DM ヘッダとサイドバーに即時反映される | ✅ |
| 8 | alice がログアウト → bob の画面で alice がオフラインになる | ✅ |
| 9 | 120 件の過去ログ：最初は 50 件 → 上へスクロールで 100 → 121 件、「#general のはじまり」まで遡れる | ✅ |
| 10 | `tsc --noEmit` と `vite build` がエラーなしで通る | ✅ |
