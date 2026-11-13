# Step 1：全体アーキテクチャとレイヤーの説明

## 1-1. なぜ通常のDjango CRUDの構成では足りないのか

普段の Django / Spring Boot の CRUD は、**HTTPのリクエスト・レスポンスモデル**で動いている。

```
ブラウザ ──GET /api/messages──▶ サーバー ──レスポンス──▶ 接続終了
```

HTTPでは、**サーバーから先に話しかけることができない**。A さんがメッセージを送っても、B さんのブラウザが問い合わせない限り、サーバーには B さんへ届ける手段がない。

| 手段 | 仕組み | 問題点 |
|---|---|---|
| ポーリング | `setInterval` で数秒ごとに GET | 遅延が出る。大半のリクエストが空振りで無駄になる |
| ロングポーリング | レスポンスを保留して待たせる | 実装が複雑。結局「接続を張りっぱなしにする」問題が残る |
| SSE | サーバー→クライアントの一方向ストリーム | 片方向だけ。今回の用途でも成立はする |
| **WebSocket** | HTTPで始めて `101 Switching Protocols` で**双方向の常時接続**に切り替える | **今回採用** |

WebSocket は、OSの知識で言えば「**TCPソケットを開いたままにし、どちらからでも書き込める**」状態。

ただし接続を開いたままにすると、新しい問題が2つ出てくる。これが ASGI と Redis が必要になる理由。

### 問題①：接続を保持し続けるとワーカーが足りなくなる → ASGI / Uvicorn

- 通常の Django は **WSGI**（gunicorn 等）で動く。1リクエストを1ワーカー（スレッド/プロセス）が担当し、レスポンスを返すと解放される方式。Spring Boot の **Tomcat（Servlet）の thread-per-request** と同じ。
- WSGI で WebSocket を100本張ると、**100ワーカーがほぼ何もせず待ち続けて**占有される。
- **ASGI** は非同期のインターフェースで、**1スレッドのイベントループが多数のソケットを並行して待てる**。OSで言えば `epoll` / `select`、Spring で言えば **WebFlux（Netty）** の世界。
- **Uvicorn** は ASGI サーバー。Tomcat の位置にあたるが、中身はイベントループ。

### 問題②：プロセスをまたいで配信できない → Channel Layer（Redis）

本番ではワーカープロセスが複数ある。次のような状況が普通に起こる。

```
A さんの WebSocket ─── プロセス1
B さんの WebSocket ─── プロセス2
A さんの POST /messages ── プロセス3 が処理
```

プロセス3は、プロセス1・2が持っている**ソケットに直接触れない**（別メモリ空間なので）。そのため**プロセス間メッセージバス**が必要になり、それが Channels の **channel layer（実体は Redis の Pub/Sub）**。Spring で言えば、STOMP の SimpleBroker を外部ブローカー（RabbitMQ）にリレーする構成と同じ発想。

## 1-2. 全体アーキテクチャ図

```
┌────────────────────── ブラウザ（React + TypeScript） ──────────────────────┐
│   REST クライアント(fetch + Cookie)    WebSocket クライアント(自動再接続)      │
└─────────┬──────────────────────────────────┬──────────────────┬────────────┘
          │ HTTP  /api/*                      │ WS  /ws/          │ HTTP PUT/GET
          ▼                                   ▼                   │（署名付きURLで直接）
┌───────────────────────────────────────────────────┐            │
│  リバースプロキシ：開発=Vite proxy / 本番=nginx       │            │
│  "/"→フロント  "/api"→backend  "/ws"→backend(Upgrade)│            │
└─────────┬──────────────────────────────────┬──────┘            │
          ▼                                   ▼                   ▼
┌──────────────── Uvicorn（ASGIサーバー） ────────────────┐  ┌──────────────┐
│  ProtocolTypeRouter（プロトコルで振り分け）              │  │ MinIO / S3   │
│   ├ http      → Django → DRF View（@RestController相当）│  │ ファイル本体   │
│   └ websocket → OriginValidator → AuthMiddleware        │  └──────────────┘
│                 → URLRouter → ChatConsumer              │         ▲
│                    （接続ごとに1インスタンス・長寿命）   │         │ boto3
│                                                         │─────────┘(署名URLの
└──────┬───────────────────────────┬──────────────────────┘  生成だけ)
       │ ORM（同期）                 │ group_send / group_add
       ▼                            ▼
┌──────────────┐            ┌──────────────────────────────┐
│ PostgreSQL   │            │ Redis                         │
│ 唯一の真実    │            │ ① channel layer（プロセス間バス）│
│ (メッセージ等) │            │ ② プレゼンス（接続数 + TTL）     │
└──────────────┘            └──────────────────────────────┘
```

## 1-3. 各コンポーネントが担うレイヤー

