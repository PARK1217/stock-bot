"""데모 모드 — 모든 /api/* 요청을 가짜(FAKE) 데이터로 응답.

실제 브로커/DB 를 절대 건드리지 않고, 실 대시보드 UI 가 가득 찬 채로 렌더되도록
합성 데이터를 반환한다. 로그인 시 데모 비밀번호를 쓰면 미들웨어가 모든 /api/*
요청을 이 모듈의 demo_api(...) 로 라우팅한다.

- 순수 표준 라이브러리만 사용.
- 결정론적(deterministic): random / wall-clock 미사용. 데모용 고정 날짜/숫자만 사용.

원본(source of truth): scratchpad/stockbot-demo.html 의 JS mock (NM, TOSS_POS,
KIS_POS, PAPER_POS, 스크리너, 뉴스, 노출, 제안, 예측/정확도, 자산 시계열, 현금/주문,
실현손익, 용어, 챗봇 응답, fetch 디스패처)을 그대로 파이썬으로 이식했다.
실제 서버 응답 형태(app/web/server.py, chateval.py)와 rag.html 의 fetch 를 교차 검증했다.
"""

import math

FX = 1385  # 데모 환율(USDKRW)

# ---------- 종목 한글명(서버 name 필드 재현) ----------
NM = {
    "SCHD": "Schwab 미국배당", "JEPI": "JPM 프리미엄인컴", "JEPQ": "JPM 나스닥프리미엄",
    "O": "리얼티인컴", "SPYI": "S&P 커버드콜", "QQQI": "나스닥 커버드콜",
    "DGRO": "iShares 배당성장", "VIG": "뱅가드 배당성장", "NVDA": "엔비디아",
    "AAPL": "애플", "TSLA": "테슬라", "SOXL": "반도체 3배", "SGOV": "미국 단기국채",
    "BOXX": "초단기 채권", "MSFT": "마이크로소프트", "AMZN": "아마존", "AVGO": "브로드컴",
    "META": "메타", "GOOGL": "알파벳(구글)", "BRK-B": "버크셔해서웨이", "LLY": "일라이릴리",
    "360200": "ACE 미국S&P500", "360750": "TIGER 미국S&P500", "379800": "KODEX 미국S&P500",
    "484790": "KODEX 미국30년국채액티브", "354240": "RISE 미국고정배당우선증권",
    "0046A0": "TIGER 미국초단기국채", "005930": "삼성전자", "000660": "SK하이닉스",
    "005380": "현대차", "035420": "NAVER", "373220": "LG에너지솔루션",
}


def _nm(s):
    return NM.get(s, "")


def _round2(n):
    return round(n * 100) / 100


# ---------- 토스(미국) 보유 ----------
def _build_toss():
    rows = [
        {"symbol": "SCHD", "qty": 120, "avg_price": 26.4, "price": 28.9, "currency": "USD", "pnl_pct": 9.5},
        {"symbol": "JEPI", "qty": 80, "avg_price": 55.1, "price": 57.8, "currency": "USD", "pnl_pct": 4.9},
        {"symbol": "JEPQ", "qty": 65, "avg_price": 50.3, "price": 53.6, "currency": "USD", "pnl_pct": 6.6},
        {"symbol": "O", "qty": 70, "avg_price": 58.9, "price": 57.1, "currency": "USD", "pnl_pct": -3.1},
        {"symbol": "SPYI", "qty": 55, "avg_price": 49.2, "price": 51.4, "currency": "USD", "pnl_pct": 4.5},
        {"symbol": "QQQI", "qty": 60, "avg_price": 47.8, "price": 50.9, "currency": "USD", "pnl_pct": 6.5},
        {"symbol": "DGRO", "qty": 40, "avg_price": 60.2, "price": 64.7, "currency": "USD", "pnl_pct": 7.5},
        {"symbol": "NVDA", "qty": 12, "avg_price": 118.0, "price": 132.6, "currency": "USD", "pnl_pct": 12.4},
        {"symbol": "AAPL", "qty": 18, "avg_price": 210.5, "price": 203.9, "currency": "USD", "pnl_pct": -3.1},
        {"symbol": "TSLA", "qty": 8, "avg_price": 245.0, "price": 259.0, "currency": "USD", "pnl_pct": 5.7},
        {"symbol": "SGOV", "qty": 90, "avg_price": 100.4, "price": 100.6, "currency": "USD", "pnl_pct": 0.2},
    ]
    for p in rows:
        p["name"] = _nm(p["symbol"])
        p["value"] = p["qty"] * p["price"]
        p["value_krw"] = round(p["value"] * (FX if p["currency"] == "USD" else 1))
    return rows


TOSS_POS = _build_toss()
TOSS_TOTAL = sum(p["value_krw"] for p in TOSS_POS)
TOSS_COST = sum(p["qty"] * p["avg_price"] * FX for p in TOSS_POS)


# ---------- 한투 실계좌 보유 (라벨 정확: ISA중개형/연금저축/소수점주식) ----------
def _build_kis():
    rows = [
        {"account": "ISA중개형", "market": "KR", "symbol": "360200", "qty": 180, "price": 19850, "currency": "KRW", "pnl_pct": 8.2},
        {"account": "ISA중개형", "market": "KR", "symbol": "354240", "qty": 210, "price": 10420, "currency": "KRW", "pnl_pct": 2.6},
        {"account": "ISA중개형", "market": "KR", "symbol": "005930", "qty": 35, "price": 78400, "currency": "KRW", "pnl_pct": -4.3},
        {"account": "연금저축", "market": "KR", "symbol": "379800", "qty": 260, "price": 15680, "currency": "KRW", "pnl_pct": 11.1},
        {"account": "연금저축", "market": "KR", "symbol": "484790", "qty": 320, "price": 9120, "currency": "KRW", "pnl_pct": -2.8},
        {"account": "연금저축", "market": "KR", "symbol": "0046A0", "qty": 150, "price": 52300, "currency": "KRW", "pnl_pct": 0.9},
        {"account": "소수점주식", "market": "US", "symbol": "VIG", "qty": 14, "price": 198.4, "currency": "USD", "pnl_pct": 6.1},
        {"account": "소수점주식", "market": "US", "symbol": "NVDA", "qty": 3, "price": 132.6, "currency": "USD", "pnl_pct": 12.4},
        {"account": "소수점주식", "market": "US", "symbol": "AVGO", "qty": 6, "price": 172.9, "currency": "USD", "pnl_pct": 5.4},
    ]
    for p in rows:
        p["name"] = _nm(p["symbol"])
        p["value_krw"] = round(p["qty"] * p["price"] * (FX if p["currency"] == "USD" else 1))
    return rows


