# Step 8：動作確認の手順

## 8-1. 起動

前提：Docker Desktop が起動していること。

```bash
cp .env.example .env
docker compose up --build
```

初回はイメージのビルドに数分かかる。`frontend-1 | VITE ... ready` と表示されたら準備完了。

別のターミナルでデモデータを作る（何度実行しても同じ状態になる）。

```bash
docker compose exec backend python manage.py seed_demo
```

| アカウント | パスワード |
|---|---|
| alice@example.com | pyslack-demo-2026 |
| bob@example.com | pyslack-demo-2026 |
| carol@example.com | pyslack-demo-2026 |

`#general`（alice・bob・carol）と、alice ↔ bob の DM が作られる。

| URL | 内容 |
|---|---|
| http://localhost:5173 | アプリ（**普段はここだけを使う**） |
| http://localhost:8000/admin | Django 管理画面（`docker compose exec backend python manage.py createsuperuser` で管理者を作る） |
| http://localhost:9001 | MinIO コンソール（`.env` の `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` でログイン） |

> `http://localhost:8000` を直接開いてアプリを使わないこと。フロントと API が別オリジンになり、Cookie や CSRF の前提が崩れる。

## 8-2. 2 つのブラウザでリアルタイム反映を確認する

**なぜ 2 つのブラウザが必要か**：ログイン状態は Cookie（`sessionid`）で決まり、Cookie は「ブラウザのプロファイル × ホスト名」ごとに 1 つしか持てない。同じウィンドウで 2 タブ開いても、同じユーザーになってしまう。

| 方法 | ユーザー A | ユーザー B |
|---|---|---|
| ① 通常ウィンドウ ＋ シークレットウィンドウ（おすすめ） | Chrome の通常ウィンドウで http://localhost:5173 | Chrome のシークレットウィンドウで http://localhost:5173 |
| ② 別のブラウザ | Chrome | Safari / Firefox |
| ③ ホスト名を変える | http://localhost:5173 | http://127.0.0.1:5173（MinIO の CORS にも許可を追加済み） |

2 つのウィンドウを左右に並べ、片方に alice、もう片方に bob でログインする。

### チェックリスト（MVP 要件 1〜8）

| # | 要件 | 手順 | 期待する結果 |
|---|---|---|---|
| 1 | DM | bob：サイドバーの「Alice」を開き、メッセージを送る | alice の画面に、**リロードなしで**表示される |
| 2 | チャンネル | alice：「チャンネル」の ＋ →「チャンネルを作成」で `#dev` を作り、メンバーに bob を入れる → 投稿する | bob のサイドバーに `#dev` が**接続したまま**現れ（`room.added`）、投稿も届く |
| 2' | グループ | alice：「ダイレクトメッセージ」の ＋ で bob と carol を選び、グループを作成する | 3 人のグループができる |
| 3 | ファイル | alice：入力欄の ＋ またはドラッグ＆ドロップで、画像と PDF を添付して送信する | 進捗バー → ✓ → 送信。bob の画面に画像のプレビューと PDF のカードが表示され、クリックでダウンロードできる |
| 3' | ファイルの制限 | 50MB を超えるファイルや .exe を添付する | 入力欄にエラーが表示され、送信できない |
| 4 | リアルタイム | 1〜3 のすべて | 相手の画面がリロードなしで更新される |
| 5 | 履歴 | 下の「8-4. 大量の履歴」でメッセージを増やし、上へスクロールする | 50 件ずつ読み込まれ、読んでいた位置が飛ばない。最後に「#general のはじまり」と表示される |
| 6 | 未読数 | bob が #general を開いている間に、alice が DM を数通送る | bob のサイドバーの「Alice」に赤いバッジ、タブのタイトルに `(n) Pyslack`。DM を開くと消える |
| 6' | ミュート | bob：#general のヘッダの 🔔 でミュートする | 未読があってもバッジが出ない |
| 7 | 編集・削除 | 自分のメッセージにマウスを乗せ「編集」（Enter で保存）／「削除」 | 相手の画面に「(編集済み)」／「このメッセージは削除されました」。他人のメッセージには編集・削除ボタンが出ない |
| 8 | プレゼンス | alice のウィンドウを閉じる（またはログアウトする） | bob の画面で alice の ● が ○（オフライン）になる。開き直すと ● に戻る |
| 8' | ステータス | alice：左下の自分の名前 → 「📅 会議中」→ 保存 | bob の画面の DM ヘッダとサイドバーに 📅 が即座に出る |

> 日本語入力の確認：変換中に Enter を押しても送信されず、確定後の Enter で送信されること。

## 8-3. 切断・再接続（取りこぼしの補完）を確認する

もっとも確実な方法：**bob のウィンドウだけ WebSocket が繋がらない状態**を作り、その間に alice が操作する。

1. bob のウィンドウで DevTools → Console に次を貼り付ける（以降に作られる WebSocket を、繋がらない先へ向ける）

   ```js
   const Orig = window.WebSocket; window.__block = true;
   function W(url, p) { return new Orig(window.__block ? "ws://127.0.0.1:9/x" : url, p); }
   W.prototype = Orig.prototype; Object.assign(W, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
   window.WebSocket = W;
   ```

