"""DB 모델 — 체결/주문 로그, 일일 스냅샷."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, Float, Integer, DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class OrderLog(Base):
    __tablename__ = "order_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    broker: Mapped[str] = mapped_column(String(16))
    mode: Mapped[str] = mapped_column(String(8))      # paper | live
    symbol: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(4))
    qty: Mapped[int] = mapped_column(Integer)
    price: Mapped[float] = mapped_column(Float, default=0)
    ok: Mapped[bool] = mapped_column(default=False)
    order_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason: Mapped[str] = mapped_column(String(128), default="")
    message: Mapped[str] = mapped_column(String(256), default="")


class Proposal(Base):
    """반자동 모드: 봇이 낸 주문 제안. 사람이 승인(approve)해야 실행된다."""
    __tablename__ = "proposal"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    broker: Mapped[str] = mapped_column(String(16))
    mode: Mapped[str] = mapped_column(String(8))       # paper | live
    symbol: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(4))
    qty: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(4), default="KRW")
    ref_price: Mapped[float] = mapped_column(Float, default=0)  # 제안 시점 시세
    reason: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(12), default="pending")
    # pending | approved | rejected | executed | failed
    order_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    message: Mapped[str] = mapped_column(String(256), default="")


class DailySnapshot(Base):
    __tablename__ = "daily_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    cash: Mapped[float] = mapped_column(Float)
    total_eval: Mapped[float] = mapped_column(Float)


class PaperSnapshot(Base):
    """모의 자동매매 성적표 — 매 실행 시 모의계좌 총평가 기록."""
    __tablename__ = "paper_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    cash: Mapped[float] = mapped_column(Float)
    total_eval: Mapped[float] = mapped_column(Float)
    holdings: Mapped[int] = mapped_column(Integer, default=0)


class AssetSnapshot(Base):
    """실계좌 자산 흐름 — 전체 + 계좌별(토스·ISA·연금) 시계열. 매일 장마감 기록."""
    __tablename__ = "asset_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    total_krw: Mapped[float] = mapped_column(Float)
    toss_krw: Mapped[float] = mapped_column(Float, default=0)
    kis_krw: Mapped[float] = mapped_column(Float, default=0)
    isa_krw: Mapped[float] = mapped_column(Float, default=0)
    pension_krw: Mapped[float] = mapped_column(Float, default=0)


class NewsIssue(Base):
    """뉴스 감성 이슈 — 장중 정기 배치(KR3·US3/일)로 종목별 점수·극성·요약을 적재.
    날짜별 히스토리(RAG 평가 '이슈 히스토리')·대시보드 이슈요약의 단일 출처."""
    __tablename__ = "news_issue"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    date: Mapped[str] = mapped_column(String(10), index=True)   # YYYY-MM-DD (KST)
    session: Mapped[str] = mapped_column(String(16), default="")  # KR-오전 / US-후반 등
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    market: Mapped[str] = mapped_column(String(4), default="US")
    score: Mapped[float] = mapped_column(Float, default=0)        # [-1,1] 부정~긍정
    polarity: Mapped[str] = mapped_column(String(8), default="중립")  # 긍정 | 부정 | 중립
    confidence: Mapped[float] = mapped_column(Float, default=0)
    summary: Mapped[str] = mapped_column(String(600), default="")
    sources: Mapped[int] = mapped_column(Integer, default=0)
    # 이슈 대비 실제 주가 반응 — 배치 시점 단기 수익률 + 반영여부 판정
    ret_1d: Mapped[float | None] = mapped_column(Float, nullable=True)   # 1거래일 %
    ret_5d: Mapped[float | None] = mapped_column(Float, nullable=True)   # 5거래일(≈1주) %
    impact: Mapped[str] = mapped_column(String(16), default="")          # 반영/역행/소화/횡보 등


class Prediction(Base):
    """자기예측추적 — 예측을 기록하고 만기 후 실제와 대조해 '실측 정확도'를 산출."""
    __tablename__ = "prediction"

    id: Mapped[int] = mapped_column(primary_key=True)
    made_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    symbol: Mapped[str] = mapped_column(String(16))
    horizon_days: Mapped[int] = mapped_column(Integer)
    base_price: Mapped[float] = mapped_column(Float)
    prob_up: Mapped[float] = mapped_column(Float)
    exp_return: Mapped[float] = mapped_column(Float)       # 기대수익률(%)
    p50: Mapped[float] = mapped_column(Float)              # 종가 중앙값 예측
    p10: Mapped[float] = mapped_column(Float)
    p90: Mapped[float] = mapped_column(Float)
    backtest_winrate: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 평가(만기 후 채움)
    status: Mapped[str] = mapped_column(String(10), default="open")  # open|evaluated
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    actual_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    dir_hit: Mapped[bool | None] = mapped_column(nullable=True)   # 방향 적중
    band_hit: Mapped[bool | None] = mapped_column(nullable=True)  # P10~P90 안에 들었나
