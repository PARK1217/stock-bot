"""종목명 자동 학습·영구 저장.

증권사(토스/한투/KIS)가 보유·잔고 응답에 실어주는 종목명을 Redis 해시에 누적한다.
→ 한 번 사서 보유한 종목은 이름이 영구 기억되어, 나중에 팔아도 거래내역·노출 등에서
   계속 종목명이 표시된다(수동으로 코드에 이름을 추가할 필요 없음).

한글 라벨은 프론트의 KNM(시드 맵)이 우선이고, 여기서 학습한 증권사 이름(US는 영어)은
KNM에 없는 종목의 자동 폴백으로 쓰인다.
"""
from __future__ import annotations

import redis

from bot.config import settings

_r = redis.from_url(settings.redis_url)
_KEY = "names:learned"


def learn(symbol: str, name: str) -> None:
    """심볼→이름 학습(영구 저장). 빈 값·티커와 동일한 이름은 무시."""
    if not symbol or not name:
        return
    name = str(name).strip()
    if not name or name == symbol:
        return
    try:
        _r.hset(_KEY, symbol, name)
    except Exception:  # noqa: BLE001  (이름 학습 실패는 치명적 아님)
        pass


def learn_positions(positions) -> None:
    """[{symbol, name}, ...]에서 일괄 학습."""
    for p in positions or []:
        try:
            learn(p.get("symbol", ""), p.get("name") or "")
        except Exception:  # noqa: BLE001
            pass


def all_learned() -> dict:
    """학습된 전체 심볼→이름 맵."""
    try:
        return {k.decode(): v.decode() for k, v in _r.hgetall(_KEY).items()}
    except Exception:  # noqa: BLE001
        return {}


# 정적 한글 이름 시드 — 워치리스트·룩스루·단일종목 등 미보유 종목도 이름 표시.
# (학습된 증권사명보다 우선 = 한글 우선. 대시보드·RAG평가 페이지 공통 사용.)
SEED = {
    "069500": "KODEX 200", "133690": "TIGER 미국나스닥100", "360750": "TIGER 미국S&P500",
    "379800": "KODEX 미국S&P500", "458730": "TIGER 미국배당다우존스", "161510": "PLUS 고배당주",
    "329200": "TIGER 리츠부동산인프라", "273130": "KODEX 종합채권액티브", "210780": "TIGER 코스피고배당",
    "122630": "KODEX 레버리지", "233740": "KODEX 코스닥150레버리지", "0021E0": "ACE TDF2050액티브",
    "484790": "KODEX 미국30년국채",
    "SCHD": "미국 배당", "VIG": "배당성장", "DGRO": "배당성장", "JEPI": "미국 커버드콜",
    "JEPQ": "나스닥 커버드콜", "SPYI": "S&P 커버드콜", "QQQI": "나스닥 커버드콜",
    "O": "리얼티인컴(리츠)", "SOXL": "반도체 3배", "TQQQ": "나스닥 3배", "NVDA": "엔비디아",
    "AAPL": "애플", "TSLA": "테슬라", "SPY": "미국 S&P500", "QQQ": "나스닥100", "VOO": "미국 S&P500",
    "BOXX": "초단기 채권", "SGOV": "미국 단기국채", "GLDM": "금 ETF", "APLE": "애플호스피탈리티(리츠)",
    "MSFT": "마이크로소프트", "AMZN": "아마존", "AVGO": "브로드컴", "META": "메타(페이스북)",
    "GOOGL": "알파벳(구글)", "GOOG": "알파벳(구글)", "BRK-B": "버크셔해서웨이", "LLY": "일라이릴리",
    "JPM": "JP모건", "V": "비자", "UNH": "유나이티드헬스", "XOM": "엑슨모빌", "MA": "마스터카드",
    "AMD": "AMD", "NFLX": "넷플릭스", "COST": "코스트코", "HD": "홈디포", "ABBV": "애브비",
    "KO": "코카콜라", "PEP": "펩시코", "MRK": "머크", "WMT": "월마트", "CVX": "셰브론",
    "BAC": "뱅크오브아메리카", "JNJ": "존슨앤드존슨", "PG": "P&G", "CRM": "세일즈포스", "ORCL": "오라클",
    "DIVO": "배당 인컴", "HDV": "고배당", "QYLD": "나스닥 커버드콜", "SPHD": "고배당 저변동",
    "VYM": "고배당", "SCHY": "미국外 배당", "SPYD": "S&P 고배당", "NOBL": "배당귀족", "DGRW": "배당성장",
    "PLTR": "팔란티어", "005930": "삼성전자", "000660": "SK하이닉스", "373220": "LG에너지솔루션",
    "207940": "삼성바이오로직스", "005380": "현대차", "000270": "기아", "035420": "NAVER",
    "035720": "카카오", "005490": "POSCO홀딩스", "068270": "셀트리온", "105560": "KB금융", "012330": "현대모비스",
}


def resolved() -> dict:
    """학습분 + 정적 한글 시드 병합(시드=한글 우선)."""
    return {**all_learned(), **SEED}
