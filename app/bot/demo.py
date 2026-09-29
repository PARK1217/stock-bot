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
import datetime
import re

# ---------- 데모 날짜를 '오늘 기준'으로 이동 ----------
# 데모 데이터의 고정 날짜(앵커=2026-07-08)를 요청 시점의 오늘에 맞춰 평행이동한다.
# 값은 합성이지만 날짜는 항상 최신으로 보인다(누적 아님·DB 미사용, 상대 간격은 보존).
_DATE_ANCHOR = datetime.date(2026, 7, 8)
_RE_FULL = re.compile(r"(\d{4})-(\d{2})-(\d{2})")                  # YYYY-MM-DD(+옵션 시각)
_RE_MONTH = re.compile(r"(\d{4})-(\d{2})(?!-?\d)")                 # YYYY-MM(월)
_RE_MD = re.compile(r"(?<![-\d])(\d{2})-(\d{2})(?=\s\d{2}:\d{2})")  # MM-DD HH:MM


def _shift_one(s):
    shift = (datetime.date.today() - _DATE_ANCHOR).days
    if shift == 0:
        return s

    def _full(m):
        try:
            d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return m.group(0)
        return (d + datetime.timedelta(days=shift)).strftime("%Y-%m-%d")

    def _month(m):
        try:
            d = datetime.date(int(m.group(1)), int(m.group(2)), 15)
        except ValueError:
            return m.group(0)
        return (d + datetime.timedelta(days=shift)).strftime("%Y-%m")

    def _md(m):
        try:
            d = datetime.date(_DATE_ANCHOR.year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return m.group(0)
        return (d + datetime.timedelta(days=shift)).strftime("%m-%d")

    return _RE_MD.sub(_md, _RE_MONTH.sub(_month, _RE_FULL.sub(_full, s)))


def _shift_dates(obj):
    """응답 dict/list 를 재귀적으로 돌며 날짜 문자열을 오늘 기준으로 이동."""
    if isinstance(obj, str):
        return _shift_one(obj)
    if isinstance(obj, list):
        return [_shift_dates(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _shift_dates(v) for k, v in obj.items()}
    return obj


FX = 1385  # 데모 환율(USDKRW)

# ---------- 종목 한글명(서버 name 필드 재현) ----------
NM = {
    "VOO": "뱅가드 S&P500", "QQQM": "인베스코 나스닥100", "SCHG": "슈왑 미국성장주",
    "VYM": "뱅가드 고배당", "DGRW": "위즈덤트리 배당성장", "BIL": "SPDR 미국 초단기국채",
    "MSFT": "마이크로소프트", "GOOGL": "알파벳", "AMZN": "아마존", "KO": "코카콜라",
    "JNJ": "존슨앤존슨", "V": "비자", "META": "메타", "AVGO": "브로드컴",
    "LLY": "일라이릴리", "JPM": "JP모건", "WMT": "월마트", "HD": "홈디포",
    "069500": "KODEX 200", "091160": "KODEX 반도체", "132030": "KODEX 골드선물(H)",
    "229200": "KODEX 코스닥150",
}


def _nm(s):
    return NM.get(s, "")


def _round2(n):
    return round(n * 100) / 100


# ---------- 토스(미국) 보유 ----------
def _build_toss():
    rows = [
        {"symbol": "VYM", "qty": 3, "avg_price": 118.4, "price": 129.6, "currency": "USD", "pnl_pct": 9.5},
        {"symbol": "DGRW", "qty": 3, "avg_price": 78.2, "price": 82.0, "currency": "USD", "pnl_pct": 4.9},
        {"symbol": "SCHG", "qty": 4, "avg_price": 24.3, "price": 25.9, "currency": "USD", "pnl_pct": 6.6},
        {"symbol": "KO", "qty": 2, "avg_price": 66.4, "price": 64.3, "currency": "USD", "pnl_pct": -3.1},
        {"symbol": "VOO", "qty": 1, "avg_price": 498.2, "price": 520.6, "currency": "USD", "pnl_pct": 4.5},
        {"symbol": "QQQM", "qty": 1, "avg_price": 190.4, "price": 202.8, "currency": "USD", "pnl_pct": 6.5},
        {"symbol": "JNJ", "qty": 2, "avg_price": 148.6, "price": 159.8, "currency": "USD", "pnl_pct": 7.5},
        {"symbol": "MSFT", "qty": 1, "avg_price": 390.0, "price": 438.2, "currency": "USD", "pnl_pct": 12.4},
        {"symbol": "V", "qty": 1, "avg_price": 298.5, "price": 289.3, "currency": "USD", "pnl_pct": -3.1},
        {"symbol": "META", "qty": 1, "avg_price": 560.5, "price": 592.4, "currency": "USD", "pnl_pct": 5.7},
        {"symbol": "BIL", "qty": 3, "avg_price": 91.4, "price": 91.6, "currency": "USD", "pnl_pct": 0.2},
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
        {"account": "ISA중개형", "market": "KR", "symbol": "069500", "qty": 32, "price": 42350, "currency": "KRW", "pnl_pct": 8.2},
        {"account": "ISA중개형", "market": "KR", "symbol": "229200", "qty": 50, "price": 13480, "currency": "KRW", "pnl_pct": 2.6},
        {"account": "ISA중개형", "market": "KR", "symbol": "132030", "qty": 25, "price": 18620, "currency": "KRW", "pnl_pct": -4.3},
        {"account": "연금저축", "market": "KR", "symbol": "091160", "qty": 40, "price": 41250, "currency": "KRW", "pnl_pct": 11.1},
        {"account": "연금저축", "market": "KR", "symbol": "069500", "qty": 20, "price": 42350, "currency": "KRW", "pnl_pct": -2.8},
        {"account": "연금저축", "market": "KR", "symbol": "132030", "qty": 30, "price": 18620, "currency": "KRW", "pnl_pct": 0.9},
        {"account": "소수점주식", "market": "US", "symbol": "VOO", "qty": 1, "price": 520.6, "currency": "USD", "pnl_pct": 6.1},
        {"account": "소수점주식", "market": "US", "symbol": "MSFT", "qty": 1, "price": 438.2, "currency": "USD", "pnl_pct": 12.4},
        {"account": "소수점주식", "market": "US", "symbol": "AVGO", "qty": 1, "price": 172.9, "currency": "USD", "pnl_pct": 5.4},
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
    {"symbol": "VYM", "name": _nm("VYM"), "qty": 16, "market": "US", "price": 129.6, "pnl_pct": 6.2, "value_krw": round(16 * 129.6 * FX), "bucket": "core"},
    {"symbol": "VOO", "name": _nm("VOO"), "qty": 4, "market": "US", "price": 520.6, "pnl_pct": 3.8, "value_krw": round(4 * 520.6 * FX), "bucket": "core"},
    {"symbol": "META", "name": _nm("META"), "qty": 1, "market": "US", "price": 592.4, "pnl_pct": 9.1, "value_krw": round(1 * 592.4 * FX), "bucket": "sat"},
    {"symbol": "AVGO", "name": _nm("AVGO"), "qty": 3, "market": "US", "price": 172.9, "pnl_pct": -6.4, "value_krw": round(3 * 172.9 * FX), "bucket": "exit"},
    {"symbol": "069500", "name": _nm("069500"), "qty": 50, "market": "KR", "price": 42350, "pnl_pct": 5.1, "value_krw": 50 * 42350, "bucket": "core"},
]
PAPER_INVESTED = sum(p["value_krw"] for p in PAPER_POS)
PAPER_INITIAL = 10000000
PAPER_TOTAL = 10000000 + 368000  # +약36.8만(가상, +3.68%)


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
    ["QQQM", 92.4, 202.8, 14.2, 31.5, True, 2.3, [4.8, 0.71]],
    ["SCHG", 78.1, 25.9, 6.1, 12.4, True, 1.1, [3.1, 0.66]],
    ["VOO", 71.5, 520.6, 4.9, 9.8, True, None, [2.4, 0.63]],
    ["DGRW", 66.2, 82.0, 4.5, 8.1, True, None, [2.2, 0.62]],
    ["VYM", 58.7, 129.6, 3.2, 6.4, True, None, [1.9, 0.60]],
    ["BIL", 52.3, 91.6, 0.2, 0.5, True, None, None],
])
SCN_KR = _mk_scn([
    ["091160", 88.0, 41250, 9.4, 18.2, True, 1.4, [3.6, 0.68]],
    ["069500", 81.3, 42350, 8.1, 15.6, True, None, [3.2, 0.65]],
    ["229200", 64.5, 13480, 3.1, 7.2, True, 2.1, [2.8, 0.61]],
    ["132030", 38.6, 18620, 1.1, 2.4, True, None, None],
])
SCN_US_SINGLE = _mk_scn([
    ["META", 92.4, 592.4, 14.2, 31.5, True, 2.3, [4.8, 0.71]],
    ["V", 48.1, 289.3, -3.1, 2.4, False, 1.1, None],
    ["AVGO", 61.7, 172.9, 5.7, 14.8, True, 1.9, [3.9, 0.64]],
    ["MSFT", 72.4, 438.2, 4.4, 9.1, True, None, None],
    ["AMZN", 66.8, 201.7, 3.9, 8.6, True, None, None],
    ["GOOGL", 77.2, 182.6, 6.8, 15.2, True, 1.3, None],
    ["LLY", 70.1, 812.4, 5.4, 13.1, True, None, None],
    ["JPM", 63.5, 268.1, 3.2, 7.4, True, None, None],
])
SCN_KR_SINGLE = _mk_scn([
    ["069500", 64.5, 42350, 3.1, 7.2, True, 2.1, [2.8, 0.61]],
    ["091160", 72.3, 41250, 5.6, 11.4, True, 1.8, [3.4, 0.66]],
    ["229200", 55.1, 13480, 4.2, 9.8, True, None, None],
    ["132030", 41.8, 18620, 1.4, 3.1, False, None, None],
])

