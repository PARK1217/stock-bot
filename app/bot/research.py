"""온디맨드 종목 리서치 — 챗봇이 '아무 종목'(미국/한국, 보유 무관) 질문에 답하도록
추세·이슈·웹반응·미래(확률) 전망을 즉석에서 모아 LLM 컨텍스트로 넘긴다.

설계 원칙: 예측/감성은 재구현하지 않고 기존 모듈(forecast·news·screener)을 그대로 재사용.
모든 네트워크 경로(KIS=집IP 필요, Tavily=키 필요)는 없어도 조용히 degrade — 절대 raise 안 함.

공개 API:
  resolve_query_tickers(text)  → 질문에서 종목 감지 (최대 3개)
  tavily_search(query, ...)    → 웹 뉴스 반응 검색 (키 없으면 [])
  research(symbol, market)     → 추세+예측+감성+반응 취합 dict (30분 캐시)
  research_block(symbol, mkt)  → 위를 한국어 컴팩트 텍스트 블록으로 포맷(LLM 프롬프트용)
"""
from __future__ import annotations

import json
import logging
import re

import httpx
import redis

from bot.config import settings

log = logging.getLogger(__name__)
_r = redis.from_url(settings.redis_url)


# ────────────────────────── 종목명 → 심볼/코드 사전 ──────────────────────────
# 미국: 한/영 회사명 → 티커 (대표 40여종)
NAME2SYM: dict[str, str] = {
    # 반도체/AI
    "엔비디아": "NVDA", "nvidia": "NVDA",
    "amd": "AMD", "에이엠디": "AMD",
    "브로드컴": "AVGO", "broadcom": "AVGO",
    "인텔": "INTC", "intel": "INTC",
    "마이크론": "MU", "micron": "MU",
    "퀄컴": "QCOM", "qualcomm": "QCOM",
    "tsmc": "TSM", "티에스엠씨": "TSM", "타이완반도체": "TSM",
    "asml": "ASML", "에이에스엠엘": "ASML",
    "arm": "ARM", "암홀딩스": "ARM",
    "슈퍼마이크로": "SMCI", "supermicro": "SMCI",
    # 빅테크/플랫폼
    "애플": "AAPL", "apple": "AAPL",
    "마이크로소프트": "MSFT", "microsoft": "MSFT", "ms": "MSFT",
    "구글": "GOOGL", "알파벳": "GOOGL", "google": "GOOGL", "alphabet": "GOOGL",
    "아마존": "AMZN", "amazon": "AMZN",
    "메타": "META", "페이스북": "META", "meta": "META", "facebook": "META",
    "넷플릭스": "NFLX", "netflix": "NFLX",
    "테슬라": "TSLA", "tesla": "TSLA",
    "팔란티어": "PLTR", "palantir": "PLTR",
    "오라클": "ORCL", "oracle": "ORCL",
    "세일즈포스": "CRM", "salesforce": "CRM",
    "어도비": "ADBE", "adobe": "ADBE",
    "우버": "UBER", "uber": "UBER",
    "쇼피파이": "SHOP", "shopify": "SHOP",
    "스노우플레이크": "SNOW", "snowflake": "SNOW",
    "크라우드스트라이크": "CRWD", "crowdstrike": "CRWD",
    "코인베이스": "COIN", "coinbase": "COIN",
    "마이크로스트래티지": "MSTR", "microstrategy": "MSTR", "스트래티지": "MSTR",
    # 자동차/EV/우주
    "리비안": "RIVN", "rivian": "RIVN",
    "루시드": "LCID", "lucid": "LCID",
    # 금융/헬스/소비
    "버크셔": "BRK-B", "버크셔해서웨이": "BRK-B", "berkshire": "BRK-B",
    "제이피모건": "JPM", "jp모건": "JPM", "jpmorgan": "JPM",
    "뱅크오브아메리카": "BAC", "bofa": "BAC",
    "비자": "V", "visa": "V",
    "마스터카드": "MA", "mastercard": "MA",
    "일라이릴리": "LLY", "릴리": "LLY", "lilly": "LLY",
    "유나이티드헬스": "UNH", "unitedhealth": "UNH",
    "존슨앤존슨": "JNJ", "존슨앤드존슨": "JNJ",
    "화이자": "PFE", "pfizer": "PFE",
    "코카콜라": "KO", "coca-cola": "KO", "coke": "KO",
    "펩시": "PEP", "펩시코": "PEP", "pepsi": "PEP",
    "월마트": "WMT", "walmart": "WMT",
    "코스트코": "COST", "costco": "COST",
    "맥도날드": "MCD", "mcdonalds": "MCD",
    "나이키": "NKE", "nike": "NKE",
    "스타벅스": "SBUX", "starbucks": "SBUX",
    "디즈니": "DIS", "disney": "DIS",
    "엑슨모빌": "XOM", "엑슨": "XOM", "exxon": "XOM",
    "셰브론": "CVX", "chevron": "CVX",
    "보잉": "BA", "boeing": "BA",
}

