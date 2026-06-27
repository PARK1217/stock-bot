"""실계좌 단일 출처(Single Source of Truth) — 계좌 식별(CANO·상품·라벨·시장).

KIS 3계좌(ISA·연금·소수점)의 계좌번호를 한 곳에서 정의한다. 이전엔 ("63776023","01")
같은 튜플이 main.py·server.py·realized.py·chateval.py 14곳에 복붙돼 있었고, 계좌를
바꾸면 전부 찾아 고쳐야 했다 → 이제 여기(+config) 한 곳만 고치면 된다.

계좌번호는 config(.env로 교체 가능)에서 가져온다.
"""
from __future__ import annotations

from dataclasses import dataclass

from bot.config import settings


@dataclass(frozen=True)
class Account:
    key: str            # isa | pension | fraction
    label: str
    cano: str
    prod: str
    market: str         # "KR" | "US"
    overseas: bool      # 해외계좌(국내 잔고 API 미적용, 해외잔고 API 사용)

    @property
    def acct(self) -> tuple[str, str]:
        return (self.cano, self.prod)


def account_registry() -> list[Account]:
    """KIS 3계좌 레지스트리(ISA·연금·소수점). 잔고/스냅샷/실현손익 순회의 정본."""
    return [
        Account("isa", "ISA중개형", settings.kis_main_cano, "01", "KR", False),
        Account("pension", "연금저축", settings.kis_main_cano, "22", "KR", False),
        Account("fraction", "소수점주식", settings.kis_fraction_cano, "01", "US", True),
    ]


def kr_data_account() -> tuple[str, str]:
    """KR 시세·일봉 조회 전용 계좌튜플(어느 KR계좌든 무관 → ISA 사용).
    시세/캔들은 계좌와 독립이라 '아무 KR계좌'면 되므로 이 헬퍼로 통일한다."""
    return (settings.kis_main_cano, "01")