BACKTEST = {"n": 4820, "horizon": 21, "ic": -0.06, "spread": -1.8, "high_avg": 1.4,
            "low_avg": 3.2, "hit": 46.2, "grade": "역상관"}


# ---------- 뉴스 ----------
NEWS = [
    {"symbol": "META", "score": 0.42, "polarity": "긍정", "sources": 9, "rvol": 2.3, "ret_1d": 2.1, "ret_5d": 6.4, "base1": 580.3, "base5": 556.8,
     "summary": "AI 광고 효율 개선·클라우드 매출 강세 · 신규 서비스 공개 · 목표주가 상향"},
    {"symbol": "VYM", "score": 0.18, "polarity": "긍정", "sources": 4, "rvol": 1.1, "ret_1d": 0.4, "ret_5d": 1.8, "base1": 129.1, "base5": 127.3,
     "summary": "배당 인상 발표 · 방어적 배당주 자금 유입 · 저변동 선호"},
    {"symbol": "V", "score": -0.22, "polarity": "부정", "sources": 6, "rvol": 1.9, "ret_1d": -1.4, "ret_5d": 2.6, "base1": 293.4, "base5": 281.9,
     "summary": "결제 성장 둔화 우려 · 수수료 규제 압박 · 경쟁 심화"},
    {"symbol": "KO", "score": -0.14, "polarity": "부정", "sources": 3, "rvol": 1.6, "ret_1d": -0.9, "ret_5d": -2.6, "base1": 64.9, "base5": 66.0,
     "summary": "원가 부담 마진 압박 · 신흥국 판매량 둔화"},
]