# 한국: 회사/ETF명 → 6자리 코드 (대표 130여종). names.SEED / 스크리너 유니버스와 합쳐 확장됨.
KR_NAME2CODE: dict[str, str] = {
    # 시총 상위 대형주
    "삼성전자": "005930", "삼성전자우": "005935",
    "sk하이닉스": "000660", "하이닉스": "000660",
    "lg에너지솔루션": "373220", "엘지에너지솔루션": "373220", "lg엔솔": "373220",
    "삼성바이오로직스": "207940", "삼성바이오": "207940",
    "현대차": "005380", "현대자동차": "005380",
    "기아": "000270", "기아차": "000270",
    "네이버": "035420", "naver": "035420",
    "카카오": "035720", "kakao": "035720",
    "posco홀딩스": "005490", "포스코홀딩스": "005490", "포스코": "005490",
    "셀트리온": "068270",
    "kb금융": "105560", "케이비금융": "105560",
    "현대모비스": "012330",
    "lg화학": "051910", "엘지화학": "051910",
    "삼성sdi": "006400", "삼성에스디아이": "006400",
    "삼성물산": "028260",
    "신한지주": "055550", "신한금융": "055550",
    "하나금융지주": "086790", "하나금융": "086790",
    "우리금융지주": "316140", "우리금융": "316140",
    "메리츠금융지주": "138040", "메리츠금융": "138040",
    "삼성생명": "032830",
    "삼성화재": "000810",
    "sk이노베이션": "096770", "에스케이이노베이션": "096770",
    "sk텔레콤": "017670", "skt": "017670", "sk텔레콤": "017670",
    "kt": "030200", "케이티": "030200",
    "lg유플러스": "032640", "엘지유플러스": "032640",
    "kt&g": "033780", "케이티앤지": "033780",
    "삼성전기": "009150",
    "sk스퀘어": "402340",
    "lg전자": "066570", "엘지전자": "066570",
    "lg": "003550", "엘지": "003550",
    "sk": "034730",
    "한국전력": "015760", "한전": "015760",
    "포스코퓨처엠": "003670", "포스코케미칼": "003670",
    "고려아연": "010130",
    "hmm": "011200",
    "두산에너빌리티": "034020", "두산에너빌러티": "034020",
    "두산": "000150",
    "한화에어로스페이스": "012450", "한화에어로": "012450",
    "한화솔루션": "009830",
    "한화": "000880",
    "현대중공업": "329180", "hd현대중공업": "329180",
    "hd현대": "267250",
    "hd한국조선해양": "009540", "한국조선해양": "009540",
    "삼성중공업": "010140",
    "대한항공": "003490",
    "카카오뱅크": "323410", "카뱅": "323410",
    "카카오페이": "377300",
    "크래프톤": "259960",
    "엔씨소프트": "036570", "엔씨": "036570",
    "넷마블": "251270",
    "펄어비스": "263750",
    "하이브": "352820", "hybe": "352820",
    "에스엠": "041510", "sm엔터": "041510",
    "제이와이피": "035900", "jyp": "035900", "jyp엔터": "035900",
    "와이지엔터": "122870", "yg엔터": "122870",
    "cj제일제당": "097950",
    "cj": "001040",
    "롯데케미칼": "011170",
    "lg생활건강": "051900", "엘지생활건강": "051900",
    "아모레퍼시픽": "090430", "아모레": "090430",
    "코스맥스": "192820",
    "한국콜마": "161890",
    "오리온": "271560",
    "농심": "004370",
    "삼양식품": "003230",
    "cj대한통운": "000120",
    "현대글로비스": "086280", "글로비스": "086280",
    "s-oil": "010950", "에스오일": "010950",
    "gs": "078930",
    "금호석유": "011780",
    "효성": "004800",
    "코웨이": "021240",
    "sk바이오팜": "326030",
    "유한양행": "000100",
    "한미약품": "128940",
    "대웅제약": "069620",
    "녹십자": "006280", "gc녹십자": "006280",
    "sk바이오사이언스": "302440",
    "알테오젠": "196170",
    "에코프로": "086520",
    "에코프로비엠": "247540",
    "에코프로에이치엔": "383310",
    "엘앤에프": "066970", "l&f": "066970",
    "포스코dx": "022100",
    "천보": "278280",
    "일진머티리얼즈": "020150",
    "레인보우로보틱스": "277810", "레인보우": "277810",
    "두산로보틱스": "454910",
    "삼성에스디에스": "018260", "삼성sds": "018260",
    "더존비즈온": "012510",
    "리노공업": "058470",
    "원익ips": "240810",
    "주성엔지니어링": "036930",
    "hpsp": "403870",
    "이오테크닉스": "039030",
    "한미반도체": "042700",
    "동진쎄미켐": "005290",
    "덕산네오룩스": "213420",
    "솔브레인": "357780",
    "isc": "095340",
    "네패스": "033640",
    "티씨케이": "064760",
    "대주전자재료": "078600",
    "씨젠": "096530",
    "휴젤": "145020",
    "클래시스": "214150",
    "루닛": "328130",
    "뷰노": "338220",
    "카나리아바이오": "016790",
    "삼천당제약": "000250",
    "펩트론": "087010",
    "에이비엘바이오": "298380",
    "리가켐바이오": "141080", "리가켐": "141080",
    "메디톡스": "086900",
    "파마리서치": "214450",
    "hlb": "028300", "에이치엘비": "028300",
    "신성델타테크": "065350",
    "포스코인터내셔널": "047050",
    "sk아이이테크놀로지": "361610",
    "롯데에너지머티리얼즈": "020150",
    "덴티움": "145720",
    "오스템임플란트": "048260",
    "현대건설": "000720",
    "gs건설": "006360",
    "대우건설": "047040",
    "dl이앤씨": "375500",
    "삼성엔지니어링": "028050",
    "한온시스템": "018880",
    "만도": "204320", "hl만도": "204320",
    "현대위아": "011210",
    "쌍용c&e": "003410",
    # 대표 국내상장 ETF
    "kodex 200": "069500", "코덱스200": "069500", "kodex200": "069500",
    "tiger 미국나스닥100": "133690", "타이거 나스닥100": "133690",
    "tiger 미국s&p500": "360750", "타이거 미국s&p500": "360750",
    "kodex 미국s&p500": "379800",
    "tiger 미국배당다우존스": "458730", "타이거 미국배당다우존스": "458730", "티미다": "458730",
    "plus 고배당주": "161510",
    "tiger 리츠부동산인프라": "329200",
    "kodex 종합채권액티브": "273130",
    "tiger 코스피고배당": "210780",
    "kodex 레버리지": "122630", "코덱스 레버리지": "122630",
    "kodex 코스닥150레버리지": "233740",
    "kodex 미국30년국채": "484790",
    "kodex 코스닥150": "229200",
    "kodex 인버스": "114800",
    "kodex 200선물인버스2x": "252670", "곱버스": "252670",
    "tiger 2차전지테마": "305540",
    "kodex 2차전지산업": "305720",
    "tiger 반도체": "091230",
    "kodex 반도체": "091160",
    "tiger 차이나전기차solactive": "371460",
    "tiger 미국테크top10": "381170", "타이거 미국테크top10": "381170",
    "kodex 미국나스닥100": "379810",
    "tiger 미국필라델피아반도체나스닥": "381180",
    "sol 미국배당다우존스": "446720",
    "ace 미국배당다우존스": "402970",
}