KIS_POS = _build_kis()
KIS_VAL = sum(p["value_krw"] for p in KIS_POS)
KIS_COST = sum(p["value_krw"] / (1 + p["pnl_pct"] / 100) for p in KIS_POS)


# ---------- 모의(페이퍼) 보유 ----------
PAPER_POS = [
    {"symbol": "SCHD", "name": _nm("SCHD"), "qty": 1400, "market": "US", "price": 28.9, "pnl_pct": 6.2, "value_krw": round(1400 * 28.9 * FX), "bucket": "core"},
    {"symbol": "SPYI", "name": _nm("SPYI"), "qty": 900, "market": "US", "price": 51.4, "pnl_pct": 3.8, "value_krw": round(900 * 51.4 * FX), "bucket": "core"},
    {"symbol": "NVDA", "name": _nm("NVDA"), "qty": 80, "market": "US", "price": 132.6, "pnl_pct": 9.1, "value_krw": round(80 * 132.6 * FX), "bucket": "sat"},
    {"symbol": "SOXL", "name": _nm("SOXL"), "qty": 200, "market": "US", "price": 31.2, "pnl_pct": -6.4, "value_krw": round(200 * 31.2 * FX), "bucket": "exit"},
    {"symbol": "360200", "name": _nm("360200"), "qty": 2200, "market": "KR", "price": 19850, "pnl_pct": 5.1, "value_krw": 2200 * 19850, "bucket": "core"},
]
PAPER_INVESTED = sum(p["value_krw"] for p in PAPER_POS)
PAPER_INITIAL = 500000000
PAPER_TOTAL = 500000000 + 18400000  # +약1840만(가상)


# ---------- 스크리너 ----------
def _mk_scn(arr):
    out = []
    for r in arr:
        out.append({
            "symbol": r[0], "score": r[1], "last": r[2], "ret_1m": r[3], "ret_3m": r[4],
            "above_sma200": r[5], "trend_aligned": r[5], "vol_20d": 1.2, "rvol": r[6],
            "forecast": ({"exp_return": r[7][0], "prob_up": r[7][1], "horizon": 21} if r[7] else None),
        })
    return out


SCN_US = _mk_scn([
    ["NVDA", 92.4, 132.6, 14.2, 31.5, True, 2.3, [4.8, 0.71]],
    ["QQQI", 78.1, 50.9, 6.1, 12.4, True, 1.1, [3.1, 0.66]],
    ["JEPQ", 71.5, 53.6, 4.9, 9.8, True, None, [2.4, 0.63]],
    ["SPYI", 66.2, 51.4, 4.5, 8.1, True, None, [2.2, 0.62]],
    ["SCHD", 58.7, 28.9, 3.2, 6.4, True, None, [1.9, 0.60]],
    ["DGRO", 52.3, 64.7, 2.8, 5.1, True, None, None],
    ["VIG", 47.9, 198.4, 2.1, 4.3, True, None, None],
    ["O", 33.4, 57.1, -1.2, -2.6, False, 1.6, None],
])
SCN_KR = _mk_scn([
    ["379800", 88.0, 15680, 9.4, 18.2, True, 1.4, [3.6, 0.68]],
    ["360200", 81.3, 19850, 8.1, 15.6, True, None, [3.2, 0.65]],
    ["005930", 64.5, 78400, 3.1, 7.2, True, 2.1, [2.8, 0.61]],
    ["000660", 59.8, 196500, 5.6, 11.4, True, 1.8, None],
    ["005380", 44.2, 241000, 1.9, 3.8, False, None, None],
    ["354240", 38.6, 10420, 1.1, 2.4, True, None, None],
    ["484790", 22.1, 9120, -2.1, -4.3, False, None, None],
])
SCN_US_SINGLE = _mk_scn([
    ["NVDA", 92.4, 132.6, 14.2, 31.5, True, 2.3, [4.8, 0.71]],
    ["AAPL", 48.1, 203.9, -3.1, 2.4, False, 1.1, None],
    ["TSLA", 61.7, 259.0, 5.7, 14.8, True, 1.9, [3.9, 0.64]],
    ["MSFT", 72.4, 438.2, 4.4, 9.1, True, None, None],
    ["AMZN", 66.8, 201.7, 3.9, 8.6, True, None, None],
    ["META", 77.2, 592.4, 6.8, 15.2, True, 1.3, None],
    ["AVGO", 70.1, 172.9, 5.4, 13.1, True, None, None],
])
SCN_KR_SINGLE = _mk_scn([
    ["005930", 64.5, 78400, 3.1, 7.2, True, 2.1, [2.8, 0.61]],
    ["000660", 72.3, 196500, 5.6, 11.4, True, 1.8, [3.4, 0.66]],
    ["373220", 55.1, 382000, 4.2, 9.8, True, None, None],
    ["035420", 41.8, 201500, 1.4, 3.1, False, None, None],
    ["005380", 44.2, 241000, 1.9, 3.8, False, None, None],
])

BACKTEST = {"n": 4820, "horizon": 21, "ic": -0.06, "spread": -1.8, "high_avg": 1.4,
            "low_avg": 3.2, "hit": 46.2, "grade": "역상관"}


# ---------- 뉴스 ----------
NEWS = [
    {"symbol": "NVDA", "score": 0.42, "polarity": "긍정", "sources": 9, "rvol": 2.3, "ret_1d": 2.1, "ret_5d": 6.4, "base1": 129.9, "base5": 124.6,
     "summary": "AI 반도체 수요 강세·데이터센터 매출 최고 · 신규 칩 공개 · 목표주가 상향"},
    {"symbol": "SCHD", "score": 0.18, "polarity": "긍정", "sources": 4, "rvol": 1.1, "ret_1d": 0.4, "ret_5d": 1.8, "base1": 28.8, "base5": 28.4,
     "summary": "배당 인상 발표 · 방어적 배당주 자금 유입 · 저변동 선호"},
    {"symbol": "TSLA", "score": -0.22, "polarity": "부정", "sources": 6, "rvol": 1.9, "ret_1d": -1.4, "ret_5d": 2.6, "base1": 262.7, "base5": 252.4,
     "summary": "인도량 둔화 우려 · 가격 인하 마진 압박 · 경쟁 심화"},
    {"symbol": "O", "score": -0.14, "polarity": "부정", "sources": 3, "rvol": 1.6, "ret_1d": -0.9, "ret_5d": -2.6, "base1": 57.6, "base5": 58.6,
     "summary": "금리 부담 리츠 약세 · 임대료 성장 둔화"},
]