# ---------- 노출(룩스루) ----------
EXPO = {
    "total": round(TOSS_TOTAL + KIS_VAL), "n": 38, "partial": False, "top5_pct": 41.3,
    "top": [
        {"symbol": "MSFT", "name": _nm("MSFT"), "krw": round((TOSS_TOTAL + KIS_VAL) * 0.112), "pct": 11.2},
        {"symbol": "AMZN", "name": _nm("AMZN"), "krw": round((TOSS_TOTAL + KIS_VAL) * 0.093), "pct": 9.3},
        {"symbol": "META", "name": _nm("META"), "krw": round((TOSS_TOTAL + KIS_VAL) * 0.085), "pct": 8.5},
        {"symbol": "GOOGL", "name": _nm("GOOGL"), "krw": round((TOSS_TOTAL + KIS_VAL) * 0.060), "pct": 6.0},
        {"symbol": "AVGO", "name": _nm("AVGO"), "krw": round((TOSS_TOTAL + KIS_VAL) * 0.059), "pct": 5.9},
        {"symbol": "LLY", "name": _nm("LLY"), "krw": round((TOSS_TOTAL + KIS_VAL) * 0.049), "pct": 4.9},
        {"symbol": "JPM", "name": _nm("JPM"), "krw": round((TOSS_TOTAL + KIS_VAL) * 0.044), "pct": 4.4},
        {"symbol": "V", "name": _nm("V"), "krw": round((TOSS_TOTAL + KIS_VAL) * 0.036), "pct": 3.6},
        {"symbol": "WMT", "name": _nm("WMT"), "krw": round((TOSS_TOTAL + KIS_VAL) * 0.030), "pct": 3.0},
        {"symbol": "KO", "name": _nm("KO"), "krw": round((TOSS_TOTAL + KIS_VAL) * 0.028), "pct": 2.8},
    ],
}


# ---------- 제안 ----------
PROPOSALS = [
    {"id": 1, "ts": "2026-07-08 09:12", "symbol": "VYM", "side": "buy", "qty": 2, "currency": "USD", "ref_price": 129.6, "reason": "rebalance 6.4%->8.0%", "status": "pending"},
    {"id": 2, "ts": "2026-07-08 09:12", "symbol": "AVGO", "side": "sell", "qty": 8, "currency": "USD", "ref_price": 172.9, "reason": "stop_loss -6.4", "status": "pending"},
    {"id": 3, "ts": "2026-07-07 15:40", "symbol": "069500", "side": "buy", "qty": 3, "currency": "KRW", "ref_price": 42350, "reason": "rebalance 0.0%->5.0%", "status": "pending"},
]

FORECAST = {
    "VYM": {"symbol": "VYM", "last": 129.6, "horizon_days": 21, "prob_up": 0.60, "exp_return": 1.9, "band": {"p10": 124.2, "p50": 131.8, "p90": 138.9}, "exp_peak_pct": 5.2, "exp_trough_pct": -3.4},
    "AVGO": {"symbol": "AVGO", "last": 172.9, "horizon_days": 21, "prob_up": 0.44, "exp_return": -1.8, "band": {"p10": 148.6, "p50": 169.8, "p90": 194.7}, "exp_peak_pct": 9.8, "exp_trough_pct": -11.2},
    "069500": {"symbol": "069500", "last": 42350, "horizon_days": 21, "prob_up": 0.65, "exp_return": 3.2, "band": {"p10": 40800, "p50": 43100, "p90": 45600}, "exp_peak_pct": 6.4, "exp_trough_pct": -3.1},
}


