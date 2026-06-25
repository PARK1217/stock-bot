"""ETF 룩스루 데모: 구성종목 뉴스를 비중가중해 ETF 감성 산출.
사용: python scripts/run_etf_lookthrough.py JEPQ"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from bot.config import settings          # noqa: E402  (.env)
from bot.etf import lookthrough, etf_news_sentiment  # noqa: E402

ETF = sys.argv[1] if len(sys.argv) > 1 else "JEPQ"

info = lookthrough(ETF)
print(f"== {ETF} 룩스루 ==")
if not info:
    print("등록된 룩스루 없음")
    sys.exit()
print(f"기초자산: {info['underlying']}  (type={info['type']})")
if info["type"] != "equity":
    print("→ 주식 구성종목 없음(금리/원자재). 뉴스 룩스루 미적용, 거시 지표로 봐야 함.")
    sys.exit()

res = etf_news_sentiment(ETF, n=6)
score, conf, detail = res
print(f"\n구성종목별 뉴스 감성(상위 6):")
for sym, w, sc, cf, n in detail:
    print(f"  {sym:<6} 비중 {w*100:4.1f}%  감성 {sc:+.2f}  신뢰 {cf:.2f}  기사 {n}건")
print(f"\n→ {ETF} 룩스루 뉴스 감성: {score:+.3f}  (가중신뢰 {conf:.2f})")
print("  ※ ETF 추이 자체는 가격(스크리너)으로, 뉴스는 구성종목 룩스루로 본다.")