# 티커처럼 보이지만 종목이 아닌 대문자 토큰 (오탐 방지)
_US_STOPLIST = {
    "ETF", "AI", "US", "KR", "USA", "MDD", "PER", "PBR", "ROE", "EPS", "IPO",
    "CEO", "CFO", "GDP", "CPI", "PCE", "FED", "FOMC", "ECB", "BOK", "ATH",
    "YOY", "QOQ", "MOM", "MA", "RSI", "MACD", "TA", "FA", "ETN", "REIT",
    "NYSE", "OK", "TV", "PC", "IT", "OS", "API", "CEO", "S&P", "DOW", "AND",
    "OR", "THE", "FOR", "BUY", "SELL", "HOLD", "YOLO", "FYI", "LOL", "IMO",
    "Q1", "Q2", "Q3", "Q4", "H1", "H2", "FY", "YTD", "ROI", "ROA",
    # 한국 ETF 브랜드 접두어(미국 티커로 오인 방지)
    "TIGER", "KODEX", "PLUS", "ACE", "SOL", "KBSTAR", "ARIRANG", "HANARO",
    # 한국 그룹/지주 약칭(미국 티커 오인 방지 → KR 경로로 처리)
    "SK", "LG", "GS", "CJ", "KT", "HD", "DL", "DB",
}