# ---------- 예측 기록 / 정확도 ----------
PREDICTIONS = [
    {"id": 20, "made_at": "2026-07-01 08:00", "symbol": "META", "horizon_days": 21, "base_price": 566.4, "prob_up": 0.71, "exp_return": 4.8, "status": "open", "actual_return": None, "dir_hit": None, "band_hit": None},
    {"id": 19, "made_at": "2026-07-01 08:00", "symbol": "SCHG", "horizon_days": 21, "base_price": 24.6, "prob_up": 0.66, "exp_return": 3.1, "status": "open", "actual_return": None, "dir_hit": None, "band_hit": None},
    {"id": 18, "made_at": "2026-06-30 08:00", "symbol": "091160", "horizon_days": 21, "base_price": 39800, "prob_up": 0.68, "exp_return": 3.6, "status": "open", "actual_return": None, "dir_hit": None, "band_hit": None},
    {"id": 12, "made_at": "2026-06-05 08:00", "symbol": "VYM", "horizon_days": 21, "base_price": 123.7, "prob_up": 0.62, "exp_return": 2.1, "status": "evaluated", "actual_return": 4.7, "dir_hit": True, "band_hit": True},
    {"id": 11, "made_at": "2026-06-04 08:00", "symbol": "SCHG", "horizon_days": 21, "base_price": 24.2, "prob_up": 0.63, "exp_return": 2.4, "status": "evaluated", "actual_return": 5.1, "dir_hit": True, "band_hit": True},
    {"id": 10, "made_at": "2026-06-03 08:00", "symbol": "KO", "horizon_days": 21, "base_price": 66.5, "prob_up": 0.58, "exp_return": 1.7, "status": "evaluated", "actual_return": -3.9, "dir_hit": False, "band_hit": False},
    {"id": 9, "made_at": "2026-06-02 08:00", "symbol": "META", "horizon_days": 21, "base_price": 538.1, "prob_up": 0.70, "exp_return": 4.4, "status": "evaluated", "actual_return": 10.4, "dir_hit": True, "band_hit": False},
    {"id": 8, "made_at": "2026-06-01 08:00", "symbol": "VOO", "horizon_days": 21, "base_price": 498.4, "prob_up": 0.61, "exp_return": 1.9, "status": "evaluated", "actual_return": 3.0, "dir_hit": True, "band_hit": True},
    {"id": 7, "made_at": "2026-05-29 08:00", "symbol": "069500", "horizon_days": 21, "base_price": 43600, "prob_up": 0.55, "exp_return": 1.4, "status": "evaluated", "actual_return": -4.3, "dir_hit": False, "band_hit": True},
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
        "전체 자산": _series(11150000, 12000000, _N, [{"at": 9, "mag": 0.02}, {"at": 20, "mag": 0.015}]),
        "토스(미국)": _series(4300000, 4850000, _N, [{"at": 9, "mag": 0.025}, {"at": 20, "mag": 0.018}]),
        "ISA": _series(2260000, 2500000, _N, [{"at": 9, "mag": 0.015}, {"at": 20, "mag": 0.012}]),
        "연금": _series(2700000, 3050000, _N, [{"at": 9, "mag": 0.012}, {"at": 20, "mag": 0.01}]),
    },
}


# ---------- 현금 / 주문 ----------
CASH = {"KRW": 100000, "USD": 28.40}
ORDERS = [
    {"at": "07-07 22:31", "side": "BUY", "symbol": "VYM", "qty": 2, "price": 129.55, "amount": 259.1, "fee": 0.03, "currency": "USD", "pending": False},
    {"at": "07-07 22:05", "side": "BUY", "symbol": "SCHG", "qty": 2, "price": 25.82, "amount": 51.6, "fee": 0.04, "currency": "USD", "pending": False},
    {"at": "07-04 23:14", "side": "SELL", "symbol": "KO", "qty": 1, "price": 64.30, "amount": 64.3, "fee": 0.03, "currency": "USD", "pending": False},
    {"at": "07-03 22:48", "side": "BUY", "symbol": "QQQM", "qty": 1, "price": 202.10, "amount": 202.1, "fee": 0.02, "currency": "USD", "pending": False},
    {"at": "07-08 09:01", "side": "BUY", "symbol": "DGRW", "qty": 1, "price": 0, "amount": 0, "fee": 0, "currency": "USD", "pending": True},
    {"at": "07-01 22:36", "side": "BUY", "symbol": "MSFT", "qty": 1, "price": 430.40, "amount": 430.4, "fee": 0.05, "currency": "USD", "pending": False},
    {"at": "06-27 22:10", "side": "BUY", "symbol": "BIL", "qty": 2, "price": 91.55, "amount": 183.1, "fee": 0.00, "currency": "USD", "pending": False},
    {"at": "06-25 22:52", "side": "SELL", "symbol": "V", "qty": 1, "price": 292.30, "amount": 292.3, "fee": 0.09, "currency": "USD", "pending": False},
    {"at": "06-23 22:19", "side": "BUY", "symbol": "VOO", "qty": 1, "price": 498.40, "amount": 498.4, "fee": 0.02, "currency": "USD", "pending": False},
    {"at": "06-20 22:41", "side": "BUY", "symbol": "META", "qty": 1, "price": 560.50, "amount": 560.5, "fee": 0.11, "currency": "USD", "pending": False},
    {"at": "06-18 22:03", "side": "BUY", "symbol": "JNJ", "qty": 1, "price": 152.20, "amount": 152.2, "fee": 0.09, "currency": "USD", "pending": False},
]
KIS_ORDERS = [
    {"at": "07-08 09:05", "side": "BUY", "side_name": "매수", "account": "ISA중개형", "symbol": "069500", "name": _nm("069500"), "qty": 2, "filled": 0, "price": 42350, "amount": 84700, "pending": True},
    {"at": "07-07 13:22", "side": "BUY", "side_name": "매수", "account": "연금저축", "symbol": "091160", "name": _nm("091160"), "qty": 3, "filled": 3, "price": 41250, "amount": 123750, "pending": False},
    {"at": "07-04 10:41", "side": "SELL", "side_name": "매도", "account": "ISA중개형", "symbol": "229200", "name": _nm("229200"), "qty": 1, "filled": 1, "price": 13600, "amount": 13600, "pending": False},
    {"at": "07-02 11:08", "side": "BUY", "side_name": "매수", "account": "연금저축", "symbol": "132030", "name": _nm("132030"), "qty": 4, "filled": 4, "price": 18620, "amount": 74480, "pending": False},
    {"at": "06-30 09:33", "side": "BUY", "side_name": "매수", "account": "ISA중개형", "symbol": "132030", "name": _nm("132030"), "qty": 3, "filled": 3, "price": 18500, "amount": 55500, "pending": False},
    {"at": "06-26 14:02", "side": "BUY", "side_name": "매수", "account": "연금저축", "symbol": "069500", "name": _nm("069500"), "qty": 1, "filled": 1, "price": 42200, "amount": 42200, "pending": False},
]