# ---------- 노출(룩스루) ----------
EXPO = {
    "total": round(TOSS_TOTAL + KIS_VAL), "n": 38, "partial": False, "top5_pct": 41.3,
    "top": [
        {"symbol": "NVDA", "name": _nm("NVDA"), "krw": 9800000, "pct": 11.2},
        {"symbol": "AAPL", "name": _nm("AAPL"), "krw": 8100000, "pct": 9.3},
        {"symbol": "MSFT", "name": _nm("MSFT"), "krw": 7400000, "pct": 8.5},
        {"symbol": "AMZN", "name": _nm("AMZN"), "krw": 5200000, "pct": 6.0},
        {"symbol": "AVGO", "name": _nm("AVGO"), "krw": 5100000, "pct": 5.9},
        {"symbol": "META", "name": _nm("META"), "krw": 4300000, "pct": 4.9},
        {"symbol": "GOOGL", "name": _nm("GOOGL"), "krw": 3800000, "pct": 4.4},
        {"symbol": "BRK-B", "name": _nm("BRK-B"), "krw": 3100000, "pct": 3.6},
        {"symbol": "LLY", "name": _nm("LLY"), "krw": 2600000, "pct": 3.0},
        {"symbol": "JEPI", "name": _nm("JEPI"), "krw": 2400000, "pct": 2.8},
    ],
}


# ---------- 제안 ----------
PROPOSALS = [
    {"id": 1, "ts": "2026-07-08 09:12", "symbol": "SCHD", "side": "buy", "qty": 15, "currency": "USD", "ref_price": 28.9, "reason": "rebalance 6.4%->8.0%", "status": "pending"},
    {"id": 2, "ts": "2026-07-08 09:12", "symbol": "SOXL", "side": "sell", "qty": 120, "currency": "USD", "ref_price": 31.2, "reason": "stop_loss -6.4", "status": "pending"},
    {"id": 3, "ts": "2026-07-07 15:40", "symbol": "360200", "side": "buy", "qty": 40, "currency": "KRW", "ref_price": 19850, "reason": "rebalance 0.0%->5.0%", "status": "pending"},
]

FORECAST = {
    "SCHD": {"symbol": "SCHD", "last": 28.9, "horizon_days": 21, "prob_up": 0.60, "exp_return": 1.9, "band": {"p10": 27.6, "p50": 29.4, "p90": 31.1}, "exp_peak_pct": 5.2, "exp_trough_pct": -3.4},
    "SOXL": {"symbol": "SOXL", "last": 31.2, "horizon_days": 21, "prob_up": 0.44, "exp_return": -1.8, "band": {"p10": 26.9, "p50": 30.6, "p90": 35.2}, "exp_peak_pct": 9.8, "exp_trough_pct": -11.2},
    "360200": {"symbol": "360200", "last": 19850, "horizon_days": 21, "prob_up": 0.65, "exp_return": 3.2, "band": {"p10": 19100, "p50": 20200, "p90": 21400}, "exp_peak_pct": 6.4, "exp_trough_pct": -3.1},
}


# ---------- 예측 기록 / 정확도 ----------
PREDICTIONS = [
    {"id": 20, "made_at": "2026-07-01 08:00", "symbol": "NVDA", "horizon_days": 21, "base_price": 126.4, "prob_up": 0.71, "exp_return": 4.8, "status": "open", "actual_return": None, "dir_hit": None, "band_hit": None},
    {"id": 19, "made_at": "2026-07-01 08:00", "symbol": "QQQI", "horizon_days": 21, "base_price": 49.6, "prob_up": 0.66, "exp_return": 3.1, "status": "open", "actual_return": None, "dir_hit": None, "band_hit": None},
    {"id": 18, "made_at": "2026-06-30 08:00", "symbol": "379800", "horizon_days": 21, "base_price": 15200, "prob_up": 0.68, "exp_return": 3.6, "status": "open", "actual_return": None, "dir_hit": None, "band_hit": None},
    {"id": 12, "made_at": "2026-06-05 08:00", "symbol": "SCHD", "horizon_days": 21, "base_price": 27.6, "prob_up": 0.62, "exp_return": 2.1, "status": "evaluated", "actual_return": 4.7, "dir_hit": True, "band_hit": True},
    {"id": 11, "made_at": "2026-06-04 08:00", "symbol": "JEPQ", "horizon_days": 21, "base_price": 51.0, "prob_up": 0.63, "exp_return": 2.4, "status": "evaluated", "actual_return": 5.1, "dir_hit": True, "band_hit": True},
    {"id": 10, "made_at": "2026-06-03 08:00", "symbol": "O", "horizon_days": 21, "base_price": 59.4, "prob_up": 0.58, "exp_return": 1.7, "status": "evaluated", "actual_return": -3.9, "dir_hit": False, "band_hit": False},
    {"id": 9, "made_at": "2026-06-02 08:00", "symbol": "NVDA", "horizon_days": 21, "base_price": 120.1, "prob_up": 0.70, "exp_return": 4.4, "status": "evaluated", "actual_return": 10.4, "dir_hit": True, "band_hit": False},
    {"id": 8, "made_at": "2026-06-01 08:00", "symbol": "SPYI", "horizon_days": 21, "base_price": 49.9, "prob_up": 0.61, "exp_return": 1.9, "status": "evaluated", "actual_return": 3.0, "dir_hit": True, "band_hit": True},
    {"id": 7, "made_at": "2026-05-29 08:00", "symbol": "005930", "horizon_days": 21, "base_price": 81900, "prob_up": 0.55, "exp_return": 1.4, "status": "evaluated", "actual_return": -4.3, "dir_hit": False, "band_hit": True},
]
ACCURACY = {"evaluated": 42, "dir_acc": 0.64, "band_acc": 0.57,
            "by_horizon": [{"horizon": 21, "evaluated": 42, "dir_acc": 0.64, "band_acc": 0.57}]}


