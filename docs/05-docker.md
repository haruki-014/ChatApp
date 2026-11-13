# Step 5：Docker Compose 構成

ファイル：
[docker-compose.yml](../docker-compose.yml) /
[.env.example](../.env.example) /
[backend/Dockerfile](../backend/Dockerfile) /
[backend/requirements.txt](../backend/requirements.txt) /
[frontend/Dockerfile](../frontend/Dockerfile) /
[nginx/nginx.conf](../nginx/nginx.conf) /
[nginx/Dockerfile](../nginx/Dockerfile) /
[docker-compose.prod.yml](../docker-compose.prod.yml)

## 5-1. 開発環境のサービス構成

```
            ホストのブラウザ
   ┌────────────┼──────────────────────┐
   │ :5173      │                       │ :9000（署名付き URL で直接）
   ▼            │                       ▼
┌──────────┐    │               ┌──────────────┐
│ frontend │ /api, /ws を proxy │ minio        │  :9001 管理コンソール
│ (Vite)   │────┐               └──────────────┘
└──────────┘    ▼                      ▲ minio:9000（HEAD/DELETE）
          ┌──────────┐                 │
          │ backend  │─────────────────┘
          │ (Uvicorn)│──────┐
          └──────────┘      │
               │            │
               ▼            ▼
          ┌────────┐   ┌────────┐   ┌──────────────────┐
          │ db     │   │ redis  │◀──│ presence-sweeper │
          │ (PG17) │   │        │   └──────────────────┘
          └────────┘   └────────┘
```

| サービス | イメージ | 役割 | 既存知識との対応 |
|---|---|---|---|
| `frontend` | node:22-alpine | Vite の開発サーバー。`/api` と `/ws` を backend に**中継**し、ブラウザから見て同一オリジンにする | 開発中の nginx 代わり |
| `backend` | python:3.12-slim | Uvicorn ＋ Django。起動時に `migrate` を実行する | Spring Boot アプリ本体 |
| `presence-sweeper` | backend と同じ | 30 秒ごとにプレゼンスを掃除する常駐プロセス（4-6） | `@Scheduled` のバッチ |
| `db` | postgres:17-alpine | 唯一の真実 | RDS |
| `redis` | redis:7-alpine | channel layer ＋ プレゼンス | ElastiCache |
| `minio` | bitnamilegacy/minio（固定） | S3 互換ストレージ。起動時にバケットを自動作成する | S3 |

### 起動順序

`depends_on: condition: service_healthy` により、db・redis・minio の **healthcheck が通ってから** backend が起動する。単なる `depends_on` は「コンテナが起動した」順序しか保証せず、「PostgreSQL が接続を受け付けられる」状態までは待たない。そのため、起動直後に `migrate` が接続エラーで落ちる問題を防ぐ。

## 5-2. ハマりどころと対策

### ① 署名付き URL のホスト名（重要）

```
backend コンテナから見た MinIO :  http://minio:9000      ← Docker の内部 DNS
ブラウザから見た MinIO        :  http://localhost:9000  ← minio という名前は解決できない
```

SigV4 の署名付き GET URL は **Host も署名に含まれる**ので、あとから URL の文字列を置換すると署名が合わなくなる。そこで boto3 のクライアントを2つ用意する（Step 6 の `chat/storage.py`）。

| クライアント | endpoint | 用途 |
|---|---|---|
| internal | `AWS_S3_ENDPOINT_URL=http://minio:9000` | サーバー自身が呼ぶ操作（HEAD、DELETE） |
| public | `AWS_S3_PUBLIC_ENDPOINT_URL=http://localhost:9000` | ブラウザに渡す URL の**署名だけ**を行う（署名はローカルの計算なので、このホストに接続できなくてもよい） |

本番ではどちらも空にし、boto3 の既定（本物の S3）を使う。

### ② MinIO 公式イメージの配布停止

2025 年に MinIO がコミュニティ版のビルド済みイメージの配布をやめ、`minio/minio` は Docker Hub から取得できなくなった。そこで Bitnami が凍結した `bitnamilegacy/minio:2025.7.23-debian-12-r5` を**タグを固定して**使う。セキュリティ更新はないが、localhost でしか使わない開発環境なので許容する。`MINIO_DEFAULT_BUCKETS` でバケットが自動作成されるので、`mc` による初期化コンテナは不要。

### ③ django-storages は使わない

django-storages は「`FileField` の保存先を S3 にする」ためのライブラリ。今回のアップロードは**ブラウザから S3 に直接**行い、Django は署名と HEAD だけを担当するので、`FileField` を一切使わない。boto3 を直接使うほうが、何をしているかが見えて学習にも向いている。

### ④ node_modules を名前なしボリュームで隠す

`./frontend:/app` を bind mount すると、ホスト（macOS）の `node_modules` がコンテナ（Linux）の `node_modules` を上書きする。esbuild などネイティブバイナリを含むパッケージは OS ごとにバイナリが違うので、これで壊れる。`- /app/node_modules` を書くと、その場所だけコンテナ内のものが優先される。

