"""계좌 프로필 — 계좌별 '투자 가능 유니버스'와 '교체 규칙'.

핵심: 각 계좌는 살 수 있는 상품이 다르다(법적 제약). 봇은 계좌 안에서만 갈아탄다.
  - 연금저축: 국내 상장 ETF·펀드만 (개별주식·해외상장ETF·레버리지 불가). 과세이연.
  - ISA 중개형: 국내주식 + 국내상장 ETF(국내상장 해외ETF 포함). 비과세 한도·3년만기.
  - 소수점주식: 해외/국내 자유.

교체 규칙(보수적): 보유가 '크게 처질' 때만, 유니버스 내 '확실히 더 나은' 후보로 교체 제안.
계좌 성격에 따라 강도 차등(소수점>ISA>연금). 모든 값은 튜닝 가능.

⚠️ 아래 유니버스 종목코드는 대표 예시 — 본인 선호로 교체/검증할 것.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AccountProfile:
    key: str                       # 식별자
    label: str
    broker: str                    # "kis" | "toss"
    account: tuple[str, str] | None  # (CANO, PRDT). toss는 None
    universe: list[str] = field(default_factory=list)  # 교체 후보(살 수 있는 것)
    review_days: int = 90          # 점검 주기(일)
    drop_score: float = 0.0        # 이 점수 미만이면 '처짐'으로 간주
    switch_margin: float = 20.0    # 후보가 보유보다 이만큼 높아야 교체 제안
    max_switches: int = 1          # 1회 점검당 최대 교체 건수
    note: str = ""


# 대표 국내상장 ETF (연금/ISA 후보) — 비레버리지·배당/지수 중심
KR_ETF_UNIVERSE = [
    "458730",  # TIGER 미국배당다우존스 (SCHD 국내판, 월배당)
    "360750",  # TIGER 미국S&P500
    "133690",  # TIGER 미국나스닥100
    "379800",  # KODEX 미국S&P500TR
    "069500",  # KODEX 200
    "161510",  # PLUS 고배당주
    "273130",  # KODEX 종합채권(AA-이상)액티브 (안정)
]

# 미국 배당/인컴 ETF (소수점 후보)
US_ETF_UNIVERSE = [
    "SCHD", "JEPI", "JEPQ", "QQQI", "SPYI", "VYM", "DGRO", "VIG",
    "DIVO", "O", "HDV", "SPHD",
]

PROFILES: dict[str, AccountProfile] = {
    "pension": AccountProfile(
        key="pension", label="연금저축", broker="kis", account=("63776023", "22"),
        universe=KR_ETF_UNIVERSE, review_days=90,
        drop_score=-10, switch_margin=25, max_switches=1,
        note="가장 보수적. 국내ETF만. 과세이연이라 갈아타기 세금부담 없음."),
    "isa": AccountProfile(
        key="isa", label="ISA중개형", broker="kis", account=("63776023", "01"),
        universe=KR_ETF_UNIVERSE, review_days=90,
        drop_score=-5, switch_margin=20, max_switches=2,
        note="중간. 국내주식+국내상장ETF. 비과세 한도·3년만기 주의(과매매 금지)."),
    "fraction": AccountProfile(
        key="fraction", label="소수점주식", broker="kis", account=("63751874", "01"),
        universe=US_ETF_UNIVERSE, review_days=90,
        drop_score=0, switch_margin=15, max_switches=2,
        note="가장 유연. 해외 배당/인컴 ETF 중심."),
    "toss": AccountProfile(
        key="toss", label="토스(미국)", broker="toss", account=None,
        universe=US_ETF_UNIVERSE, review_days=90,
        drop_score=0, switch_margin=15, max_switches=2,
        note="토스 미국 배당/인컴 포트폴리오."),
}