# ---------- 자산 시계열 (~30점, 완만 상승+2회 하락) ----------
def _series(start, end, n, dips):
    out = []
    for i in range(n):
        v = start + (end - start) * (i / (n - 1))
        v *= (1 + math.sin(i * 0.6) * 0.006)  # 잔물결
        for d in dips:
            if d["at"] <= i < d["at"] + 3:
                v *= (1 - d["mag"])
        out.append(round(v))
    return out


_N = 30
# 고정 데모 날짜: 2026-06-08 부터 30일 (JS Date(2026,5,8+i))
_ASSET_TS = ["2026-{:02d}-{:02d}".format(*_md) for _md in (
    [(6, d) for d in range(8, 31)] + [(7, d) for d in range(1, 8)]
)]
ASSETS = {
    "ts": _ASSET_TS,
    "series": {
        "전체 자산": _series(112000000, 120500000, _N, [{"at": 9, "mag": 0.02}, {"at": 20, "mag": 0.015}]),
        "토스(미국)": _series(46000000, 52000000, _N, [{"at": 9, "mag": 0.025}, {"at": 20, "mag": 0.018}]),
        "ISA": _series(28000000, 31000000, _N, [{"at": 9, "mag": 0.015}, {"at": 20, "mag": 0.012}]),
        "연금": _series(33000000, 37500000, _N, [{"at": 9, "mag": 0.012}, {"at": 20, "mag": 0.01}]),
    },
}


# ---------- 현금 / 주문 ----------
CASH = {"KRW": 1284000, "USD": 342.18}
ORDERS = [
    {"at": "07-07 22:31", "side": "BUY", "symbol": "SCHD", "qty": 10, "price": 28.85, "amount": 288.5, "fee": 0.13, "currency": "USD", "pending": False},
    {"at": "07-07 22:05", "side": "BUY", "symbol": "QQQI", "qty": 8, "price": 50.72, "amount": 405.8, "fee": 0.18, "currency": "USD", "pending": False},
    {"at": "07-04 23:14", "side": "SELL", "symbol": "O", "qty": 12, "price": 57.60, "amount": 691.2, "fee": 0.31, "currency": "USD", "pending": False},
    {"at": "07-03 22:48", "side": "BUY", "symbol": "JEPQ", "qty": 6, "price": 53.10, "amount": 318.6, "fee": 0.14, "currency": "USD", "pending": False},
    {"at": "07-08 09:01", "side": "BUY", "symbol": "DGRO", "qty": 5, "price": 0, "amount": 0, "fee": 0, "currency": "USD", "pending": True},
    {"at": "07-01 22:36", "side": "BUY", "symbol": "NVDA", "qty": 2, "price": 126.40, "amount": 252.8, "fee": 0.11, "currency": "USD", "pending": False},
    {"at": "06-27 22:10", "side": "BUY", "symbol": "SGOV", "qty": 20, "price": 100.55, "amount": 2011.0, "fee": 0.00, "currency": "USD", "pending": False},
    {"at": "06-25 22:52", "side": "SELL", "symbol": "AAPL", "qty": 3, "price": 208.30, "amount": 624.9, "fee": 0.28, "currency": "USD", "pending": False},
    {"at": "06-23 22:19", "side": "BUY", "symbol": "SPYI", "qty": 9, "price": 49.90, "amount": 449.1, "fee": 0.20, "currency": "USD", "pending": False},
    {"at": "06-20 22:41", "side": "BUY", "symbol": "TSLA", "qty": 2, "price": 245.00, "amount": 490.0, "fee": 0.22, "currency": "USD", "pending": False},
    {"at": "06-18 22:03", "side": "BUY", "symbol": "VIG", "qty": 4, "price": 194.20, "amount": 776.8, "fee": 0.34, "currency": "USD", "pending": False},
]
KIS_ORDERS = [
    {"at": "07-08 09:05", "side": "BUY", "side_name": "매수", "account": "ISA중개형", "symbol": "360200", "name": _nm("360200"), "qty": 20, "filled": 0, "price": 19850, "amount": 397000, "pending": True},
    {"at": "07-07 13:22", "side": "BUY", "side_name": "매수", "account": "연금저축", "symbol": "379800", "name": _nm("379800"), "qty": 30, "filled": 30, "price": 15680, "amount": 470400, "pending": False},
    {"at": "07-04 10:41", "side": "SELL", "side_name": "매도", "account": "ISA중개형", "symbol": "005930", "name": _nm("005930"), "qty": 10, "filled": 10, "price": 79000, "amount": 790000, "pending": False},
    {"at": "07-02 11:08", "side": "BUY", "side_name": "매수", "account": "연금저축", "symbol": "484790", "name": _nm("484790"), "qty": 50, "filled": 50, "price": 9120, "amount": 456000, "pending": False},
    {"at": "06-30 09:33", "side": "BUY", "side_name": "매수", "account": "ISA중개형", "symbol": "354240", "name": _nm("354240"), "qty": 40, "filled": 40, "price": 10420, "amount": 416800, "pending": False},
    {"at": "06-26 14:02", "side": "BUY", "side_name": "매수", "account": "연금저축", "symbol": "0046A0", "name": _nm("0046A0"), "qty": 15, "filled": 15, "price": 52300, "amount": 784500, "pending": False},
]


