import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
os.environ["REDIS_URL"] = "redis://localhost:6379/0"
os.environ["DATABASE_URL"] = \
    "postgresql+psycopg://stockbot:change-this-password@127.0.0.1:55432/stockbot"

print("a) import db", flush=True)
from bot.storage.db import SessionLocal, init_db  # noqa: E402
from bot.storage.models import Proposal           # noqa: E402
print("   settings.database_url host check:", flush=True)
from bot.config import settings                    # noqa: E402
print("   ->", settings.database_url, flush=True)

print("b) init_db()", flush=True)
init_db()
print("c) query pending", flush=True)
with SessionLocal() as s:
    rows = s.query(Proposal).filter(Proposal.status == "pending").all()
    print("   rows:", len(rows), flush=True)
    for p in rows:
        print(f"   #{p.id} {p.side} {p.symbol} {p.qty}", flush=True)
print("DONE", flush=True)
