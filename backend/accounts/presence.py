"""
オンライン状態（プレゼンス）を Redis で管理する（docs/04-websocket-events.md の 4-6）。

  presence:conn:{user_id}  ZSET  member = channel_name（WS 接続1本）, score = 期限（UNIX 秒）
  presence:online          SET   オンラインの user_id

「接続数を数えて 0 なら offline にする」を別々のコマンドで行うと、2 タブが同時に閉じたときなどに
判定が狂う。Lua スクリプトは Redis 上で割り込みなしに実行されるので、DB のトランザクションの代わりになる。
時刻も Redis サーバーの TIME を使い、プロセスごとの時計のずれの影響を受けないようにする。
"""

import asyncio
import weakref

import redis
import redis.asyncio as aioredis
from django.conf import settings

ONLINE_KEY = "presence:online"


def conn_key(user_id: int) -> str:
    return f"presence:conn:{user_id}"


# 接続を登録する／期限を延長する。オンラインになった（SET に新しく入った）なら 1 を返す
# KEYS[1]=conn zset, KEYS[2]=online set / ARGV[1]=channel_name, ARGV[2]=ttl, ARGV[3]=user_id
_TOUCH = """
local now = tonumber(redis.call('TIME')[1])
local ttl = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
redis.call('ZADD', KEYS[1], now + ttl, ARGV[1])
redis.call('EXPIRE', KEYS[1], ttl * 2)
return redis.call('SADD', KEYS[2], ARGV[3])
"""

# 接続を外し、期限切れも掃除する。接続が 0 本になってオフラインになったなら 1 を返す
# ARGV[1]=channel_name（掃除だけのときは空文字）, ARGV[2]=user_id
_RELEASE = """
local now = tonumber(redis.call('TIME')[1])
if ARGV[1] ~= '' then
  redis.call('ZREM', KEYS[1], ARGV[1])
end
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
if redis.call('ZCARD', KEYS[1]) == 0 then
  return redis.call('SREM', KEYS[2], ARGV[2])
end
return 0
"""

# ---------------------------------------------------------------------------
# Redis クライアント
#   非同期クライアントはイベントループに結び付くので、ループごとに作る
#   （Uvicorn ではループは 1 つだが、テストではテストごとにループが作り直される）
# ---------------------------------------------------------------------------
_async_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, tuple[str, aioredis.Redis]]" = (
    weakref.WeakKeyDictionary()
)
_sync_client: tuple[str, redis.Redis] | None = None


def _aclient() -> aioredis.Redis:
    loop = asyncio.get_running_loop()
    cached = _async_clients.get(loop)
    if cached is None or cached[0] != settings.REDIS_URL:
        cached = (settings.REDIS_URL, aioredis.Redis.from_url(settings.REDIS_URL, decode_responses=True))
        _async_clients[loop] = cached
    return cached[1]


def sync_client() -> redis.Redis:
    global _sync_client
    if _sync_client is None or _sync_client[0] != settings.REDIS_URL:
        _sync_client = (settings.REDIS_URL, redis.Redis.from_url(settings.REDIS_URL, decode_responses=True))
    return _sync_client[1]


# ---------------------------------------------------------------------------
# Consumer（非同期）から使う
# ---------------------------------------------------------------------------
async def connect(user_id: int, channel_name: str) -> bool:
    """接続を登録する。オンラインに変わったら True。"""
    result = await _aclient().eval(
        _TOUCH, 2, conn_key(user_id), ONLINE_KEY, channel_name, settings.PRESENCE_TTL_SECONDS, user_id
    )
    return result == 1


async def heartbeat(user_id: int, channel_name: str) -> bool:
    """ping のたびに期限を延長する。掃除役に一度オフラインにされていたら、オンラインに戻して True。"""
    return await connect(user_id, channel_name)


async def disconnect(user_id: int, channel_name: str) -> bool:
    """接続を外す。オフラインに変わったら True。"""
    result = await _aclient().eval(_RELEASE, 2, conn_key(user_id), ONLINE_KEY, channel_name, user_id)
    return result == 1


# ---------------------------------------------------------------------------
# View・管理コマンド（同期）から使う
# ---------------------------------------------------------------------------
def online_user_ids() -> set[int]:
    return {int(uid) for uid in sync_client().smembers(ONLINE_KEY)}


def sweep() -> list[int]:
    """期限切れの接続を掃除し、オフラインに変わった user_id を返す（presence_sweeper が呼ぶ）。"""
    client = sync_client()
    went_offline = []
    for uid in client.smembers(ONLINE_KEY):
        if client.eval(_RELEASE, 2, conn_key(int(uid)), ONLINE_KEY, "", uid) == 1:
            went_offline.append(int(uid))
    return went_offline