2. ターミナルで backend を再起動し、全員の WebSocket を切る

   ```bash
   docker compose restart backend
   ```

3. bob のサイドバーに「再接続しています…」と表示される。alice は普段どおり再接続されるので、その間に alice が **投稿・編集・削除** を行う（bob の画面は変わらない）
4. bob の Console で `window.__block = false` を実行する
5. 数秒以内（バックオフの待ち時間後）に bob が再接続し、`hello` → `GET /api/sync` で、**切断中の投稿・編集・削除がすべて反映される**

別の方法：Chrome の DevTools → Network →「Offline」でも試せる。ただし、ブラウザのバージョンによっては、既存の WebSocket が切れないことがある。

## 8-4. 大量の履歴を作る

```bash
docker compose exec backend python manage.py shell -c "
from accounts.models import User
from chat import services
from chat.models import Room
carol = User.objects.get(username='carol'); g = Room.objects.get(type='channel', name='general')
for i in range(1, 121):
    services.post_message(room_id=g.id, sender=carol, body=f'履歴テスト {i:03d}', attachment_ids=[], client_msg_id=None)
"
```

bob が #general を開いていれば 120 件がリアルタイムで流れてくる。開いていなければ未読が 120 になる。ページを再読み込みしてから #general を開くと、最新の 50 件だけが読み込まれ、上へスクロールすると過去分が読み込まれる。

## 8-5. 中で何が起きているかを見る

| 見たいもの | 方法 |
|---|---|
| WebSocket で流れている JSON | DevTools → Network → `ws/` → **Messages** タブ（25 秒ごとの ping/pong、`message.created` など） |
| オンラインのユーザー | `docker compose exec redis redis-cli SMEMBERS presence:online` |
| ユーザー 1 の接続（タブ）と期限 | `docker compose exec redis redis-cli ZRANGE presence:conn:1 0 -1 WITHSCORES` |
| channel layer の中身 | `docker compose exec redis redis-cli MONITOR`（投稿すると `asgi:group:room.1` などへの操作が見える。Ctrl+C で終了） |
| 実行される SQL | `docker compose exec db psql -U pyslack -c "SELECT id, room_id, left(body, 20), deleted_at FROM chat_message ORDER BY id DESC LIMIT 5"` |
| アップロードされたファイル | http://localhost:9001 → バケット `pyslack` → `attachments/` |
| サーバーのログ | `docker compose logs -f backend presence-sweeper` |

## 8-6. 自動テスト

```bash
docker compose run --rm --no-deps backend pytest        # 64 件（REST・WebSocket・プレゼンス）
docker compose exec frontend npm run build              # 型チェック＋本番ビルド
```

本番用イメージのビルドと nginx の設定チェック：

```bash
cp .env.example .env.prod
docker compose -f docker-compose.prod.yml build
docker run --rm --add-host backend:127.0.0.1 pyslack-prod-nginx nginx -t
rm .env.prod
```

## 8-7. うまくいかないとき

| 症状 | 原因と対処 |
|---|---|
| `port is already allocated` | 5173 / 8000 / 5432 / 6379 / 9000 / 9001 のどれかが使用中。該当するプロセスを止めるか、`docker-compose.yml` の左側のポートを変える |
| ログインすると 403 | `http://localhost:8000` を直接開いている。http://localhost:5173 から使う |
| WebSocket がすぐ切れる（1006）を繰り返す | backend のログを確認する。`DJANGO_ALLOWED_HOSTS` に、アクセスしているホスト名（localhost / 127.0.0.1）が入っているか |
| ファイルのアップロードだけ失敗する | ブラウザのコンソールに CORS エラーが出ていれば、`MINIO_API_CORS_ALLOW_ORIGIN` に開いている URL を追加して `docker compose up -d minio` |
| 画像が表示されない | MinIO が起動しているか（`docker compose ps`）。署名付き URL のホストは `.env` の `AWS_S3_PUBLIC_ENDPOINT_URL` |
| データを全部消してやり直したい | `docker compose down -v`（DB・MinIO のボリュームごと削除）→ `docker compose up` → `seed_demo` |

## 8-8. 実施済みの確認（2026-11-05）

- pytest 64 件すべて成功（3 回連続で実行し、不安定なテストが無いことも確認）
- 実スタック（Uvicorn ＋ Redis channel layer ＋ MinIO）に対するスモークテスト：ログイン、WS 接続、MinIO への直接アップロード（CORS ヘッダあり）、Redis 経由の配信、302 → 署名付き GET、編集・削除の配信、5MB 超を MinIO が 400 で拒否、非メンバーは 404、ping/pong、オンライン → オフライン、不正な Origin は 403
- ブラウザ 2 ユーザーでの確認：docs/07-frontend.md の 7-7 の 10 項目すべて
- 本番イメージのビルド、`nginx -t` の成功、backend が root 以外（`app` ユーザー）で動くこと