# ---------- 모의 거래내역(페이징) ----------
PTR_ALL = [
    {"ts": "07-08 09:31", "symbol": "VYM", "side": "buy", "qty": 10, "filled": 10, "market": "US", "status": "체결", "fill_price": 129.55, "amount_krw": round(10 * 129.55 * FX)},
    {"ts": "07-08 09:31", "symbol": "META", "side": "buy", "qty": 1, "filled": 1, "market": "US", "status": "체결", "fill_price": 566.40, "amount_krw": round(1 * 566.4 * FX)},
    {"ts": "07-07 15:20", "symbol": "AVGO", "side": "sell", "qty": 4, "filled": 4, "market": "US", "status": "체결", "fill_price": 174.10, "amount_krw": round(4 * 174.1 * FX)},
    {"ts": "07-07 09:31", "symbol": "VOO", "side": "buy", "qty": 6, "filled": 6, "market": "US", "status": "체결", "fill_price": 511.10, "amount_krw": round(6 * 511.1 * FX)},
    {"ts": "07-04 10:02", "symbol": "069500", "side": "buy", "qty": 8, "filled": 8, "market": "KR", "status": "체결", "fill_price": 42000, "amount_krw": 8 * 42000},
    {"ts": "07-03 09:31", "symbol": "META", "side": "buy", "qty": 2, "filled": 1, "market": "US", "status": "부분체결", "fill_price": 559.90, "amount_krw": round(1 * 559.9 * FX)},
    {"ts": "07-02 15:38", "symbol": "VYM", "side": "sell", "qty": 3, "filled": 3, "market": "US", "status": "체결", "fill_price": 128.60, "amount_krw": round(3 * 128.6 * FX)},
    {"ts": "07-01 09:31", "symbol": "AVGO", "side": "buy", "qty": 5, "filled": 0, "market": "US", "status": "미체결", "fill_price": None, "amount_krw": None},
    {"ts": "06-30 10:14", "symbol": "VOO", "side": "buy", "qty": 2, "filled": 2, "market": "US", "status": "체결", "fill_price": 498.90, "amount_krw": round(2 * 498.9 * FX)},
    {"ts": "06-27 09:31", "symbol": "069500", "side": "sell", "qty": 5, "filled": 5, "market": "KR", "status": "체결", "fill_price": 41500, "amount_krw": 5 * 41500},
]


# ---------- 실현손익 ----------
REALIZED = {
    "fx": FX,
    "accounts": {
        "토스(나)": {"total_krw": 180000, "daily": {}, "monthly": {},
            "sells": [
                {"month": "2026-07", "date": "2026-07-04", "symbol": "KO", "qty": 1, "buy_avg": 66.60, "sell_price": 64.30, "currency": "USD", "realized": -2.3, "realized_krw": -3185},
                {"month": "2026-06", "date": "2026-06-25", "symbol": "V", "qty": 1, "buy_avg": 282.40, "sell_price": 292.30, "currency": "USD", "realized": 9.9, "realized_krw": 13712},
                {"month": "2026-06", "date": "2026-06-11", "symbol": "DGRW", "qty": 2, "buy_avg": 78.90, "sell_price": 82.20, "currency": "USD", "realized": 6.6, "realized_krw": 9141},
                {"month": "2026-05", "date": "2026-05-19", "symbol": "VYM", "qty": 2, "buy_avg": 116.80, "sell_price": 119.40, "currency": "USD", "realized": 5.2, "realized_krw": 7202},
            ]},
        "한투": {"total_krw": 130000, "daily": {}, "monthly": {},
            "sells": [
                {"month": "2026-07", "date": "2026-07-04", "symbol": "229200", "qty": 1, "buy_avg": 13000, "sell_price": 13600, "currency": "KRW", "realized": 600, "realized_krw": 600},
                {"month": "2026-06", "date": "2026-06-18", "symbol": "069500", "qty": 6, "buy_avg": 40200, "sell_price": 41500, "currency": "KRW", "realized": 7800, "realized_krw": 7800},
                {"month": "2026-05", "date": "2026-05-22", "symbol": "091160", "qty": 9, "buy_avg": 39900, "sell_price": 41000, "currency": "KRW", "realized": 9900, "realized_krw": 9900},
            ]},
        "모의": {"total_krw": 320000, "daily": {}, "monthly": {},
            "sells": [
                {"month": "2026-07", "date": "2026-07-07", "symbol": "AVGO", "qty": 4, "buy_avg": 176.80, "sell_price": 174.10, "currency": "USD", "realized": -10.8, "realized_krw": -14958},
                {"month": "2026-07", "date": "2026-07-02", "symbol": "VYM", "qty": 3, "buy_avg": 126.40, "sell_price": 128.60, "currency": "USD", "realized": 6.6, "realized_krw": 9141},
                {"month": "2026-06", "date": "2026-06-27", "symbol": "069500", "qty": 5, "buy_avg": 40100, "sell_price": 41500, "currency": "KRW", "realized": 7000, "realized_krw": 7000},
                {"month": "2026-06", "date": "2026-06-13", "symbol": "META", "qty": 2, "buy_avg": 508.0, "sell_price": 524.0, "currency": "USD", "realized": 32.0, "realized_krw": 44320},
            ]},
    },
    "total": {"daily": {}, "monthly": {}, "total_krw": 630000},
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
    "지금 데모 포트폴리오는 **배당+성장 균형형**으로 잘 짜여 있어요. 토스(미국)는 VYM·DGRW·VOO 같은 배당·지수 ETF가 중심이라 방어력이 좋고, MSFT·META로 성장 매운맛도 살짝 얹었네요(전체 약 **+7%대**).\n"
    "- **집중도 주의**: 룩스루로 뚫어보면 상위5개 기업에 **41%**가 몰려 있어요. 여러 ETF를 들고 있어도 속은 결국 마이크로소프트·아마존·메타라 분산 착시가 있어요.\n"
    "- **AVGO**는 손실(-6.4%)이고 변동성이 큰 종목이라, 목표 비중을 넘겼다면 조금 줄이는 걸 고려해볼 만해요.\n"
    "- 스크리너 점수는 **매수신호가 아니라 요즘 핫한 온도계**예요(신뢰도 역상관). 점수만 보고 추격매수는 피하세요.\n"
    "참고용 분석이에요 — 최종 판단은 직접 하세요! 🙂"
)