# ---------- 모의 거래내역(페이징) ----------
PTR_ALL = [
    {"ts": "07-08 09:31", "symbol": "SCHD", "side": "buy", "qty": 120, "filled": 120, "market": "US", "status": "체결", "fill_price": 28.85, "amount_krw": round(120 * 28.85 * FX)},
    {"ts": "07-08 09:31", "symbol": "NVDA", "side": "buy", "qty": 10, "filled": 10, "market": "US", "status": "체결", "fill_price": 126.40, "amount_krw": round(10 * 126.4 * FX)},
    {"ts": "07-07 15:20", "symbol": "SOXL", "side": "sell", "qty": 50, "filled": 50, "market": "US", "status": "체결", "fill_price": 31.40, "amount_krw": round(50 * 31.4 * FX)},
    {"ts": "07-07 09:31", "symbol": "SPYI", "side": "buy", "qty": 80, "filled": 80, "market": "US", "status": "체결", "fill_price": 51.10, "amount_krw": round(80 * 51.1 * FX)},
    {"ts": "07-04 10:02", "symbol": "360200", "side": "buy", "qty": 100, "filled": 100, "market": "KR", "status": "체결", "fill_price": 19700, "amount_krw": 100 * 19700},
    {"ts": "07-03 09:31", "symbol": "NVDA", "side": "buy", "qty": 8, "filled": 5, "market": "US", "status": "부분체결", "fill_price": 125.90, "amount_krw": round(5 * 125.9 * FX)},
    {"ts": "07-02 15:38", "symbol": "SCHD", "side": "sell", "qty": 40, "filled": 40, "market": "US", "status": "체결", "fill_price": 28.60, "amount_krw": round(40 * 28.6 * FX)},
    {"ts": "07-01 09:31", "symbol": "SOXL", "side": "buy", "qty": 60, "filled": 0, "market": "US", "status": "미체결", "fill_price": None, "amount_krw": None},
    {"ts": "06-30 10:14", "symbol": "SPYI", "side": "buy", "qty": 30, "filled": 30, "market": "US", "status": "체결", "fill_price": 49.90, "amount_krw": round(30 * 49.9 * FX)},
    {"ts": "06-27 09:31", "symbol": "360200", "side": "sell", "qty": 60, "filled": 60, "market": "KR", "status": "체결", "fill_price": 19500, "amount_krw": 60 * 19500},
]


# ---------- 실현손익 ----------
REALIZED = {
    "fx": FX,
    "accounts": {
        "토스(나)": {"total_krw": 2360000, "daily": {}, "monthly": {},
            "sells": [
                {"month": "2026-07", "date": "2026-07-04", "symbol": "O", "qty": 12, "buy_avg": 60.10, "sell_price": 57.60, "currency": "USD", "realized": -30.0, "realized_krw": -41550},
                {"month": "2026-06", "date": "2026-06-25", "symbol": "AAPL", "qty": 3, "buy_avg": 198.40, "sell_price": 208.30, "currency": "USD", "realized": 29.7, "realized_krw": 411345},
                {"month": "2026-06", "date": "2026-06-11", "symbol": "JEPI", "qty": 20, "buy_avg": 53.90, "sell_price": 57.20, "currency": "USD", "realized": 66.0, "realized_krw": 914100},
                {"month": "2026-05", "date": "2026-05-19", "symbol": "SCHD", "qty": 30, "buy_avg": 25.80, "sell_price": 28.40, "currency": "USD", "realized": 78.0, "realized_krw": 1080300},
            ]},
        "한투": {"total_krw": 1720000, "daily": {}, "monthly": {},
            "sells": [
                {"month": "2026-07", "date": "2026-07-04", "symbol": "005930", "qty": 10, "buy_avg": 73000, "sell_price": 79000, "currency": "KRW", "realized": 60000, "realized_krw": 60000},
                {"month": "2026-06", "date": "2026-06-18", "symbol": "360200", "qty": 80, "buy_avg": 18200, "sell_price": 19500, "currency": "KRW", "realized": 104000, "realized_krw": 104000},
                {"month": "2026-05", "date": "2026-05-22", "symbol": "379800", "qty": 120, "buy_avg": 14100, "sell_price": 15200, "currency": "KRW", "realized": 132000, "realized_krw": 132000},
            ]},
        "모의": {"total_krw": 4180000, "daily": {}, "monthly": {},
            "sells": [
                {"month": "2026-07", "date": "2026-07-07", "symbol": "SOXL", "qty": 50, "buy_avg": 34.10, "sell_price": 31.40, "currency": "USD", "realized": -135.0, "realized_krw": -186975},
                {"month": "2026-07", "date": "2026-07-02", "symbol": "SCHD", "qty": 40, "buy_avg": 26.40, "sell_price": 28.60, "currency": "USD", "realized": 88.0, "realized_krw": 121880},
                {"month": "2026-06", "date": "2026-06-27", "symbol": "360200", "qty": 60, "buy_avg": 18100, "sell_price": 19500, "currency": "KRW", "realized": 84000, "realized_krw": 84000},
                {"month": "2026-06", "date": "2026-06-13", "symbol": "NVDA", "qty": 30, "buy_avg": 108.0, "sell_price": 124.0, "currency": "USD", "realized": 480.0, "realized_krw": 664800},
            ]},
    },
    "total": {"daily": {}, "monthly": {}, "total_krw": 8260000},
}


# ---------- 종목명 맵 / 용어 ----------
NAMES_MAP = dict(NM)
GLOSSARY = {
    "ETF": "여러 종목을 한 바구니에 담아 통째로 사고파는 상품이에요. 하나만 사도 자동 분산돼요.",
    "배당": "주식을 갖고 있으면 회사가 이익 일부를 나눠주는 돈이에요.",
    "정배열": "오늘 가격이 20일·50일·200일 평균보다 다 위에 있는 상태 — 계단 잘 밟고 오르는 중이라는 신호예요.",
    "커버드콜": "주식을 들고 있으면서 살짝 웃돈을 받고 파는 옵션을 걸어 매달 현금을 만드는 전략이에요.",
    "룩스루": "여러 ETF 속을 뚫어봐서 실제로 어떤 기업에 얼마나 투자됐는지 합쳐 보는 거예요.",
    "RVOL": "오늘 거래량이 평소(20일 평균)보다 몇 배인지 나타내요. 높으면 관심이 몰렸다는 뜻.",
    "실현손익": "실제로 팔아서 확정된 이익이나 손실이에요. 아직 안 판 건 평가손익이라고 해요.",
}