# 대문자 티커 후보 정규식 (1~5글자). ASCII 글자 룩어라운드 사용 —
# 한글 조사가 붙어도(예: "NVDA랑") \b 가 아니라 여기서 잘 잡히게.
_US_TOKEN = re.compile(r"(?<![A-Za-z])[A-Z]{1,5}(?![A-Za-z])")
_KR_CODE = re.compile(r"(?<!\d)\d{6}(?!\d)")


def _build_kr_index() -> dict[str, str]:
    """KR 이름→코드 인덱스: 커리큘럼 사전 + names.SEED + 스크리너 유니버스 + 학습된 이름."""
    idx: dict[str, str] = {}
    # 1) 큐레이션 사전(소문자 키)
    for k, v in KR_NAME2CODE.items():
        idx[k.lower().replace(" ", "")] = v
    # 2) names.SEED (코드→한글) 역매핑 + 학습된 이름
    try:
        from bot import names
        merged = {}
        try:
            merged.update(names.all_learned())
        except Exception:  # noqa: BLE001
            pass
        merged.update(names.SEED)
        for code, nm in merged.items():
            if isinstance(code, str) and code.isdigit() and len(code) == 6 and nm:
                idx.setdefault(str(nm).lower().replace(" ", ""), code)
    except Exception:  # noqa: BLE001
        pass
    return idx


def _kr_index() -> dict[str, str]:
    """빌드 1회 후 프로세스 캐시."""
    global _KR_IDX
    try:
        return _KR_IDX  # type: ignore[name-defined]
    except NameError:
        _KR_IDX = _build_kr_index()  # noqa: F841
        return _KR_IDX


def _kr_code2name() -> dict[str, str]:
    """KR 코드→대표이름(KR_NAME2CODE 역매핑, 코드당 가장 짧은=대표 이름). 1회 캐시."""
    global _KR_C2N
    try:
        return _KR_C2N  # type: ignore[name-defined]
    except NameError:
        m: dict[str, str] = {}
        for nm, code in KR_NAME2CODE.items():
            if code not in m or len(nm) < len(m[code]):
                m[code] = nm
        _KR_C2N = m  # noqa: F841
        return m


def _display_name(symbol: str, market: str) -> str:
    """심볼/코드 → 한글(가능하면) 표시 이름."""
    try:
        from bot import names
        nm = names.resolved().get(symbol)
        if nm:
            return nm
    except Exception:  # noqa: BLE001
        pass
    if market == "US":
        for k, v in NAME2SYM.items():          # US NAME2SYM 역매핑(한글 우선)
            if v == symbol and not k.isascii():
                return k
    else:
        nm = _kr_code2name().get(symbol)        # KR 코드→이름
        if nm:
            return nm
    return symbol


