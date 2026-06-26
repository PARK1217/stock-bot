from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    trading_mode: str = "paper"  # paper | live
    broker: str = "kis"          # kis | toss

    # KIS — 모의투자(paper)
    kis_paper_app_key: str = ""
    kis_paper_app_secret: str = ""
    kis_paper_account_no: str = ""
    kis_paper_account_prod: str = "01"
    # KIS — 실전투자(live)
    kis_live_app_key: str = ""
    kis_live_app_secret: str = ""
    kis_live_account_no: str = ""
    kis_live_account_prod: str = "01"
    kis_live_accounts: str = ""   # 통합조회용 "CANO-PRDT,CANO-PRDT" 콤마목록

    # Toss
    toss_app_key: str = ""       # CLIENT_ID
    toss_app_secret: str = ""    # CLIENT_SECRET
    toss_account_no: str = ""    # X-Tossinvest-Account 헤더용 계좌번호
    toss_allow_live: bool = False  # 토스 실거래 명시 동의 (모의 환경 없음)
    # 남편(배우자) 토스 계좌 — 대시보드 '남편' 사용자탭용. 본인 명의 별도 앱키 필요.
    toss_spouse_app_key: str = ""
    toss_spouse_app_secret: str = ""
    toss_spouse_account_no: str = ""

    # infra
    database_url: str = "postgresql+psycopg://stockbot:changeme@db:5432/stockbot"
    redis_url: str = "redis://redis:6379/0"

    # notify
    discord_webhook_url: str = ""   # 디스코드 알림(웹훅 URL 하나면 끝)
    telegram_bot_token: str = ""    # (폴백)
    telegram_chat_id: str = ""

    # news / sentiment
    finnhub_api_key: str = ""     # 미국 종목 뉴스 (finnhub.io 무료)
    dart_api_key: str = ""        # 한국 전자공시 DART
    tiingo_api_key: str = ""      # 백테스트용 장기 US 일봉(tiingo.com 무료, 분배조정=총수익)
    news_lookback_days: int = 7
    # 감성: FinBERT(점수) + Groq(관련성·요약) 하이브리드. 없으면 키워드 폴백.
    use_finbert: bool = True              # 로컬 FinBERT 사용(키 없을 때)
    huggingface_api_key: str = ""         # FinBERT를 HF Inference API로 오프로딩(ARM용)
    hf_finbert_model: str = "ProsusAI/finbert"
    groq_api_key: str = ""                # Groq 무료 API (관련성 필터·요약)
    groq_model: str = "llama-3.3-70b-versatile"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    mistral_api_key: str = ""             # 폴백 LLM(Groq 장애 대비)
    mistral_model: str = "mistral-small-latest"
    mistral_base_url: str = "https://api.mistral.ai/v1"
    anthropic_api_key: str = ""           # (선택) Anthropic 대체
    sentiment_model: str = "claude-haiku-4-5-20251001"
    dashboard_password: str = ""          # 대시보드 외부접속 비번(빈값=인증off, 집망내용)

    # risk
    max_position_pct: float = 20
    daily_order_limit: int = 10
    stop_loss_pct: float = 15

    @property
    def is_paper(self) -> bool:
        return self.trading_mode.lower() != "live"

    # TRADING_MODE 에 따라 모의/실전 KIS 키를 자동 선택 (어댑터는 이 속성만 본다)
    @property
    def kis_app_key(self) -> str:
        return self.kis_paper_app_key if self.is_paper else self.kis_live_app_key

    @property
    def kis_app_secret(self) -> str:
        return self.kis_paper_app_secret if self.is_paper else self.kis_live_app_secret

    @property
    def kis_account_no(self) -> str:
        return self.kis_paper_account_no if self.is_paper else self.kis_live_account_no

    @property
    def kis_account_prod(self) -> str:
        return self.kis_paper_account_prod if self.is_paper else self.kis_live_account_prod

    @property
    def kis_accounts(self) -> list[tuple[str, str]]:
        """통합조회 대상 계좌 [(CANO, PRDT), ...]. 모의=단일, 실전=목록(없으면 기본 단일)."""
        if self.is_paper:
            return [(self.kis_paper_account_no, self.kis_paper_account_prod)]
        out = []
        for tok in self.kis_live_accounts.split(","):
            tok = tok.strip()
            if "-" in tok:
                cano, prod = tok.split("-", 1)
                out.append((cano.strip(), prod.strip()))
        return out or [(self.kis_live_account_no, self.kis_live_account_prod)]


settings = Settings()
