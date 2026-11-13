# Step 4：WebSocket イベント仕様

## 4-1. 送信を REST POST にし、WebSocket で送らない理由

| 観点 | **REST POST → WS で配信（採用）** | WS で送信も行う |
|---|---|---|
| 結果の確定 | HTTP ステータス（201 / 400 / 404）で成否がはっきりする | 自前の ACK プロトコル（リクエスト id ↔ 応答の対応づけ）が必要 |
| バリデーション・認可 | DRF の Serializer / Permission / Throttle をそのまま使える | Consumer の中で書き直す必要がある |
| ミドルウェア | CSRF・セッション・ログなど Django の仕組みが全部効く | Django のミドルウェアを通らない |
| 失敗時の再送 | 冪等キー（`client_msg_id`）を付けて再送するだけ | 送信直後に切断されると「処理されたのか」が分からない → 結局 ACK と冪等キーが必要 |
| 添付ファイル | presign も REST なので、同じ流れの中で完結する | REST と WS が混ざる |
| テスト | `APIClient` で普通に書ける | `WebsocketCommunicator` で非同期テストが必要 |
| 遅延 | HTTP リクエスト1回分だけ多い（keep-alive が効くので数 ms 程度） | わずかに速い |
| Spring での対応 | `@PostMapping` ＋ `SimpMessagingTemplate.convertAndSend()` | STOMP の `@MessageMapping` |

**結論**：「書き込みも読み取りも REST、WebSocket は**サーバー → クライアントへの通知専用**」と役割を分ける。WS を流れるのは「変更があった」という知らせだけで、取りこぼしても `/api/sync` で回復できる。この単純さが、学習用途でも本番でも効いてくる（なお、この設計なら WS を SSE に置き換えることも可能）。

クライアント → サーバー方向の WS メッセージは、ハートビートの `ping` だけにする。

---

## 4-2. Channels の仕組み（ここだけは先に理解しておく）

```
               ┌───────────── Redis ─────────────┐
               │ group "room.42" = {ch_A1, ch_B1} │   ← group は channel_name の集合
               │ channel ch_A1 の受信箱 [....]     │   ← 接続ごとの受信キュー
               │ channel ch_B1 の受信箱 [....]     │
               └──────────────────────────────────┘
                     ▲ group_send             │ 各プロセスが自分の接続の受信箱を読む
POST を処理した View ─┘                        ▼
                                  ChatConsumer(ch_B1).chat_event() → ソケットへ書く
```

| 用語 | 意味 | 対応するもの |
|---|---|---|
| `channel_name` | **WS 接続1本ごと**に割り当てられる一意の受信箱の名前 | 接続ごとの専用キュー |
| group | channel_name の集合（Redis に保存される） | Pub/Sub のトピック |
| `group_add(group, channel_name)` | 自分の接続をトピックに登録する | subscribe |
| `group_send(group, {"type": "chat.event", ...})` | group 内の全受信箱へ配る | publish |
| `"type": "chat.event"` | 受け取った Consumer の **`chat_event` メソッド**が呼ばれる（`.` が `_` に変わる） | イベントハンドラのディスパッチ |

**注意点**：group の登録には有効期限がある（channels_redis の `group_expiry` は既定で 86,400 秒）。1日以上つなぎっぱなしの接続は、何も言われずに group から外れる。そこで、ハートビート処理の中で1時間ごとに `group_add` をやり直す。

---

## 4-3. 接続

```
URL:      ws(s)://<同一オリジン>/ws/
認証:     ハンドシェイク時の Cookie（sessionid）を AuthMiddlewareStack が読む
Origin:   AllowedHostsOriginValidator で検証（CSRF の代わり）
単位:     1タブ = 1接続（同じユーザーが複数接続を持てる）
```

ASGI のスタック（`config/asgi.py`）：

```python
application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AllowedHostsOriginValidator(        # ① Origin チェック
        AuthMiddlewareStack(                          # ② Cookie → scope["user"]
            URLRouter([path("ws/", ChatConsumer.as_asgi())])  # ③ ルーティング
        )
    ),
})
```

### 接続時の処理（`ChatConsumer.connect`）

```
1. scope["user"] が未ログインなら accept → close(code=4401)
     ※ accept 前に close すると HTTP 403 になり、ブラウザ側では理由の分からない 1006 になる。
       独自コードを伝えるために一度 accept する
2. group_add: user.{me}, presence, room.{id}（参加中の全ルーム）
3. accept
4. プレゼンスを登録（4-6）。接続数が 0 → 1 になったら presence.changed(online) を配信
5. hello イベントを送る
```

### 切断時の処理（`disconnect`）

```
1. 全 group から group_discard
2. プレゼンスを解除。接続数が 1 → 0 になったら presence.changed(offline) を配信
```