# ---------- 데모 실엔진 챗봇용: 합성 포트폴리오 컨텍스트 ----------
def demo_top_holdings(n: int = 3):
    """데모 합성 보유 상위 [(symbol, market)] — 데모 챗봇 포트폴리오 자동리서치용(실계좌 미접근)."""
    rows = [(p["symbol"], "US", p["value_krw"]) for p in TOSS_POS]
    rows += [(p["symbol"], p.get("market", "KR"), p["value_krw"]) for p in KIS_POS]
    rows.sort(key=lambda r: -r[2])
    seen, out = set(), []
    for sym, mk, _ in rows:
        if sym in seen:
            continue
        seen.add(sym)
        out.append((sym, mk))
        if len(out) >= n:
            break
    return out


def _mw(v) -> str:
    """만원/억원 단위 표기 — LLM이 콤마 원단위(538,000원)를 '538만원'으로 잘못 읽는
    단위 환각 방지(server._manwon과 동일 규칙, 데모는 stdlib 유지 위해 복제)."""
    v = float(v or 0)
    a = abs(v)
    if a >= 1e8:
        return f"{v/1e8:.1f}".rstrip("0").rstrip(".") + "억원"
    if a >= 1e4:
        return f"{v/1e4:.1f}".rstrip("0").rstrip(".") + "만원"
    return f"{round(v):,}원"


def demo_chat_context() -> str:
    """데모 챗봇이 LLM에 넘길 '합성 포트폴리오' 컨텍스트(실계좌 _chat_context 대체).
    실제 보유/금액은 절대 노출하지 않고 데모 합성 숫자만 사용한다."""
    L = ["[※ 데모 계정 — 아래 보유·금액은 모두 예시(합성) 데이터입니다]"]
    toss_tot = TOSS_TOTAL
    L.append(f"[토스(미국) 총 {_mw(toss_tot)}, 오늘 +0.6% / 전체 +7.1%]")
    for x in sorted(TOSS_POS, key=lambda z: -z["value_krw"])[:8]:
        L.append(f"  - {x['symbol']}({x.get('name','')}) {x['qty']:g}주 수익률 {x['pnl_pct']}% "
                 f"평가 {_mw(x['value_krw'])}")
    L.append(f"[한투 실계좌(데모) 총 {_mw(KIS_VAL)}]")
    for x in KIS_POS:
        L.append(f"  - [{x['account']}] {x['symbol']}({x.get('name','')}) {x['qty']:g}주 {x['pnl_pct']}%")
    L.append("[미국 추세 스크리너 상위(점수=상승세 순위, 매수신호 아님)]")
    for r in SCN_US[:6]:
        L.append(f"  - {r['symbol']} 점수 {r['score']} (1M {r['ret_1m']}% 3M {r['ret_3m']}%)")
    L.append("[국내(KR) 추세 스크리너 상위]")
    for r in SCN_KR[:4]:
        L.append(f"  - {r['symbol']} 점수 {r['score']} (1M {r['ret_1m']}% 3M {r['ret_3m']}%)")
    L.append(f"[모델 예측 적중률(실측): 방향 {CHATEVAL['accuracy']['acc']*100:.0f}% "
             f"(표본 {CHATEVAL['accuracy']['n']}건). 50%대면 동전던지기 수준이니 참고만]")
    L.append(f"[★스크리너 신뢰도: {BACKTEST['grade']} (IC {BACKTEST['ic']}). "
             f"점수 높은 종목 1개월 뒤 평균 {BACKTEST['high_avg']}% vs 낮은 종목 {BACKTEST['low_avg']}% "
             f"→ 이 배당/인컴 ETF군은 점수 추격매수 부적합]")
    L.append(f"[실질 노출(ETF 룩스루): 상위5 {EXPO['top5_pct']}% 집중 — "
             + ", ".join(f"{t['symbol']} {t['pct']}%" for t in EXPO['top'][:5])
             + ". 여러 ETF여도 실제론 이 기업들에 노출(분산 착시 주의)]")
    L.append("[계좌 매매제약] 연금저축=국내상장 비레버리지 ETF/ETN만. "
             "ISA중개형=국내상장 개별주/ETF. 소수점=해외포함 자유. 미국상장은 소수점·일반만(연금·ISA 직접매수 불가).")
    return "\n".join(L)


