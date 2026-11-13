# 開発用（Vite dev server）。本番ではこのイメージは使わず、nginx/Dockerfile でビルド成果物だけを配信する
FROM node:22-alpine
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
EXPOSE 5173
# --host 0.0.0.0: コンテナの外（ホストのブラウザ）から接続できるようにする
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