### クローズコード

| コード | 意味 | クライアントの動作 |
|---|---|---|
| 1000 | 正常終了（ログアウトなど） | 再接続しない |
| 1006 | 異常切断（ネットワーク断、サーバー再起動、Origin による拒否） | 再接続する（4-8） |
| 4401 | 未認証 | 再接続せずログイン画面へ |

---

## 4-4. メッセージの共通形式（エンベロープ）

```json
{
  "type": "message.created",
  "ts": "2026-11-05T05:10:00.123Z",
  "data": { }
}
```

| フィールド | 説明 |
|---|---|
| `type` | イベント名（`<対象>.<動作>`） |
| `ts` | サーバーがイベントを作った時刻（UTC）。クライアントはこれを `sync_token` として保存する（3-5） |
| `data` | 内容。Message / Room / UserSummary は **REST と同じ型**（3-7） |

### 「受信者ごとに違う値」を room 配信に入れない

`room.{id}` group への配信は、全員に**同じ JSON** が届く。そのため `is_mine` や `unread_count` のような受信者ごとに変わる値は入れない。

- `is_mine` → クライアントが `sender.id === me.id` で判定する
- 未読数 → クライアントが加算する（4-7）。ずれたら `/api/sync` で直る

受信者ごとの値が必要なイベント（`room.added`、`read.updated`）は `user.{id}` group に**個別に**送る。

---

## 4-5. イベント一覧（サーバー → クライアント）

| type | 送信先 group | data | きっかけ |
|---|---|---|---|
| `hello` | （その接続のみ） | `{user_id}` | 接続直後 |
| `message.created` | `room.{id}` | `Message` | POST /messages |
| `message.updated` | `room.{id}` | `Message` | PATCH /messages/{id} |
| `message.deleted` | `room.{id}` | `{id, room_id, deleted_at, updated_at}` | DELETE /messages/{id} |
| `read.updated` | `user.{me}` | `{room_id, last_read_message_id, unread_count}` | POST /rooms/{id}/read（自分の他のタブとの同期） |
| `room.added` | `user.{対象者}` | `Room`（その人から見た値） | ルーム作成・DM 作成・招待・参加 |
| `room.removed` | `user.{対象者}` | `{room_id}` | 退出・除外 |
| `room.updated` | `room.{id}` | `{id, name, display_name, is_private}` | PATCH /rooms/{id} |
| `room.member_joined` | `room.{id}` | `{room_id, user: UserSummary, member_count}` | 参加・招待 |
| `room.member_left` | `room.{id}` | `{room_id, user_id, member_count}` | 退出・除外 |
| `presence.changed` | `presence` | `{user_id, is_online}` | 接続数が 0↔1 に変化 |
| `user.updated` | `presence` | `{user: UserSummary}` | 表示名・ステータス・アバターの変更 |
| `pong` | （その接続のみ） | `{}` | `ping` への応答 |

### JSON の例

```json
{ "type": "hello", "ts": "2026-11-05T05:10:00.000Z", "data": { "user_id": 1 } }
```

```json
{
  "type": "message.created",
  "ts": "2026-11-05T05:10:01.234Z",
  "data": {
    "id": 1025,
    "room_id": 42,
    "sender": { "id": 2, "username": "bob", "display_name": "Bob", "avatar_url": null,
                "status_emoji": "🍵", "status_text": "休憩中", "is_online": true },
    "body": "資料を送ります",
    "attachments": [
      { "id": 77, "original_filename": "設計書.pdf", "content_type": "application/pdf",
        "size": 482113, "is_image": false,
        "download_url": "/api/attachments/77/download", "preview_url": null }
    ],
    "client_msg_id": "7c0e6a52-6a5c-4bd6-9b0e-0f8f3f7f2a11",
    "created_at": "2026-11-05T05:10:01.200Z",
    "updated_at": "2026-11-05T05:10:01.200Z",
    "edited_at": null,
    "deleted_at": null
  }
}
```

```json
{ "type": "message.deleted", "ts": "...",
  "data": { "id": 1025, "room_id": 42, "deleted_at": "2026-11-05T05:12:00.000Z",
            "updated_at": "2026-11-05T05:12:00.000Z" } }
```

```json
{ "type": "read.updated", "ts": "...",
  "data": { "room_id": 42, "last_read_message_id": 1025, "unread_count": 0 } }
```

```json
{ "type": "presence.changed", "ts": "...", "data": { "user_id": 2, "is_online": false } }
```

### クライアント → サーバー

```json
{ "type": "ping" }
```

25 秒ごとに送る。サーバーは `pong` を返し、プレゼンスの有効期限を延長する（4-6）。nginx の `proxy_read_timeout`（既定 60 秒）で、無通信の接続が切られるのを防ぐ役目もある。それ以外の `type` を受け取ったら、無視してログに残す。