# ---------- RAG: 챗봇 정확도(chateval) / 이슈 히스토리 ----------
# 서버 chateval.report() 형태: {"accuracy": {n,hit,acc,open}, "calls": [...], "log": [...]}
CHATEVAL = {
    "accuracy": {"n": 6, "hit": 4, "acc": round(4 / 6, 4), "open": 2},
    "calls": [
        {"ts": "2026-07-05 21:14", "symbol": "META", "direction": "up", "base_price": 585.2, "horizon": 21, "status": "open", "q": "메타 지금 사도 될까?"},
        {"ts": "2026-07-03 22:02", "symbol": "AVGO", "direction": "down", "base_price": 178.4, "horizon": 21, "status": "open", "q": "브로드컴 손절해야 하나?"},
        {"ts": "2026-06-12 21:40", "symbol": "VYM", "direction": "up", "base_price": 123.7, "horizon": 21, "status": "evaluated", "actual_return": 4.7, "hit": True},
        {"ts": "2026-06-10 20:55", "symbol": "SCHG", "direction": "up", "base_price": 24.2, "horizon": 21, "status": "evaluated", "actual_return": 5.1, "hit": True},
        {"ts": "2026-06-08 22:18", "symbol": "KO", "direction": "up", "base_price": 66.5, "horizon": 21, "status": "evaluated", "actual_return": -3.9, "hit": False},
        {"ts": "2026-06-05 21:03", "symbol": "V", "direction": "down", "base_price": 298.0, "horizon": 21, "status": "evaluated", "actual_return": -2.4, "hit": True},
        {"ts": "2026-06-02 21:31", "symbol": "069500", "direction": "up", "base_price": 43600, "horizon": 21, "status": "evaluated", "actual_return": -4.3, "hit": False},
        {"ts": "2026-06-01 20:47", "symbol": "VOO", "direction": "up", "base_price": 498.9, "horizon": 21, "status": "evaluated", "actual_return": 3.0, "hit": True},
    ],
    "log": [
        {"ts": "2026-07-08T09:14", "who": "me", "user_msg": "지금 뭘 팔고 뭘 사면 좋을까?", "reply": CHAT_REPLY,
         "model": "claude-demo", "usage": {"total": 1240, "prompt": 980, "completion": 260},
         "system_prompt": "너는 참고용 투자분석 도우미야. 보유·스크리너·예측 근거만 사용하고 매수/매도를 단정하지 마.",
         "sources": "portfolio(toss,kis) · exposure(top5 41.3%) · screener(us/kr) · forecast · realized"},
        {"ts": "2026-07-07T22:31", "who": "me", "user_msg": "AVGO 지금 손절이 맞아?",
         "reply": "AVGO는 -6.4% 손실에 변동성이 큰 종목이라, 목표 비중을 넘겼다면 일부만 줄이는 분할 대응이 안전해요. 한 번에 전량 손절보다 규칙(비중/손절선) 기반으로 판단하세요. 참고용이에요.",
         "model": "claude-demo", "usage": {"total": 720, "prompt": 560, "completion": 160}},
        {"ts": "2026-07-05T21:14", "who": "spouse", "user_msg": "메타 더 담아도 될까?",
         "reply": "메타는 최근 흐름이 강하지만 스크리너 1등 종목은 되돌림(고무줄 반등) 위험이 있어요. 룩스루로 보면 이미 상위 노출 1위(11%)라 추가 매수 시 집중도가 더 커져요. 참고용이에요.",
         "model": "claude-demo", "usage": {"total": 690, "prompt": 540, "completion": 150}},
    ],
}

# 이슈 히스토리 (/api/issues) — 서버 반환은 dict 리스트
ISSUES = [
    {"date": "2026-07-08", "session": "US 장초반", "symbol": "META", "market": "US", "score": 0.42, "polarity": "긍정",
     "summary": "AI 광고 효율 개선 · 클라우드 매출 최고 · 목표주가 상향", "sources": 9, "ret_1d": 2.1, "ret_5d": 6.4, "impact": "상승반영", "rvol": 2.3, "ts": "2026-07-08T09:35:00"},
    {"date": "2026-07-08", "session": "US 장초반", "symbol": "V", "market": "US", "score": -0.22, "polarity": "부정",
     "summary": "결제 성장 둔화 우려 · 수수료 규제 압박", "sources": 6, "ret_1d": -1.4, "ret_5d": 2.6, "impact": "역행소화", "rvol": 1.9, "ts": "2026-07-08T09:35:00"},
    {"date": "2026-07-08", "session": "KR 마감", "symbol": "091160", "market": "KR", "score": 0.15, "polarity": "긍정",
     "summary": "반도체 업황 개선 기대 · 외국인 순매수", "sources": 5, "ret_1d": 0.8, "ret_5d": 3.1, "impact": "상승반영", "rvol": 2.1, "ts": "2026-07-08T15:40:00"},
    {"date": "2026-07-07", "session": "US 마감", "symbol": "KO", "market": "US", "score": -0.14, "polarity": "부정",
     "summary": "원가 부담 마진 압박 · 신흥국 판매량 둔화", "sources": 3, "ret_1d": -0.9, "ret_5d": -2.6, "impact": "하락반영", "rvol": 1.6, "ts": "2026-07-07T22:10:00"},
    {"date": "2026-07-07", "session": "US 장중", "symbol": "VYM", "market": "US", "score": 0.18, "polarity": "긍정",
     "summary": "배당 인상 발표 · 방어적 배당주 자금 유입", "sources": 4, "ret_1d": 0.4, "ret_5d": 1.8, "impact": "횡보", "rvol": 1.1, "ts": "2026-07-07T20:05:00"},
    {"date": "2026-07-06", "session": "KR 장초반", "symbol": "069500", "market": "KR", "score": 0.26, "polarity": "긍정",
     "summary": "코스피 강세 · 환율 안정 · 자금 유입", "sources": 7, "ret_1d": 1.2, "ret_5d": 4.1, "impact": "상승반영", "rvol": 1.4, "ts": "2026-07-06T09:32:00"},
]