def resolve_query_tickers(text: str) -> list[dict]:
    """질문 문장에서 종목 감지 → 최대 3개 [{symbol, market, name}] (중복 제거)."""
    if not text:
        return []
    low = text.lower()
    found: list[dict] = []
    seen: set[str] = set()

    def add(symbol: str, market: str):
        key = f"{market}:{symbol}"
        if key in seen:
            return
        seen.add(key)
        found.append({"symbol": symbol, "market": market,
                      "name": _display_name(symbol, market)})

    kr_idx = _kr_index()

    # 1) 명시적 6자리 코드
    for m in _KR_CODE.findall(text):
        add(m, "KR")
        if len(found) >= 3:
            return found[:3]

    # 2) 한국 회사/ETF명 (긴 이름부터 매칭해 부분일치 오탐 방지)
    compact = low.replace(" ", "")
    for name in sorted(kr_idx.keys(), key=len, reverse=True):
        if len(name) < 2:
            continue
        if name in compact:
            add(kr_idx[name], "KR")
            if len(found) >= 3:
                return found[:3]

    # 3) 미국 회사명 (한/영)
    for name in sorted(NAME2SYM.keys(), key=len, reverse=True):
        if len(name) < 2:
            continue
        if name in low:
            add(NAME2SYM[name], "US")
            if len(found) >= 3:
                return found[:3]

    # 4) 대문자 티커 토큰 (스톱리스트 제외)
    for tok in _US_TOKEN.findall(text):
        if tok in _US_STOPLIST or len(tok) < 2:
            continue
        add(tok, "US")
        if len(found) >= 3:
            return found[:3]

    return found[:3]


# 그룹/지주 접두어 — 단독으로 오면 여러 계열사라 임의판단 말고 되물어야 함
_AMBIG_PREFIX = {"삼성", "현대", "lg", "엘지", "sk", "에스케이", "한화", "두산",
                 "포스코", "롯데", "cj", "gs", "지에스", "한진", "효성", "금호",
                 "코오롱", "신세계"}


def _prefix_candidates(term: str, limit: int = 6) -> list[dict]:
    """KR 인덱스에서 term 으로 시작하는 종목들 → [{symbol,name}] (코드 dedupe, 짧은이름 우선)."""
    t = term.lower().replace(" ", "")
    if len(t) < 2:
        return []
    by_code: dict[str, str] = {}
    for name, code in _kr_index().items():
        if name.startswith(t) and code not in by_code and not name.endswith("우"):
            by_code[code] = _display_name(code, "KR")
    items = sorted(by_code.items(), key=lambda kv: len(kv[1]))   # 짧은(대표) 이름 우선
    return [{"symbol": c, "name": n} for c, n in items[:limit]]


def resolve_query(text: str) -> dict:
    """종목 감지 + 애매성 판정.
    반환 {confident:[{symbol,market,name}], clarify:[{term,candidates:[{symbol,name}]}]}
    - confident: 정확 매칭(코드/풀네임/US티커) → 바로 리서치.
    - clarify: '삼성·현대·SK'처럼 계열사가 여럿이라 특정 안 되는 그룹 → 챗봇이 되물음(임의판단 금지)."""
    confident = resolve_query_tickers(text)
    compact = (text or "").lower().replace(" ", "")
    clarify: list[dict] = []
    drop_syms: set[str] = set()
    for g in _AMBIG_PREFIX:
        if g not in compact:
            continue
        # 더 구체적인 계열사명이 문장에 있으면(예: 'sk하이닉스') 사용자가 특정한 것 → 안 물음
        specific = [nm for nm in _kr_index()
                    if nm.startswith(g) and len(nm) > len(g) and nm in compact]
        if specific:
            drop_syms.add(_kr_index().get(g, ""))   # 그래도 붙은 지주사 단독매칭은 제거
            continue
        cands = _prefix_candidates(g, 6)
        if len(cands) >= 2:
            clarify.append({"term": g, "candidates": cands})
            drop_syms |= {c["symbol"] for c in cands}
    if drop_syms:
        confident = [c for c in confident if c["symbol"] not in drop_syms]
    return {"confident": confident, "clarify": clarify}


# ────────────────────────────── Tavily 웹 검색 ──────────────────────────────
def tavily_search(query: str, max_results: int = 5) -> list[dict]:
    """Tavily 뉴스 검색. 키 없거나 오류면 [] (never raise)."""
    key = getattr(settings, "tavily_api_key", "") or ""
    if not key or not query:
        return []
    try:
        r = httpx.post("https://api.tavily.com/search",
                       json={"api_key": key, "query": query, "topic": "news",
                             "search_depth": "basic", "days": 14,
                             "max_results": max_results},
                       timeout=6)
        r.raise_for_status()
        data = r.json()
        out = []
        for item in data.get("results", []) or []:
            row = {"title": item.get("title", ""),
                   "content": item.get("content", ""),
                   "url": item.get("url", "")}
            if item.get("published_date"):
                row["published"] = item["published_date"]
            out.append(row)
        return out
    except Exception:  # noqa: BLE001
        log.warning("tavily 검색 실패: %s", query)
        return []


