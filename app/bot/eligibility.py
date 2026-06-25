"""계좌별 매수가능 판정 — 종목 태그(securityType·market·leverageFactor)로 규칙 판정.

하드코딩 리스트 대신 증권사 종목정보를 받아 '연금/ISA/일반 가능 여부'를 계산한다.
규칙 근거(2026):
  - 연금저축: 국내상장 ETF/ETN, 비레버리지/비인버스만 (개별주식·해외상장·레버리지 불가)
  - ISA중개형: 국내상장이면 개별주식·ETF 등 대부분 가능 (해외상장 직접 불가)
  - 일반/소수점: 제약 없음

⚠️ 법규 해석은 변동 가능 → 경계 케이스(리츠/인프라펀드 등)는 보수적으로 제외. 최종 확인 권장.
"""
from __future__ import annotations

DOMESTIC_MARKETS = {"KOSPI", "KOSDAQ", "KR_ETC"}
PENSION_TYPES = {"ETF", "ETN"}                       # 연금: 간접투자상품만
ISA_TYPES = {"STOCK", "ETF", "ETN", "REIT",
             "INFRASTRUCTURE_FUND", "DEPOSITARY_RECEIPT"}  # ISA: 국내상장 대부분


def _is_domestic(info: dict) -> bool:
    return info.get("market") in DOMESTIC_MARKETS


def _is_leveraged(info: dict) -> bool:
    lf = info.get("leverageFactor")
    if lf is None:
        return False
    try:
        return abs(float(lf)) != 1.0
    except (TypeError, ValueError):
        return False


def eligible_accounts(info: dict) -> set[str]:
    """이 종목을 담을 수 있는 계좌 유형 집합. {'pension','isa','general'}"""
    out = {"general"}  # 일반/소수점은 항상 가능
    st = info.get("securityType")
    if _is_domestic(info):
        if st in ISA_TYPES:
            out.add("isa")
        if st in PENSION_TYPES and not _is_leveraged(info):
            out.add("pension")
    return out


def can_hold(account_type: str, info: dict) -> bool:
    """account_type in {'pension','isa','general','fraction','toss'}"""
    key = "general" if account_type in ("general", "fraction", "toss") else account_type
    return key in eligible_accounts(info)
