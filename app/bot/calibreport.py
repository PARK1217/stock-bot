"""캘리브레이션 주간 리포트 — '결과를 보고 로직 고치기'의 상시화.

채점된 예측으로 ①확률 구간별 실제 상승률(과신/역캘리 감시) ②틸트 A/B(원시 vs 적용) 비교를
매주 집계해 디스코드로 알린다. CALIB_K(과신 수축 계수)를 자동으로 바꾸진 않는다 —
최근 표본 추종은 노이즈 과적합 위험이라, 리포트가 '재검토 필요'만 알리고 결정은 사람이.
(근거 실측 2026-10-21: 60~65% 확신 구간 실제 상승 40% — forecast.CALIB_K 주석 참조)
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

BUCKETS = [(0, .45), (.45, .55), (.55, .60), (.60, 1.01)]


def calib_report(send: bool = True) -> str:
    from bot.storage.db import SessionLocal
    from bot.storage.models import Prediction
    with SessionLocal() as s:
        ev = s.query(Prediction).filter(Prediction.status == "evaluated").all()
    if not ev:
        return "채점 표본 없음"
    n = len(ev)
    dir_acc = sum(1 for p in ev if p.dir_hit) / n
    band_acc = sum(1 for p in ev if p.band_hit) / n
    L = [f"🧪 [주간 캘리브레이션] 채점 {n:,}건 · 방향 {dir_acc*100:.0f}% · 범위 {band_acc*100:.0f}%"]

    # ① 확률 구간별 실제 상승률 — 과신(역캘리) 감시
    warn = False
    L.append("· 확률 구간별 실제 상승률(괄호=건수):")
    for lo, hi in BUCKETS:
        g = [p for p in ev if lo <= (p.prob_up or 0) < hi]
        if len(g) < 30:
            continue
        up = sum(1 for p in g if (p.actual_return or 0) > 0) / len(g)
        mid = sum(p.prob_up for p in g) / len(g)
        gap = (up - mid) * 100
        mark = " ⚠️" if abs(gap) >= 10 else ""
        if abs(gap) >= 10:
            warn = True
        L.append(f"   {lo*100:.0f}~{hi*100:.0f}%: 실제 {up*100:.0f}% (차이 {gap:+.0f}p{mark}, {len(g)}건)")

    # ② 틸트 A/B — 원시(raw) 병행기록 있는 채점분만(2026-10-21 이후 생성분)
    ab = [p for p in ev if p.prob_up_raw is not None and p.actual_return is not None]
    if len(ab) >= 50:
        hit_t = sum(1 for p in ab if ((p.actual_return or 0) > 0) == ((p.prob_up or 0) >= 0.5)) / len(ab)
        hit_r = sum(1 for p in ab if ((p.actual_return or 0) > 0) == ((p.prob_up_raw or 0) >= 0.5)) / len(ab)
        diff = (hit_t - hit_r) * 100
        verdict = ("틸트가 도움" if diff >= 2 else "틸트가 해로움 ⚠️" if diff <= -2 else "차이 미미")
        L.append(f"· 틸트 A/B({len(ab)}건): 적용 {hit_t*100:.0f}% vs 원시 {hit_r*100:.0f}% → {verdict}")
    else:
        L.append(f"· 틸트 A/B: 표본 {len(ab)}건 — 50건 쌓이면 비교 시작(10/21부터 기록 중)")

    L.append("→ ⚠️가 계속 뜨면 CALIB_K(현재 0.25) 재검토 필요. 자동 변경은 안 함(과적합 방지)."
             if warn else "→ 캘리브레이션 특이사항 없음. K=0.25 유지.")
    txt = "\n".join(L)
    if send:
        try:
            from bot.notify import notify
            notify(txt)
        except Exception:  # noqa: BLE001
            pass
    return txt