### ⑤ Vite proxy で Host を書き換えない

Vite の proxy で `changeOrigin: false`（既定値）にすると、backend には `Host: localhost:5173` がそのまま届く。ブラウザが送る `Origin: http://localhost:5173` と一致するので、Django の CSRF チェックと Channels の `AllowedHostsOriginValidator` の両方が素直に通る（設定は Step 7 の `vite.config.ts`）。

## 5-3. backend の Dockerfile（マルチステージ）

| ステージ | 内容 |
|---|---|
| `base` | `requirements.txt` だけを先に COPY して pip install する（ソースを変えても依存のレイヤーキャッシュが効く） |
| `dev` | テスト用の依存を追加する。ソースは bind mount で渡し、`--reload` で反映する |
| `prod` | ソースをイメージに焼き込み、`collectstatic` を実行する。root 以外のユーザーで動かし、`--workers 2` で起動する |

**`--workers 2` でも正しく動く理由**：各ワーカーは別プロセスで、WebSocket 接続はどちらかに属する。それでも配信は Redis の channel layer を経由するので、どのワーカーで POST が処理されても全員に届く（1-1 の問題②）。

## 5-4. 本番構成（AWS）

```
Route53 → ALB (HTTPS / ACM証明書, idle timeout 60s)
            │ :80
            ▼
     EC2 ── nginx ─┬─ /            → React のビルド成果物
                   ├─ /api /admin /static → backend:8000
                   └─ /ws          → backend:8000（Upgrade を中継）
            backend ─┬─ RDS PostgreSQL
                     ├─ ElastiCache Redis（rediss:// で TLS）
                     └─ S3（IAM ロールで認証。アクセスキーは置かない）
```

| 項目 | 内容 |
|---|---|
| nginx の要点 | `/ws/` では `proxy_http_version 1.1`、`Upgrade` / `Connection` ヘッダの中継、`proxy_read_timeout 120s`。`client_max_body_size 1m`（ファイルは S3 に直接送るため） |
| ALB | WebSocket に標準で対応している。idle timeout（既定 60 秒）より短い 25 秒間隔の ping で接続を維持する |
| S3 の認証 | EC2 のインスタンスプロファイル（IAM ロール）を使う。`.env.prod` に `AWS_ACCESS_KEY_ID` を**書かない**と、boto3 が自動でロールを使う。必要な権限：`s3:PutObject` / `s3:GetObject` / `s3:DeleteObject`（`attachments/*` と `avatars/*`） |
| S3 の CORS | ブラウザから直接 POST するため、バケットに CORS を設定する（下記） |
| マイグレーション | デプロイ手順の中で `docker compose run --rm backend python manage.py migrate` を明示的に実行する（ワーカー起動時に並行実行させない） |
| 費用を抑える場合 | ElastiCache の代わりに、EC2 上で redis コンテナを動かしてもよい（100 人規模なら十分） |

```json
[
  {
    "AllowedOrigins": ["https://chat.example.com"],
    "AllowedMethods": ["POST", "GET"],
    "AllowedHeaders": ["*"],
    "ExposeHeaders": ["ETag"],
    "MaxAgeSeconds": 3000
  }
]
```

## 5-5. 環境変数

| 変数 | 開発の値 | 本番 |
|---|---|---|
| `DJANGO_SECRET_KEY` | ダミー | Secrets Manager / SSM から注入する |
| `DJANGO_DEBUG` | 1 | 0 |
| `DJANGO_ALLOWED_HOSTS` | localhost,127.0.0.1 | chat.example.com |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | http://localhost:5173 | https://chat.example.com |
| `POSTGRES_*` | compose の db | RDS のエンドポイント |
| `REDIS_URL` | redis://redis:6379/0 | rediss://...cache.amazonaws.com:6379/0 |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | MinIO の root ユーザー | **設定しない**（IAM ロール） |
| `AWS_S3_ENDPOINT_URL` / `AWS_S3_PUBLIC_ENDPOINT_URL` | minio:9000 / localhost:9000 | **設定しない**（本物の S3） |

## 5-6. このステップで確認したこと／まだできないこと

- ✅ `docker compose config` で、開発用・本番用の両方の compose ファイルの構文を確認した
- ✅ `requirements.txt` と `requirements-dev.txt` が同時にインストールできることを確認した（Django 5.2.19 / DRF 3.16.1 / channels 4.3.2 / channels_redis 4.3.0 / uvicorn 0.54 / psycopg 3.3.8 / redis 6.4 / boto3 1.43 / pytest 9.2）
- ⏳ `docker compose up` はまだできない：`config/`・`manage.py`（Step 6）と `package.json`（Step 7）がまだないため
- ⏳ `nginx -t` による構文チェックは、Docker デーモンが動いている環境で Step 8 に実施する