# ---------- 챗봇 응답 ----------
CHAT_REPLY = (
    "지금 데모 포트폴리오는 **배당+성장 균형형**으로 잘 짜여 있어요. 토스(미국)는 SCHD·JEPI·JEPQ 같은 인컴 ETF가 중심이라 방어력이 좋고, NVDA·TSLA로 성장 매운맛도 살짝 얹었네요(전체 약 **+7%대**).\n"
    "- **집중도 주의**: 룩스루로 뚫어보면 상위5개 기업에 **41%**가 몰려 있어요. 여러 ETF를 들고 있어도 속은 결국 엔비디아·애플·MS라 분산 착시가 있어요.\n"
    "- **SOXL**은 손실(-6.4%)이고 변동성이 큰 3배 상품이라, 목표 비중을 넘겼다면 조금 줄이는 걸 고려해볼 만해요.\n"
    "- 스크리너 점수는 **매수신호가 아니라 요즘 핫한 온도계**예요(신뢰도 역상관). 점수만 보고 추격매수는 피하세요.\n"
    "참고용 분석이에요 — 최종 판단은 직접 하세요! 🙂"
)


# ---------- RAG: 챗봇 정확도(chateval) / 이슈 히스토리 ----------
# 서버 chateval.report() 형태: {"accuracy": {n,hit,acc,open}, "calls": [...], "log": [...]}
CHATEVAL = {
    "accuracy": {"n": 6, "hit": 4, "acc": round(4 / 6, 4), "open": 2},
    "calls": [
        {"ts": "2026-07-05 21:14", "symbol": "NVDA", "direction": "up", "base_price": 128.4, "horizon": 21, "status": "open", "q": "엔비디아 지금 사도 될까?"},
        {"ts": "2026-07-03 22:02", "symbol": "SOXL", "direction": "down", "base_price": 32.9, "horizon": 21, "status": "open", "q": "SOXL 손절해야 하나?"},
        {"ts": "2026-06-12 21:40", "symbol": "SCHD", "direction": "up", "base_price": 27.6, "horizon": 21, "status": "evaluated", "actual_return": 4.7, "hit": True},
        {"ts": "2026-06-10 20:55", "symbol": "JEPQ", "direction": "up", "base_price": 51.0, "horizon": 21, "status": "evaluated", "actual_return": 5.1, "hit": True},
        {"ts": "2026-06-08 22:18", "symbol": "O", "direction": "up", "base_price": 59.4, "horizon": 21, "status": "evaluated", "actual_return": -3.9, "hit": False},
        {"ts": "2026-06-05 21:03", "symbol": "TSLA", "direction": "down", "base_price": 268.0, "horizon": 21, "status": "evaluated", "actual_return": -2.4, "hit": True},
        {"ts": "2026-06-02 21:31", "symbol": "005930", "direction": "up", "base_price": 81900, "horizon": 21, "status": "evaluated", "actual_return": -4.3, "hit": False},
        {"ts": "2026-06-01 20:47", "symbol": "SPYI", "direction": "up", "base_price": 49.9, "horizon": 21, "status": "evaluated", "actual_return": 3.0, "hit": True},
    ],
    "log": [
        {"ts": "2026-07-08T09:14", "who": "me", "user_msg": "지금 뭘 팔고 뭘 사면 좋을까?", "reply": CHAT_REPLY,
         "model": "claude-demo", "usage": {"total": 1240, "prompt": 980, "completion": 260},
         "system_prompt": "너는 참고용 투자분석 도우미야. 보유·스크리너·예측 근거만 사용하고 매수/매도를 단정하지 마.",
         "sources": "portfolio(toss,kis) · exposure(top5 41.3%) · screener(us/kr) · forecast · realized"},
        {"ts": "2026-07-07T22:31", "who": "me", "user_msg": "SOXL 지금 손절이 맞아?",
         "reply": "SOXL은 -6.4% 손실에 변동성이 큰 3배 레버리지라, 목표 비중을 넘겼다면 일부만 줄이는 분할 대응이 안전해요. 한 번에 전량 손절보다 규칙(비중/손절선) 기반으로 판단하세요. 참고용이에요.",
         "model": "claude-demo", "usage": {"total": 720, "prompt": 560, "completion": 160}},
        {"ts": "2026-07-05T21:14", "who": "spouse", "user_msg": "엔비디아 더 담아도 될까?",
         "reply": "엔비디아는 최근 흐름이 강하지만 스크리너 1등 종목은 되돌림(고무줄 반등) 위험이 있어요. 룩스루로 보면 이미 상위 노출 1위(11%)라 추가 매수 시 집중도가 더 커져요. 참고용이에요.",
         "model": "claude-demo", "usage": {"total": 690, "prompt": 540, "completion": 150}},
    ],
}

# 이슈 히스토리 (/api/issues) — 서버 반환은 dict 리스트
ISSUES = [
    {"date": "2026-07-08", "session": "US 장초반", "symbol": "NVDA", "market": "US", "score": 0.42, "polarity": "긍정",
     "summary": "AI 반도체 수요 강세 · 데이터센터 매출 최고 · 목표주가 상향", "sources": 9, "ret_1d": 2.1, "ret_5d": 6.4, "impact": "상승반영", "rvol": 2.3, "ts": "2026-07-08T09:35:00"},
    {"date": "2026-07-08", "session": "US 장초반", "symbol": "TSLA", "market": "US", "score": -0.22, "polarity": "부정",
     "summary": "인도량 둔화 우려 · 가격 인하 마진 압박", "sources": 6, "ret_1d": -1.4, "ret_5d": 2.6, "impact": "역행소화", "rvol": 1.9, "ts": "2026-07-08T09:35:00"},
    {"date": "2026-07-08", "session": "KR 마감", "symbol": "005930", "market": "KR", "score": 0.15, "polarity": "긍정",
     "summary": "HBM 공급 확대 기대 · 외국인 순매수", "sources": 5, "ret_1d": 0.8, "ret_5d": 3.1, "impact": "상승반영", "rvol": 2.1, "ts": "2026-07-08T15:40:00"},
    {"date": "2026-07-07", "session": "US 마감", "symbol": "O", "market": "US", "score": -0.14, "polarity": "부정",
     "summary": "금리 부담 리츠 약세 · 임대료 성장 둔화", "sources": 3, "ret_1d": -0.9, "ret_5d": -2.6, "impact": "하락반영", "rvol": 1.6, "ts": "2026-07-07T22:10:00"},
    {"date": "2026-07-07", "session": "US 장중", "symbol": "SCHD", "market": "US", "score": 0.18, "polarity": "긍정",
     "summary": "배당 인상 발표 · 방어적 배당주 자금 유입", "sources": 4, "ret_1d": 0.4, "ret_5d": 1.8, "impact": "횡보", "rvol": 1.1, "ts": "2026-07-07T20:05:00"},
    {"date": "2026-07-06", "session": "KR 장초반", "symbol": "379800", "market": "KR", "score": 0.26, "polarity": "긍정",
     "summary": "미국 S&P500 신고가 · 환율 안정 · 자금 유입", "sources": 7, "ret_1d": 1.2, "ret_5d": 4.1, "impact": "상승반영", "rvol": 1.4, "ts": "2026-07-06T09:32:00"},
]