_TAG_RE = re.compile(r"<[^>]+>")


def _clean_naver(s: str) -> str:
    """네이버 응답의 <b> 태그·HTML 엔티티 제거."""
    import html
    return html.unescape(_TAG_RE.sub("", s or "")).strip()


def naver_search(query: str, kind: str = "news", display: int = 3) -> list[dict]:
    """네이버 검색(뉴스·블로그) — KR 반응 특화. 키 없거나 오류면 [] (never raise).
    kind='news'(뉴스) | 'blog'(블로그=개미 반응). 결과 {title,content,url,published}."""
    cid = getattr(settings, "naver_client_id", "") or ""
    sec = getattr(settings, "naver_client_secret", "") or ""
    if not cid or not sec or not query:
        return []
    ep = "blog" if kind == "blog" else "news"
    try:
        r = httpx.get(f"https://openapi.naver.com/v1/search/{ep}.json",
                      params={"query": query, "display": display, "sort": "sim"},
                      headers={"X-Naver-Client-Id": cid,
                               "X-Naver-Client-Secret": sec},
                      timeout=6)
        r.raise_for_status()
        out = []
        for it in r.json().get("items", []) or []:
            out.append({"title": _clean_naver(it.get("title", "")),
                        "content": _clean_naver(it.get("description", "")),
                        "url": it.get("originallink") or it.get("link", ""),
                        "published": it.get("pubDate") or it.get("postdate", ""),
                        "src": f"네이버{'블로그' if ep == 'blog' else '뉴스'}"})
        return out
    except Exception:  # noqa: BLE001
        log.warning("네이버 검색 실패(%s): %s", kind, query)
        return []


def _reactions(symbol: str, name: str, market: str) -> list[dict]:
    """웹/소셜 반응 수집 — Tavily(US+웹) + 네이버 뉴스·블로그(KR). URL 기준 중복 제거."""
    out = []
    if market == "KR":
        q = f"{name or symbol} 주가 전망"
        out += naver_search(q, "news", 3)
        out += naver_search(f"{name or symbol} 주식", "blog", 2)   # 개미 반응
        out += tavily_search(f"{name or symbol} 주가 전망 이슈 반응", 3)
    else:
        out += tavily_search(f"{name or symbol} stock outlook news reaction", 4)
    seen, dedup = set(), []
    for r in out:
        u = r.get("url") or r.get("title")
        if u and u not in seen:
            seen.add(u)
            dedup.append(r)
    return dedup[:5]


# ────────────────────────────── 캔들 조회 ──────────────────────────────
def _get_candles(symbol: str, market: str) -> list[dict]:
    """시장별 일봉 200봉. 실패 시 [] (KIS=집IP, Toss=간헐 실패 모두 degrade)."""
    try:
        if market == "KR":
            from bot.brokers.kis import KISBroker
            from bot.accounts import kr_data_account
            return KISBroker(account=kr_data_account(), paper=False).get_candles(
                symbol, "1d", 200) or []
        from bot.brokers.toss import TossBroker
        return TossBroker().get_candles(symbol, "1d", 200) or []
    except Exception:  # noqa: BLE001
        log.warning("%s(%s) 캔들 조회 실패", symbol, market)
        return []


def _pct(a: float, b: float) -> float | None:
    return (a / b - 1) * 100 if b else None


def _trend_from_candles(candles: list[dict]) -> dict | None:
    closes = [c["close"] for c in candles if c.get("close", 0) > 0]
    if len(closes) < 30:
        return None
    last = closes[-1]

    def sma(n: int) -> float | None:
        return sum(closes[-n:]) / n if len(closes) >= n else None

    ma20, ma50, ma200 = sma(20), sma(50), sma(200)
    ret_1m = _pct(last, closes[-22]) if len(closes) > 22 else None
    ret_3m = _pct(last, closes[-64]) if len(closes) > 64 else None
    aligned = bool(ma20 and ma50 and ma200 and ma20 > ma50 > ma200)
    return {
        "last": last, "ret_1m": ret_1m, "ret_3m": ret_3m,
        "ma20": ma20, "ma50": ma50, "ma200": ma200,
        "above_ma50": bool(ma50 and last > ma50), "aligned": aligned,
    }


