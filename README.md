# stock-bot

배당 안정형 자동매매 봇. 증권사 어댑터 추상화로 **한국투자증권(KIS)** 먼저,
**토스증권**은 정식 API 오픈 후 어댑터만 추가하는 구조.

> ⚠️ 어떤 봇도 수익을 보장하지 않습니다. 반드시 `paper`(모의투자)로 충분히
> 검증한 뒤에만 `live`로 전환하세요. 기본값은 `paper` 입니다.

## 구조

```
app/bot/
  config.py            환경설정(.env)
  brokers/
    base.py            BrokerAdapter 인터페이스 (Position/Balance/OrderResult)
    kis.py             한국투자증권 구현 (토큰캐시·잔고·시세·주문)
  strategies/
    base.py            Strategy 인터페이스 (Signal)
    dividend_core.py   배당 코어 + 밴드 리밸런싱
  risk/rules.py        비중·일일한도·손절 게이트
  notify/telegram.py   알림
  storage/             Postgres 모델·세션
  main.py              balance / rebalance / snapshot / run
```

데이터 흐름: `잔고 조회 → 전략(신호 생성) → 리스크(검증·차단) → 주문 → DB기록·알림`

## 시작하기

```bash
cp .env.example .env        # KIS 앱키·계좌번호 입력 (먼저 모의투자 키!)
docker compose up -d db redis

# 1) 연결 점검
docker compose run --rm bot python -m bot.main balance

# 2) 주문 없이 신호만 확인 (안전)
docker compose run --rm bot python -m bot.main rebalance --dry

# 3) 모의계좌 실제 주문 1회
docker compose run --rm bot python -m bot.main rebalance

# 4) 스케줄러 상주 (장중 자동 운용)
docker compose up -d bot
```

## 다음 단계 (TODO)

- [ ] KIS 엔드포인트/TR_ID를 공식 포털 문서로 최종 검증
- [ ] `dividend_core.py` 의 예시 바스켓을 실제 배당 종목/비중으로 교체
- [ ] 배당기준일 캘린더 연동(배당락 관리)
- [ ] 백테스트 모듈(backtrader/vectorbt) + 과거 데이터
- [ ] 대시보드(수익률·배당 캘린더·포지션)
- [ ] 토스증권 어댑터(`brokers/toss.py`) — 정식 오픈 후
```
