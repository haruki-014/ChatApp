# Step 6：バックエンドの実装

## 6-1. ディレクトリとレイヤー

```
backend/
├─ config/
│  ├─ settings.py        設定（すべて環境変数から）
│  ├─ settings_test.py   pytest 用（InMemory channel layer、Redis は DB 15）
│  ├─ asgi.py            ★入口: HTTP → Django / WebSocket → Origin検証 → セッション認証 → Consumer
│  └─ urls.py
├─ accounts/
│  ├─ models.py          User（email ログイン）
│  ├─ authentication.py  401 を返す SessionAuthentication、ログイン時の CSRF 強制
│  ├─ presence.py        ★オンライン状態（Redis + Lua）
│  ├─ serializers.py / views.py / urls.py   認証・ユーザー・プロフィール・アバター
│  └─ management/commands/presence_sweeper.py   期限切れ接続の掃除（常駐）
└─ chat/
   ├─ models.py          Room / RoomMember / Message / Attachment
   ├─ permissions.py     ★認可「ルームのメンバーか」（REST と WS で共通）
   ├─ serializers.py     入出力の型（REST と WS イベントで共通の JSON）
   ├─ services.py        ★業務ロジック（DB 更新 → コミット後に配信）
   ├─ events.py          ★View（同期）→ channel layer（非同期）への配信
   ├─ consumers.py       ★WebSocket 1 接続 = 1 インスタンス
   ├─ routing.py         WebSocket 版 urls.py
   ├─ storage.py         S3 / MinIO（署名・HEAD・削除だけ）
   ├─ views.py / urls.py REST API
   └─ management/commands/  seed_demo（デモデータ）, cleanup_attachments（孤児の掃除）
```

| ファイル | レイヤー | Spring Boot での対応 |
|---|---|---|
| `views.py` | プレゼンテーション（HTTP） | `@RestController` |
| `serializers.py` | 入力検証・DTO | `@Valid` 付きの DTO ＋ Jackson |
| `permissions.py` | 認可 | `@PreAuthorize` / `AccessDecisionVoter` |
| `services.py` | ビジネスロジック・トランザクション境界 | `@Service` ＋ `@Transactional` |
| `events.py` | ドメインイベントの配信 | `ApplicationEventPublisher` ＋ `@TransactionalEventListener(AFTER_COMMIT)` ＋ `SimpMessagingTemplate` |
| `consumers.py` | WebSocket の接続ハンドラ | `WebSocketHandler` |
| `presence.py` | 揮発性の状態（Redis） | `RedisTemplate` ＋ Lua スクリプト |
| `storage.py` | 外部ストレージ | `S3Presigner` / `S3Client` |

## 6-2. メッセージ送信の流れ（コードの対応）

```
POST /api/rooms/42/messages
  └ views.MessageListCreateView.post
      ├ permission_classes = [IsAuthenticated, IsRoomMember]   … 401 / 404
      ├ MessageCreateSerializer                                 … 本文 or 添付が必須、冪等キー
      └ services.post_message
          ├ client_msg_id が既にあれば既存を返す（200）           … 二重投稿防止
          ├ storage.head_object で添付の実在と実サイズを確認       … ロックの外で通信する
          └ transaction.atomic():
              ├ Room.objects.select_for_update().get(pk=42)       … 同じルームへの投稿を直列化
              ├ Message.objects.create(...)
              ├ 添付を紐付け（select_for_update で取り合いを防ぐ）
              ├ room.last_message_at を更新
              └ events.broadcast_room(42, "message.created", data)
                  └ transaction.on_commit(…, robust=True)          … コミット後に実行
                      └ async_to_sync(channel_layer.group_send)("room.42", {"type": "chat.event", …})
                                                                    ▼ Redis
各プロセスの ChatConsumer.chat_event() → self.send_json(event) → ブラウザ
```

## 6-3. 学習ポイント

### ① 同期と非同期の境界

| 呼び出し元 | 呼び出し先 | 使うもの |
|---|---|---|
| 同期の View | 非同期の `group_send` | `async_to_sync(layer.group_send)(...)`（events.py） |
| 非同期の Consumer | 同期の ORM | `database_sync_to_async(fn)`（consumers.py） |