# ────────────────────────────── 리서치 취합 ──────────────────────────────
def research(symbol: str, market: str, light: bool = False) -> dict:
    """추세+예측+감성+웹반응 취합. 30분 redis 캐시. 각 파트 독립 try — never raise.
    light=True면 웹반응 검색(느린 외부호출)을 생략(포트폴리오 자동리서치 등 다건용)."""
    symbol = symbol.strip().upper()
    market = "KR" if market == "KR" else "US"
    name = _display_name(symbol, market)
    ck = f"research:{'L' if light else 'F'}:{market}:{symbol}"
    try:
        c = _r.get(ck)
        if c:
            return json.loads(c)
    except Exception:  # noqa: BLE001
        pass

    result: dict = {"symbol": symbol, "market": market, "name": name,
                    "trend": None, "forecast": None, "sentiment": None,
                    "reactions": []}

    # 감성 (예측 드리프트 입력으로도 씀)
    news_score = 0.0
    try:
        from bot import news
        ns = news.get_sentiment(symbol, market, need_summary=True)
        news_score = ns.score
        result["sentiment"] = {
            "score": ns.score, "polarity": news.polarity(ns.score),
            "summary": ns.summary, "sources": ns.sources,
        }
    except Exception:  # noqa: BLE001
        log.warning("%s 감성 조회 실패", symbol)

    # 웹/소셜 반응 — Tavily(US+웹) + 네이버 뉴스·블로그(KR). 예측 blend 입력으로도 씀 → 먼저 수집
    if not light:
        try:
            result["reactions"] = _reactions(symbol, name, market)
        except Exception:  # noqa: BLE001
            log.warning("%s 웹반응 검색 실패", symbol)

    # 반응 감성(FinBERT) → 뉴스/공시 감성과 결합해 예측 신호로 blend
    reaction_score = 0.0
    try:
        from bot.sentiment import finbert_scores
        rtexts = [f"{r.get('title', '')} {r.get('content', '')}".strip()
                  for r in (result["reactions"] or [])][:6]
        rs = finbert_scores([t for t in rtexts if t]) if rtexts else None
        if rs:
            reaction_score = sum(s for _, s in rs) / len(rs)
    except Exception:  # noqa: BLE001
        log.warning("%s 반응 감성 실패", symbol)
    if news_score and reaction_score:
        blended_sent = 0.6 * news_score + 0.4 * reaction_score
    else:
        blended_sent = news_score or reaction_score
    result["signal"] = {"news": round(news_score, 3),
                        "reaction": round(reaction_score, 3),
                        "blended": round(blended_sent, 3)}

    # 캔들 → 추세 + 예측 (그래프 + 뉴스/공시 + 웹반응 결합 신호로 몬테카를로 틸트)
    candles = _get_candles(symbol, market)
    try:
        result["trend"] = _trend_from_candles(candles)
    except Exception:  # noqa: BLE001
        log.warning("%s 추세 계산 실패", symbol)

    try:
        closes = [c["close"] for c in candles if c.get("close", 0) > 0]
        if len(closes) >= 31:
            from bot.forecast import forecast_symbol
            from bot.main import _technical_tilt
            fc = forecast_symbol(symbol, closes, 21,
                                 technical_tilt=_technical_tilt(closes),
                                 news_sentiment=blended_sent)
            if fc is not None:
                result["forecast"] = {
                    "prob_up": fc.prob_up, "exp_return": fc.exp_return,
                    "band": fc.band, "target_touch": fc.target_touch,
                    "backtest_winrate": fc.backtest_winrate,
                    "backtest_n": fc.backtest_n,
                }
    except Exception:  # noqa: BLE001
        log.warning("%s 예측 실패", symbol)

    try:
        _r.setex(ck, 600, json.dumps(result, default=str))   # 10분(지연 단축)
    except Exception:  # noqa: BLE001
        pass
    return result


# ────────────────────────────── LLM용 텍스트 블록 ──────────────────────────────
def _f(x, suffix="%", nd=1):
    if x is None:
        return "—"
    return f"{x:+.{nd}f}{suffix}"


