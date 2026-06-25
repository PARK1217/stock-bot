FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Seoul

WORKDIR /app

COPY requirements.txt .
# FinBERT는 HF Inference API로 오프로딩 → torch/transformers 불필요(ARM 경량 이미지)
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "-m", "bot.main", "run"]
