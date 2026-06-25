"""교체 제안 엔진 — 계좌 안에서 '처진 보유'를 '더 나은 후보'로 갈아타자고 제안.

보수적: 보유 점수가 profile.drop_score 미만(처짐)이고,
유니버스 내 후보가 profile.switch_margin 이상 더 높을 때만 제안.
실시간 아님. 점검 주기(review_days)마다. 반자동(제안→사용자 승인).

broker 비의존: candles 조회 함수를 주입받아 KIS/토스 모두에 쓴다.
"""
from __future__ import annotations

from dataclasses import dataclass

from bot.accounts import AccountProfile
from bot.screener import score_symbol


@dataclass
class SwitchProposal:
    account: str
    sell_symbol: str
    sell_score: float
    buy_symbol: str
    buy_score: float
    reason: str

    def line(self) -> str:
        return (f"[{self.account}] 교체 제안: {self.sell_symbol}(점수 {self.sell_score:.0f}) "
                f"→ {self.buy_symbol}(점수 {self.buy_score:.0f})  | {self.reason}")


def propose_switches(profile: AccountProfile, held_symbols: list[str],
                     get_closes, eligible=None) -> list[SwitchProposal]:
    """held_symbols: 보유 종목코드. get_closes(symbol)->list[float] 종가 시계열.
    eligible(symbol)->bool 제공 시, 계좌가 담을 수 있는 후보만 매수 대상으로 둔다
    (레버리지·인버스·해외상장·개별주식 등 자동 제외)."""
    # 보유 + 유니버스 점수화
    def score(sym):
        closes = get_closes(sym)
        r = score_symbol(sym, [{"close": c} for c in closes]) if closes else None
        return r.score if r else None

    candidates = [s for s in profile.universe if s not in held_symbols]
    if eligible is not None:
        candidates = [s for s in candidates if eligible(s)]
    held_scores = {s: score(s) for s in held_symbols}
    uni_scores = {s: score(s) for s in candidates}
    uni_ranked = sorted(((s, v) for s, v in uni_scores.items() if v is not None),
                        key=lambda x: x[1], reverse=True)

    proposals: list[SwitchProposal] = []
    used_buys: set[str] = set()
    # 처진 보유부터(점수 낮은 순) 검토
    deteriorated = sorted(((s, v) for s, v in held_scores.items()
                           if v is not None and v < profile.drop_score),
                          key=lambda x: x[1])
    for sym, sc in deteriorated:
        if len(proposals) >= profile.max_switches:
            break
        for cand, cv in uni_ranked:
            if cand in used_buys:
                continue
            if cv - sc >= profile.switch_margin:
                proposals.append(SwitchProposal(
                    account=profile.label, sell_symbol=sym, sell_score=sc,
                    buy_symbol=cand, buy_score=cv,
                    reason=f"추세 처짐(<{profile.drop_score:g}), "
                           f"후보 +{cv - sc:.0f}점 우위"))
                used_buys.add(cand)
                break
    return proposals