def research_block(symbol: str, market: str, light: bool = False) -> str:
    """research()를 LLM 프롬프트용 한국어 컴팩트 블록으로 포맷. 과거 vs 미래 명확 분리.
    light=True면 웹반응 없이 추세·예측·뉴스만(포트폴리오 자동리서치용)."""
    d = research(symbol, market, light=light)
    name = d.get("name") or ""
    head = f"[종목 리서치: {d['symbol']}" + (f" ({name})" if name and name != d["symbol"] else "") + "]"
    lines = [head]

    # 추세 (과거)
    t = d.get("trend")
    if t:
        cur = t.get("last")
        ma50 = t.get("ma50")
        pos = "위" if t.get("above_ma50") else "아래"
        aligned = "정배열(MA20>50>200)" if t.get("aligned") else "정배열 아님"
        lines.append(
            f"▷ 추세(과거): 현재가 {cur:,.2f}"
            f" | 1개월 {_f(t.get('ret_1m'))} · 3개월 {_f(t.get('ret_3m'))}"
            f" | MA50 {pos}({ma50:,.2f}) · {aligned}"
            if ma50 else
            f"▷ 추세(과거): 현재가 {cur:,.2f}"
            f" | 1개월 {_f(t.get('ret_1m'))} · 3개월 {_f(t.get('ret_3m'))} · {aligned}"
        )
    else:
        lines.append("▷ 추세(과거): 데이터 없음(캔들 부족/시세 조회 불가)")

    # 예측 (미래·확률)
    f = d.get("forecast")
    if f:
        wr = f.get("backtest_winrate")
        wr_txt = (f"과거 동일신호 적중률 {wr*100:.0f}%(표본 {f.get('backtest_n', 0)}건)"
                  if wr is not None else "과거 적중률 자료부족")
        tt = f.get("target_touch") or {}
        up5 = tt.get("+5%")
        up5_txt = f" · 21일내 +5% 터치 {up5*100:.0f}%" if up5 is not None else ""
        lines.append(
            f"▷ 예측(미래·21일, 몬테카를로 확률·단정 아님): "
            f"상승확률 {f.get('prob_up', 0)*100:.0f}% · 기대수익 {f.get('exp_return', 0):+.1f}%"
            f"{up5_txt} | {wr_txt}"
        )
    else:
        lines.append("▷ 예측(미래): 데이터 없음(시세 부족으로 확률 산출 불가)")

    # 뉴스 감성
    s = d.get("sentiment")
    if s and (s.get("sources") or s.get("summary")):
        summ = (s.get("summary") or "").strip()
        summ = f" · {summ}" if summ else ""
        lines.append(
            f"▷ 뉴스감성: {s.get('polarity', '중립')}"
            f"(점수 {s.get('score', 0):+.2f}, 기사 {s.get('sources', 0)}건){summ}"
        )
    else:
        lines.append("▷ 뉴스감성: 데이터 없음(뉴스 미수집/키 없음)")

    # 웹 반응 (light 모드에선 수집 안 하므로 줄 생략)
    reactions = d.get("reactions") or []
    if reactions:
        lines.append("▷ 웹/소셜 반응(최근):")
        for r in reactions[:4]:
            title = (r.get("title") or "").strip()
            snippet = (r.get("content") or "").strip().replace("\n", " ")
            if len(snippet) > 90:
                snippet = snippet[:90] + "…"
            src = r.get("src")
            tag = f"[{src}] " if src else ""
            lines.append(f"   · {tag}{title}" + (f" — {snippet}" if snippet else ""))
    elif not light:
        lines.append("▷ 웹/소셜 반응: 데이터 없음(검색 키 없음/결과 없음)")

    return "\n".join(lines)


# ────────────────────────────── 자가 테스트 ──────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    print("=== resolve_query_tickers ===")
    samples = [
        "엔비디아 어때?",
        "삼성전자 지금 사도 될까?",
        "NVDA랑 TSLA 비교해줘",
        "요즘 시장 어때?",
        "005930 전망",
        "TIGER 미국S&P500 괜찮아?",
    ]
    for q in samples:
        print(f"  {q!r:34} → {resolve_query_tickers(q)}")

    print(f"\nNAME2SYM 항목수: {len(NAME2SYM)}  |  KR_NAME2CODE 항목수: {len(KR_NAME2CODE)}"
          f"  |  KR 인덱스(확장후): {len(_kr_index())}")

    print("\n=== research_block (네트워크 없어도 무크래시 확인) ===")
    for sym, mkt in [("AAPL", "US"), ("005930", "KR")]:
        try:
            print(research_block(sym, mkt))
        except Exception as e:  # noqa: BLE001
            print(f"  [FAIL] {sym}: {e!r}")
        print("-" * 60)
