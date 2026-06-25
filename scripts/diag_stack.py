"""로컬 스택 단계별 진단 — 어디서 막히는지 출력."""
import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("DATABASE_URL",
                      "postgresql+psycopg://stockbot:change-this-password@localhost:55432/stockbot")

print("1) config", flush=True)
from bot.config import settings  # noqa: E402
print("   redis_url:", settings.redis_url, " db:", settings.database_url[:45], flush=True)

print("2) redis ping", flush=True)
import redis  # noqa: E402
r = redis.from_url(settings.redis_url, socket_connect_timeout=5)
print("   ping:", r.ping(), flush=True)

print("3) postgres connect", flush=True)
from sqlalchemy import create_engine, text  # noqa: E402
e = create_engine(settings.database_url, connect_args={"connect_timeout": 5})
with e.connect() as c:
    print("   select1:", c.execute(text("select 1")).scalar(), flush=True)

print("4) toss token (redis-cached)", flush=True)
from bot.brokers import get_broker  # noqa: E402
b = get_broker("toss")
print("   usdkrw:", b.usdkrw(), flush=True)

print("5) toss balance", flush=True)
bal = b.get_balance()
print("   cash:", bal.cash, " positions:", len(bal.positions), flush=True)
print("DONE", flush=True)
