# Pyslack

Slack 風のリアルタイムチャット（学習・ポートフォリオ用）。

- DM・グループ・チャンネルでのメッセージの送受信（リロードなしでリアルタイムに反映）
- ファイルの送付（S3 / MinIO へブラウザから直接アップロード、画像はプレビュー表示）
- 過去ログの遡り（カーソル方式のページネーション）、ルームごとの未読数
- 自分のメッセージの編集・削除、オンライン状態とカスタムステータス
- 切断・再接続時の取りこぼし補完

| レイヤー | 技術 |
|---|---|
| Frontend | React 19 + TypeScript + Vite 7 + React Router 7 |
| REST API | Django 5.2 + Django REST Framework |
| WebSocket | Django Channels 4（channel layer は Redis） |
| ASGI サーバー | Uvicorn |
| DB | PostgreSQL 17 |
| オンライン状態 | Redis（Lua スクリプト） |
| ファイル | 開発: MinIO / 本番: AWS S3（boto3 で署名付き URL を発行） |

## クイックスタート

```bash
cp .env.example .env
docker compose up --build
docker compose exec backend python manage.py seed_demo   # 別ターミナルで
```

http://localhost:5173 を開き、`alice@example.com` / `pyslack-demo-2026` でログインする。
別のブラウザ（またはシークレットウィンドウ）で `bob@example.com` としてログインすると、リアルタイムに反映される様子を確認できる。

## ドキュメント

| # | 内容 |
|---|---|
| [01](docs/01-architecture.md) | 全体アーキテクチャと各レイヤーの役割（通常の Django CRUD との違い） |
| [02](docs/02-data-model.md) | ER 図とデータモデル（DM の重複防止、カーソルと採番順の問題） |
| [03](docs/03-rest-api.md) | REST API（セッションと JWT の比較、カーソル方式のページネーション、署名付き URL） |
| [04](docs/04-websocket-events.md) | WebSocket イベント仕様（REST 送信を選んだ理由、group 設計、プレゼンス） |
| [05](docs/05-docker.md) | Docker Compose 構成と本番構成（nginx / AWS） |
| [06](docs/06-backend.md) | バックエンドの実装とテスト |
| [07](docs/07-frontend.md) | フロントエンドの実装 |
| [08](docs/08-verification.md) | 動作確認の手順（2 つのブラウザでの確認、切断・再接続） |

## テスト

```bash
docker compose run --rm --no-deps backend pytest
```