---

## 4-6. プレゼンス（Redis）

### なぜ単純な「online フラグ」ではだめか

- 同じユーザーが2タブ開いていて1つ閉じても、オンラインのままであるべき → **接続数**を数える
- プロセスが落ちると `disconnect` が呼ばれず、「オンラインのまま」が残る → **有効期限**で自然に消す

### Redis のキー

```
presence:conn:{user_id}   ZSET  member = channel_name, score = 期限（UNIX 秒）
presence:online           SET   オンラインの user_id の集合（GET /api/presence 用）
```

| タイミング | 処理（Lua スクリプトで原子的に実行） | 配信 |
|---|---|---|
| connect | 期限切れを削除 → `ZADD conn now+60` → 件数が 1 なら `SADD online` | 0→1 なら online |
| ping | `ZADD conn now+60`（期限を延長） | なし |
| disconnect | `ZREM conn` → 期限切れを削除 → 件数が 0 なら `SREM online` | 1→0 なら offline |
| 掃除（30 秒ごと） | `online` の各ユーザーについて期限切れを削除 → 0 件なら `SREM` | offline |

**Lua スクリプトにする理由**：「件数を数える → 0 なら削除」を別々のコマンドで行うと、2タブが同時に閉じたときや、閉じると同時に開いたときに判定が狂う。Redis は Lua スクリプトを他のコマンドと割り込みなしで実行するので、DB のトランザクションと同じ役割を果たす。

**掃除役**：プロセスがクラッシュした場合の後始末として、`presence_sweeper` 管理コマンドを docker compose の別サービスで常駐させる（Step 5 / 6）。

---

## 4-7. クライアントの状態更新ルール

| イベント | 処理 |
|---|---|
| `message.created` | `client_msg_id` が一致する仮メッセージがあれば置き換え、なければ追加する。表示中のルームでなく、送信者が自分でなければ `unread_count += 1`。`last_message_at` を更新してルーム一覧を並べ替える |
| `message.updated` / `message.deleted` | id で探して、**`updated_at` が新しい場合だけ**上書きする（古いイベントが後から届いても巻き戻らない） |
| `read.updated` | そのルームの未読数と既読位置を上書きする |
| `room.added` | ルーム一覧に追加する（サーバー側では、その接続がすでに `room.{id}` group に入り直している） |
| `room.removed` | ルーム一覧から削除し、表示中ならホームへ移動する |
| `presence.changed` / `user.updated` | ユーザーマップ（id → UserSummary）を更新する。メッセージの送信者名は、このマップを優先して表示する |

**冪等にしておく**：同じイベントが2回届いても、`/api/sync` と重複しても、結果が変わらないようにする（id をキーにした upsert）。

---

## 4-8. 再接続と取りこぼしの補完

```
onclose (1006) ─▶ 1s, 2s, 4s, 8s … 最大 30s ＋ ランダムな揺らぎで再接続
                   （全員が同時に再接続してサーバーに負荷が集中するのを防ぐ）
onopen ─▶ hello を受信 ─▶ GET /api/sync?since=<sync_token>
                          ├ reset=false → rooms と messages を upsert
                          └ reset=true  → ルーム一覧と表示中ルームの最新ページを取り直す
```

`sync` の応答を待っている間に届いた WS イベントも、そのまま適用してよい。どちらも upsert なので、順序が前後しても最終的に同じ状態になる。

---

## 4-9. 動的な group 参加・離脱（room.added / room.removed）

接続中のユーザー C がチャンネル 42 に招待された場合：

```
招待 API（View）
  ├ RoomMember を作成
  └ on_commit:
      group_send("user.C",  {type:"chat.room_added", room_id:42, ...})   ← C の全タブへ
      group_send("room.42", member_joined)                                 ← 既存メンバーへ

C の各 Consumer.chat_room_added():
  1. DB で本当にメンバーか再確認する（イベントを鵜呑みにしない）
  2. group_add("room.42", self.channel_name)
  3. room.added をクライアントへ送る
```

除外された場合も同じ流れで、`user.{id}` 経由で `group_discard` させる。View から他人の接続を直接 group から外すことはできない（channel_name を知らないし、別プロセスにある）ため、**本人の Consumer に自分で外させる**。この処理が終わるまでのごく短い間はイベントが届く可能性があるので、Consumer は `room.{id}` 宛てのイベントを送る前に、自分が保持しているルーム集合に含まれているかを確認する。

---

## 4-10. Channel layer の設定

```python
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [REDIS_URL],
            "capacity": 1000,   # 1 受信箱あたりの最大滞留数（既定 100）。超えると送信側で ChannelFull
            "expiry": 60,       # 受信箱のメッセージの有効期限（秒）
        },
    }
}
```