# ============================================================
#  헬퍼 (JS의 portfolioResp/quotesResp/... 이식)
# ============================================================
def _portfolio_resp(who):
    if who == "spouse":  # 남편 = 토스만(간단 버전)
        pos = TOSS_POS[:5]
        tot = sum(p["value_krw"] for p in pos)
        return {"broker": "toss", "fx": FX, "cash": 540000, "total_krw": tot,
                "daily_pnl_pct": 0.6, "total_pnl_pct": 5.2,
                "daily_pnl_amt_krw": round(tot * 0.006),
                "total_pnl_amt_krw": round(tot * 0.052 / 1.052),
                "positions": pos}
    return {"broker": "toss", "fx": FX, "cash": 1284000, "total_krw": TOSS_TOTAL + 1284000,
            "daily_pnl_pct": 0.9, "total_pnl_pct": _round2((TOSS_TOTAL / TOSS_COST - 1) * 100),
            "daily_pnl_amt_krw": round((TOSS_TOTAL + 1284000) * 0.009),
            "total_pnl_amt_krw": round(TOSS_TOTAL - TOSS_COST),
            "positions": TOSS_POS}


def _quotes_resp(syms):
    px = {}
    all_pos = list(TOSS_POS) + list(KIS_POS) + list(PAPER_POS)
    scn_all = list(SCN_US) + list(SCN_KR) + list(SCN_US_SINGLE)
    for s in syms:
        p = next((x for x in all_pos if x["symbol"] == s), None)
        if p:
            px[s] = p["price"]
        if px.get(s) is None:
            n = next((x for x in NEWS if x["symbol"] == s), None)
            if n:
                r = next((r for r in scn_all if r["symbol"] == s), None)
                px[s] = (r["last"] if r else 100)
    return px


def _volume_resp(syms):
    rv = {"SCHD": 1.1, "NVDA": 2.3, "O": 1.6, "005930": 2.1, "360200": 1.4, "SOXL": 1.8, "TSLA": 1.9, "000660": 1.8}
    return {s: rv[s] for s in syms if s in rv}


def _kis_resp():
    val, cost = KIS_VAL, KIS_COST
    return {"total": val + 2100000, "cash_krw": 2100000, "fx": FX, "positions": KIS_POS,
            "total_pnl_pct": _round2((val / cost - 1) * 100), "total_pnl_amt_krw": round(val - cost),
            "daily_pnl_amt_krw": 214000, "daily_pnl_pct": 0.35}


def _paper_resp():
    hist_series = _series(485000000, 518400000, _N, [{"at": 9, "mag": 0.02}, {"at": 20, "mag": 0.015}])
    return {"cash": PAPER_TOTAL - PAPER_INVESTED, "total": PAPER_TOTAL, "invested": PAPER_INVESTED,
            "initial": PAPER_INITIAL, "ret_pct": _round2((PAPER_TOTAL / PAPER_INITIAL - 1) * 100),
            "positions": PAPER_POS,
            "strategy": {"regime": "risk_on", "core": ["SCHD", "SPYI", "360200"], "sat": ["NVDA"]},
            "history": [{"ts": ASSETS["ts"][i], "total": hist_series[i]} for i in range(_N)]}


def _ptr_resp(page, size):
    page = max(0, page)
    size = size or 8
    total = len(PTR_ALL)
    pages = math.ceil(total / size)
    turnover = sum((t.get("amount_krw") or 0) for t in PTR_ALL)
    return {"items": PTR_ALL[page * size:(page + 1) * size], "page": page, "size": size,
            "total": total, "pages": pages, "turnover_krw": turnover}


# ============================================================
#  디스패처
# ============================================================
def demo_api(method: str, path: str, query: dict, body: dict | None) -> dict | None:
    """데모 모드 라우터. 알 수 없는 경로 / health 는 None 반환(호출자가 {}로 변환)."""
    query = query or {}

    def _get(k, default=""):
        v = query.get(k, default)
        return v if v is not None else default

    def _split_syms(k):
        return [s for s in str(_get(k, "")).split(",") if s]

    # POST /api/chat
    if path == "/api/chat":
        return {"reply": CHAT_REPLY, "cached": False}

    # RAG 챗봇 평가/로그
    if path == "/api/chateval":
        return CHATEVAL
    if path == "/api/chateval/log":
        return {"ok": True, "id": 1}
    if path.startswith("/api/chateval"):
        return {"ok": True, "cases": []}

    if path == "/api/issues":
        return ISSUES

    if path == "/api/names":
        return {"names": NAMES_MAP}
    if path == "/api/market-status":
        return {"kr_open": True}
    if path == "/api/glossary":
        return {"terms": GLOSSARY}
    if path == "/api/portfolio":
        return _portfolio_resp(_get("who", "me") or "me")
    if path == "/api/quotes":
        return _quotes_resp(_split_syms("symbols"))
    if path == "/api/volume":
        return _volume_resp(_split_syms("symbols"))
    if path == "/api/kis/orders":
        return {"orders": KIS_ORDERS}
    if path == "/api/kis":
        return _kis_resp()
    if path == "/api/exposure":
        return EXPO
    if path == "/api/proposals":
        return PROPOSALS  # JS: 배열(JSON array) 반환
    if path == "/api/predictions":
        return PREDICTIONS
    if path == "/api/accuracy":
        return ACCURACY
    if path == "/api/paper/trades":
        try:
            page = int(_get("page", 0) or 0)
        except (TypeError, ValueError):
            page = 0
        try:
            size = int(_get("size", 8) or 8)
        except (TypeError, ValueError):
            size = 8
        return _ptr_resp(page, size)
    if path == "/api/paper":
        return _paper_resp()
    if path == "/api/cash":
        return CASH
    if path == "/api/orders":
        return {"orders": ORDERS}
    if path == "/api/assets":
        return ASSETS
    if path == "/api/realized":
        return REALIZED
    if path == "/api/screen/backtest":
        return BACKTEST
    if path == "/api/screen":
        m = str(_get("market", "us") or "us").lower()
        k = str(_get("kind", "etf") or "etf").lower()
        if m == "kr":
            return SCN_KR_SINGLE if k == "single" else SCN_KR
        return SCN_US_SINGLE if k == "single" else SCN_US
    if path.startswith("/api/forecast/"):
        sym = path.split("/api/forecast/", 1)[1].upper()
        return FORECAST.get(sym, {"error": "데이터 부족", "symbol": sym, "candles": 0})
    if path == "/api/news":
        syms = _split_syms("symbols")
        return [n for n in NEWS if (not syms or n["symbol"] in syms)]

    # health / 기타 알 수 없는 /api/* — None (호출자가 {} 로 변환)
    return None