Django の ORM は同期 API なので、Consumer（イベントループ上）から直接呼ぶと `SynchronousOnlyOperation` になる。`database_sync_to_async` はスレッドプールで実行し、終わったら DB 接続を片付けてくれる。

### ② `transaction.on_commit` と `robust=True`

- コミット前に配信すると、ロールバック時に「存在しないメッセージの通知」が届いてしまう。
- `robust=True` は、配信が失敗しても（Redis の一時的な断など）コミット済みのリクエストを 500 にしないため。取りこぼした通知は `/api/sync` で回復できる、という設計（4-1）だからこそ選べる判断。

### ③ View は他人の接続を group に出し入れできない

招待や除外では、本人の `user.{id}` group に `chat.room_added` / `chat.room_removed` を送り、**本人の Consumer が DB を確認してから** `group_add` / `group_discard` する（consumers.py の `chat_room_added`）。

### ④ Consumer の `connect` で一度 accept してから閉じる

未ログインのときは `accept()` → `close(4401)` とする。accept 前に閉じると HTTP 403 になり、ブラウザには理由の分からない 1006 しか届かない。

### ⑤ プレゼンスの Lua スクリプト

`accounts/presence.py` の `_TOUCH` / `_RELEASE`。「接続を ZSET に追加・削除 → 期限切れを掃除 → 0 本ならオフラインにする」を、Redis 上で割り込みなしに実行する。時刻も Redis サーバーの `TIME` を使うので、プロセス間の時計のずれの影響を受けない。

### ⑥ is_online の N+1 を避ける

`UserSummarySerializer.get_is_online` は、1 リクエストにつき 1 回だけ `SMEMBERS presence:online` を呼び、結果を Serializer の context（ネストした Serializer とも共有される dict）に置く。

## 6-4. テスト

```bash
docker compose run --rm --no-deps backend pytest
# 64 passed
```

| ファイル | 内容 |
|---|---|
| `test_auth.py` | サインアップ／ログイン（大文字小文字、ログイン CSRF）、401、プロフィール更新の配信、アバターキーの検証 |
| `test_rooms.py` | チャンネル名の重複、グループの条件、**DM の一意性（逆向きでも同じ）**、未読数と並び順、既読は巻き戻らない、参加時は未読 0、ロール |
| `test_messages.py` | 非メンバー 404 / 未ログイン 401、**コミット後に配信**、冪等キー、**カーソルのページング（新着が来てもずれない）**、編集・論理削除、`/api/sync`（重なり幅、reset） |
| `test_attachments.py` | POST ポリシーのサイズ条件、形式・拡張子・サイズの検証、パスの除去、未アップロードの拒否、使い回しの禁止、302 リダイレクト、非メンバー・削除済みの拒否 |
| `test_presence.py` | 2 タブ → 1 タブ → 0 タブの判定、掃除役 |
| `test_consumers.py` | **WebsocketCommunicator** による結合テスト：4401、Origin 拒否、ping/pong、メンバーだけに届く、プレゼンス配信、**接続中のルーム追加・除外**、既読の他タブ同期 |

- channel layer はテストではインメモリ版（`settings_test.py`）。プレゼンスは Lua を使うので本物の Redis（DB 15）を使う。
- REST のテストでは `chat.events._group_send` を差し替えて「何をどの group に送ったか」を記録し、`django_capture_on_commit_callbacks` で on_commit を実行する。

## 6-5. 実スタックでのスモークテスト（Uvicorn + Redis channel layer + MinIO）

ホストから Python スクリプトで以下を確認した（すべて OK）。

1. ログイン → `GET /api/rooms`
2. WebSocket 接続 → `hello`
3. presign → **MinIO への直接 POST（204）**。`Access-Control-Allow-Origin: http://localhost:5173` が返る
4. メッセージ投稿 → **別ユーザーの WS に Redis 経由で `message.created`**。添付のサイズは HEAD の実測値
5. 別ユーザーがダウンロード → 302 → MinIO から同じバイト列（日本語ファイル名は `filename*` で復元）
6. 編集・削除 → `message.updated` / `message.deleted`
7. 5MB 上限のアバターに 6MB を送る → **MinIO 自体が 400 EntityTooLarge で拒否**
8. 非メンバー 404、ping/pong、接続中は online・切断後は offline、不正な Origin は 403
