from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from bot.config import settings
from bot.storage.models import Base

# connect_timeout: 잘못된 호스트/IPv6 등으로 무한 대기 방지(방어적)
engine = create_engine(settings.database_url, pool_pre_ping=True,
                       connect_args={"connect_timeout": 10})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