# 참고: JS 원본은 /api/screen, /api/proposals, /api/predictions, /api/accuracy, /api/news
# 를 배열(JSON array)로 반환한다. 파이썬 반환 타입 힌트는 dict 지만, 실제로는 list 도
# 반환한다(서버 핸들러도 동일하게 list 를 반환하므로 UI 와 일치). 미들웨어에서 그대로
# JSON 직렬화하면 된다.


if __name__ == "__main__":
    # ---------- 셀프 테스트: 모든 엔드포인트 호출 + 상위 키 검증 ----------
    checks = [
        ("GET", "/api/names", {}, None, "dict", ["names"]),
        ("GET", "/api/market-status", {}, None, "dict", ["kr_open"]),
        ("GET", "/api/glossary", {}, None, "dict", ["terms"]),
        ("GET", "/api/portfolio", {"who": "me"}, None, "dict", ["broker", "positions", "total_krw"]),
        ("GET", "/api/portfolio", {"who": "spouse"}, None, "dict", ["broker", "positions"]),
        ("GET", "/api/quotes", {"symbols": "SCHD,NVDA,360200"}, None, "dict", ["SCHD", "NVDA"]),
        ("GET", "/api/volume", {"symbols": "SCHD,NVDA"}, None, "dict", ["SCHD", "NVDA"]),
        ("GET", "/api/kis", {}, None, "dict", ["positions", "total", "fx"]),
        ("GET", "/api/kis/orders", {}, None, "dict", ["orders"]),
        ("GET", "/api/exposure", {}, None, "dict", ["total", "top", "top5_pct"]),
        ("GET", "/api/proposals", {}, None, "list", None),
        ("GET", "/api/predictions", {}, None, "list", None),
        ("GET", "/api/accuracy", {}, None, "dict", ["evaluated", "dir_acc", "band_acc"]),
        ("GET", "/api/paper", {}, None, "dict", ["total", "positions", "history"]),
        ("GET", "/api/paper/trades", {"page": "0", "size": "8"}, None, "dict", ["items", "page", "pages", "total"]),
        ("GET", "/api/cash", {}, None, "dict", ["KRW", "USD"]),
        ("GET", "/api/orders", {}, None, "dict", ["orders"]),
        ("GET", "/api/assets", {}, None, "dict", ["ts", "series"]),
        ("GET", "/api/realized", {}, None, "dict", ["fx", "accounts", "total"]),
        ("GET", "/api/screen/backtest", {}, None, "dict", ["n", "ic", "grade"]),
        ("GET", "/api/screen", {"market": "us", "kind": "etf"}, None, "list", None),
        ("GET", "/api/screen", {"market": "us", "kind": "single"}, None, "list", None),
        ("GET", "/api/screen", {"market": "kr", "kind": "etf"}, None, "list", None),
        ("GET", "/api/screen", {"market": "kr", "kind": "single"}, None, "list", None),
        ("GET", "/api/forecast/SCHD", {}, None, "dict", ["symbol", "band", "prob_up"]),
        ("GET", "/api/forecast/UNKNOWN", {}, None, "dict", ["error", "symbol"]),
        ("GET", "/api/news", {"symbols": ""}, None, "list", None),
        ("GET", "/api/news", {"symbols": "NVDA"}, None, "list", None),
        ("POST", "/api/chat", {}, {"message": "지금 뭘 팔까?"}, "dict", ["reply", "cached"]),
        ("GET", "/api/chateval", {}, None, "dict", ["accuracy", "calls", "log"]),
        ("POST", "/api/chateval/log", {}, {"q": "test", "a": "reply"}, "dict", ["ok"]),
        ("GET", "/api/issues", {"days": "14"}, None, "list", None),
        ("GET", "/api/health", {}, None, "none", None),
        ("GET", "/api/does-not-exist", {}, None, "none", None),
    ]

    fails = 0
    for method, path, q, b, kind, keys in checks:
        res = demo_api(method, path, q, b)
        ok = True
        detail = ""
        if kind == "none":
            ok = res is None
            detail = "None" if ok else "expected None"
        elif kind == "list":
            ok = isinstance(res, list) and len(res) > 0
            detail = "list len={}".format(len(res) if isinstance(res, list) else "?")
        elif kind == "dict":
            ok = isinstance(res, dict)
            if ok and keys:
                missing = [k for k in keys if k not in res]
                ok = not missing
                detail = "dict keys ok" if ok else "MISSING {}".format(missing)
            else:
                detail = "dict"
        status = "PASS" if ok else "FAIL"
        if not ok:
            fails += 1
        print("[{}] {:5s} {:28s} ({})".format(status, method, path, detail))

    print("\n{} checks, {} failed.".format(len(checks), fails))
    # 추가 정합성: 챗봇 정확도 계산이 서버 report() 형태와 일치하는지
    ce = demo_api("GET", "/api/chateval", {}, None)
    assert ce["accuracy"]["n"] >= 1 and 0 <= ce["accuracy"]["acc"] <= 1, "chateval accuracy shape"
    # KIS 계좌 라벨 정확성
    kis = demo_api("GET", "/api/kis", {}, None)
    accts = {p["account"] for p in kis["positions"]}
    assert accts == {"ISA중개형", "연금저축", "소수점주식"}, "KIS account labels: {}".format(accts)
    print("extra asserts: chateval + KIS account labels OK")
    raise SystemExit(1 if fails else 0)