| コンポーネント | 担うレイヤー | 既存知識での対応 |
|---|---|---|
| React | UI・クライアント状態。REST と WS の**2系統の入力**を1つの状態にマージする | 通常の React SPA ＋「サーバーから勝手にデータが届く」経路 |
| Vite proxy / nginx | リバースプロキシ。パスで振り分け、`/ws` では `Upgrade` ヘッダを中継する | nginx 経験そのもの。WS には `proxy_set_header Upgrade` が**明示的に必要** |
| Uvicorn | ASGI サーバー（ネットワーク I/O とイベントループ） | Tomcat の位置づけ。中身は Netty 型 |
| ProtocolTypeRouter | HTTP と WebSocket の入口を分ける | DispatcherServlet のさらに手前 |
| DRF View | REST の業務処理（保存・認可・バリデーション） | `@RestController` / 通常の Django CRUD とまったく同じ |
| Channels Consumer | WS 1接続のライフサイクル（connect / receive / disconnect）。group に入り、届いたイベントをソケットに書き出す | Spring の `WebSocketHandler`。**View と違い、接続中ずっと生きているオブジェクト** |
| Channel layer（channels_redis） | プロセス間のメッセージバス。`group` ＝ Pub/Sub のトピック | メッセージブローカー / OS の IPC |
| Redis（プレゼンス） | 揮発性で書き込み頻度の高い状態。TTL で自然に消える | セッションキャッシュ。DB に書くと、クラッシュ時に「オンラインのまま」が残る |
| PostgreSQL | **唯一の真実**。メッセージ・既読位置・メンバーシップ | いつもどおり |
| MinIO / S3 | ファイル本体の保管。Django は**署名するだけでバイトを中継しない** | S3 経験そのもの。Django の FileField / media 配信との違いに注意 |

## 1-4. 通常の Django CRUD との違い

| 観点 | 通常の Django CRUD | 今回 |
|---|---|---|
| サーバー | gunicorn（WSGI） | **Uvicorn（ASGI）** |
| 接続 | 短命（1リクエストで終わる） | 短命の HTTP ＋ **長命の WS** の併用 |
| サーバーの状態 | ステートレス | 「どの接続がどの group にいるか」を**各プロセスが保持** → channel layer が必要 |
| データの流れ | クライアントが pull | サーバーが **push** |
| ミドルウェア | Django ミドルウェア（CSRF・セッション） | **WS には Django ミドルウェアが効かない**。ASGI ミドルウェア（`AuthMiddlewareStack`）で Cookie セッションを読み、CSRF の代わりに `AllowedHostsOriginValidator` で Origin をチェック |
| 同期/非同期 | 全部同期 | ORM は同期のまま。Consumer（非同期）からは `database_sync_to_async`、View（同期）から配信するときは `async_to_sync(group_send)` で**境界をまたぐ** |
| ファイル | Django が受信して保存 | ブラウザ → S3 に直接 PUT（Django は署名URLの発行と権限チェックのみ） |

## 1-5. 代表的な処理の流れ

### (a) メッセージ送信（REST POST → WS 配信）

```
A のブラウザ ─POST /api/rooms/42/messages─▶ DRF View
                                             ├ 認証(セッション) / メンバー認可
                                             ├ Message を INSERT（トランザクション）
                                             └ transaction.on_commit(
                                                  group_send("room.42", message.created))
                                                         │
                                                         ▼ Redis Pub/Sub
                                 各プロセスの ChatConsumer（room.42 に参加中のもの）
                                                         │
                       B のブラウザ ◀── WS: {"type":"message.created", ...}
A のブラウザ ◀── 201 Created（自分の画面は REST レスポンスで確定）
```

`on_commit` を使うのは、**コミット前に配信すると「通知は届いたのに DB にはまだ無い（ロールバックされた）」状態が起きうる**ため。

### (b) ファイル送信

```
1. POST /api/rooms/42/attachments/presign  → サイズ・形式・メンバー権限をチェックし、
                                            Attachment(pending) を作成して PUT 用署名URLを返す
2. ブラウザ ─PUT─▶ MinIO/S3（Django を経由しない）
3. POST /api/rooms/42/messages { body, attachment_ids:[...] } → 紐付けて配信
4. ダウンロード: GET /api/attachments/{id}/download → メンバー権限チェック → 短期限の GET 署名URL
```

### (c) 再接続時の取りこぼし補完

```
WS 切断 → 指数バックオフで再接続 → onopen で
GET /api/rooms/{id}/messages?after=<最後に受信した message_id> を叩いて差分をマージ
```

**基本原則：DB が唯一の真実で、WebSocket は「変更があったよ」という通知にすぎない**（届かなくても REST で回復できる）。この原則が「送信は REST POST」を選ぶ根拠になる。WS 送信方式との詳細な比較は Step 4 で扱う。

## 1-6. 要件に対する補足（後続ステップの前提）

1. **「1ユーザー1接続」は実際には「1タブ1接続」。** 同じユーザーが2タブ開くと接続は2本になる。そのため、プレゼンスはオン/オフのフラグではなく、Redis で**ユーザーごとの接続数**（channel_name の集合＋TTL ハートビート）を数える。0→1 で online、1→0 で offline を配信する。
2. **group は3種類。**
   - `room.{id}`：ルームのメンバーへの配信用
   - `user.{id}`：本人の全タブ宛。接続中に新しいルームに追加されたとき、Consumer が `group_add` し直すための通知や、別タブとの既読同期に使う
   - `presence`：全体配信用。100人規模なら全員に配信して問題ない
3. **データモデルへの指摘**（DM の重複防止、Attachment を紐付けるタイミング、`last_read_message_id` の型など）は Step 2 で扱う。