# ============================================================
#  헬퍼 (JS의 portfolioResp/quotesResp/... 이식)
# ============================================================
def _portfolio_resp(who):
    if who == "spouse":  # 남편 = 토스만(간단 버전)
        pos = TOSS_POS[:5]
        tot = sum(p["value_krw"] for p in pos)
        return {"broker": "toss", "fx": FX, "cash": 45000, "total_krw": tot,
                "daily_pnl_pct": 0.6, "total_pnl_pct": 5.2,
                "daily_pnl_amt_krw": round(tot * 0.006),
                "total_pnl_amt_krw": round(tot * 0.052 / 1.052),
                "positions": pos}
    return {"broker": "toss", "fx": FX, "cash": 100000, "total_krw": TOSS_TOTAL + 100000,
            "daily_pnl_pct": 0.9, "total_pnl_pct": _round2((TOSS_TOTAL / TOSS_COST - 1) * 100),
            "daily_pnl_amt_krw": round((TOSS_TOTAL + 100000) * 0.009),
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
    rv = {"VYM": 1.1, "META": 2.3, "KO": 1.6, "069500": 2.1, "091160": 1.4, "AVGO": 1.8, "V": 1.9, "229200": 1.8}
    return {s: rv[s] for s in syms if s in rv}


def _kis_resp():
    val, cost = KIS_VAL, KIS_COST
    return {"total": val + 200000, "cash_krw": 200000, "fx": FX, "positions": KIS_POS,
            "total_pnl_pct": _round2((val / cost - 1) * 100), "total_pnl_amt_krw": round(val - cost),
            "daily_pnl_amt_krw": 16000, "daily_pnl_pct": 0.35}


def _paper_resp():
    hist_series = _series(9700000, 10368000, _N, [{"at": 9, "mag": 0.02}, {"at": 20, "mag": 0.015}])
    return {"cash": PAPER_TOTAL - PAPER_INVESTED, "total": PAPER_TOTAL, "invested": PAPER_INVESTED,
            "initial": PAPER_INITIAL, "ret_pct": _round2((PAPER_TOTAL / PAPER_INITIAL - 1) * 100),
            "positions": PAPER_POS,
            "strategy": {"regime": "risk_on", "core": ["VYM", "VOO", "069500"], "sat": ["META"]},
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
def _demo_api_raw(method: str, path: str, query: dict, body: dict | None) -> dict | None:
    """데모 모드 라우터. 알 수 없는 경로 / health 는 None 반환(호출자가 {}로 변환)."""
    query = query or {}

    def _get(k, default=""):
        v = query.get(k, default)
        return v if v is not None else default

    def _split_syms(k):
        return [s for s in str(_get(k, "")).split(",") if s]

    # POST /api/chat
    if path == "/api/chat":
        return {"reply": CHAT_REPLY, "cached": False, "session_id": 1, "message_id": 1}

    # 알림센터(데모: 예시 1건만 — 실계정 알림 미노출)
    if path == "/api/notifications":
        return {"items": [{"ts": 0, "text": "🔔 (데모) 실계정에선 자동매매·리서치 알림이 여기에 모여요"}]}

    # ISA 자동매매(실계좌 기능 — 데모는 상태 숨김·무동작. 실주문 경로 완전 차단)
    if path == "/api/isa/status":
        return {"auto": False, "status": None, "pending": None}
    if path.startswith("/api/isa/"):
        return {"ok": False}

    # Chat 2.0(데모: 합성이라 저장 안 함 → 빈 목록/무동작으로 UI만 정상 동작)
    if path == "/api/chat/sessions":
        return {"sessions": [], "stats": {"up": 0, "down": 0}}
    if path == "/api/chat/messages":
        return {"messages": []}
    if path == "/api/chat/new":
        return {"session_id": 1}
    if path == "/api/chat/search":
        return {"results": []}
    if path == "/api/chat/rate":
        return {"ok": True}
    if path == "/api/chat/summarize":
        return {"summary": ""}

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


def demo_api(method: str, path: str, query: dict, body: dict | None) -> dict | None:
    """공개 진입점 — 원 라우터 결과의 모든 날짜를 오늘 기준으로 이동해 반환."""
    return _shift_dates(_demo_api_raw(method, path, query, body))


if __name__ == "__main__":
    # ---------- 셀프 테스트: 모든 엔드포인트 호출 + 상위 키 검증 ----------
    checks = [
        ("GET", "/api/names", {}, None, "dict", ["names"]),
        ("GET", "/api/market-status", {}, None, "dict", ["kr_open"]),
        ("GET", "/api/glossary", {}, None, "dict", ["terms"]),
        ("GET", "/api/portfolio", {"who": "me"}, None, "dict", ["broker", "positions", "total_krw"]),
        ("GET", "/api/portfolio", {"who": "spouse"}, None, "dict", ["broker", "positions"]),
        ("GET", "/api/quotes", {"symbols": "VYM,META,069500"}, None, "dict", ["VYM", "META"]),
        ("GET", "/api/volume", {"symbols": "VYM,META"}, None, "dict", ["VYM", "META"]),
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
        ("GET", "/api/forecast/VYM", {}, None, "dict", ["symbol", "band", "prob_up"]),
        ("GET", "/api/forecast/UNKNOWN", {}, None, "dict", ["error", "symbol"]),
        ("GET", "/api/news", {"symbols": ""}, None, "list", None),
        ("GET", "/api/news", {"symbols": "META"}, None, "list", None),
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
