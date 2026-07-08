"""챗봇 대화 영구 저장·검색·요약·평가 — Chat 2.0 (Postgres 기반).

기존 Redis(chat:log 100·chat:calls 500) + localStorage(40)는 휘발/제한적이라
날짜별 세션·검색·평가(👍/👎)·중복합치기·요약을 Postgres로 영속화한다.
server.py의 chat() 핸들러가 이 모듈만 호출하도록 캡슐화(핸들러 최소 변경).
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta

from sqlalchemy import or_

from bot.storage.db import SessionLocal
from bot.storage.models import ChatSession, ChatMessage

log = logging.getLogger(__name__)

# 중복질문 재사용 유효기간(시세 변동 고려). 이 안이면 같은 질문에 이전 답변 재활용.
DEDUP_HOURS = 6


# ---------------- 정규화·해시 ----------------
def norm(q: str) -> str:
    """질문 정규화(중복 감지용) — 공백·기호 제거, 소문자화. '엔비디아 어때?'=='엔비디아어때'."""
    s = (q or "").lower()
    s = re.sub(r"[\s]+", "", s)
    s = re.sub(r"[^\w가-힣]", "", s)
    return s


def qhash(q: str) -> str:
    return hashlib.sha256(norm(q).encode()).hexdigest()[:32]


def _title_of(q: str) -> str:
    t = re.sub(r"\s+", " ", (q or "").strip())
    return (t[:38] + "…") if len(t) > 39 else (t or "새 대화")


# ---------------- 세션 ----------------
def new_session(who: str = "me") -> int:
    who = "spouse" if who == "spouse" else "me"
    with SessionLocal() as s:
        row = ChatSession(who=who, title="새 대화")
        s.add(row)
        s.commit()
        return row.id


def _active_session(s, who: str) -> ChatSession:
    """가장 최근 세션(오늘, 미아카이브) 재사용 or 신규. chat()에서 session 없을 때."""
    row = (s.query(ChatSession)
           .filter(ChatSession.who == who, ChatSession.archived.is_(False))
           .order_by(ChatSession.last_at.desc()).first())
    # 마지막 활동이 6시간 넘었으면 새 대화로 분리(자연스러운 '지난 대화' 구분)
    if row and (datetime.now() - row.last_at) < timedelta(hours=6):
        return row
    row = ChatSession(who=who, title="새 대화")
    s.add(row)
    s.flush()
    return row


def list_sessions(who: str = "me", limit: int = 60) -> list[dict]:
    who = "spouse" if who == "spouse" else "me"
    out = []
    with SessionLocal() as s:
        rows = (s.query(ChatSession)
                .filter(ChatSession.who == who, ChatSession.archived.is_(False),
                        ChatSession.msg_count > 0)
                .order_by(ChatSession.last_at.desc()).limit(limit).all())
        for r in rows:
            out.append({"id": r.id, "title": r.title or "새 대화",
                        "summary": r.summary or "",
                        "started_at": str(r.started_at)[:16],
                        "last_at": str(r.last_at)[:16],
                        "date": str(r.last_at)[:10], "count": r.msg_count})
    return out


def get_messages(session_id: int) -> list[dict]:
    out = []
    with SessionLocal() as s:
        rows = (s.query(ChatMessage)
                .filter(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.id.asc()).all())
        for r in rows:
            out.append({"id": r.id, "role": r.role, "content": r.content,
                        "cached": bool(r.cached), "rating": r.rating or 0,
                        "verdict": r.verdict or "", "ts": str(r.ts)[:16]})
    return out


# ---------------- 메시지 저장 ----------------
def append_turn(who: str, question: str, answer: str, *, session_id: int | None = None,
                cached: bool = False) -> dict:
    """한 턴(user 질문 + bot 답변)을 세션에 추가. 세션 없으면 자동 선택/생성.
    반환 {session_id, user_id, bot_id}."""
    who = "spouse" if who == "spouse" else "me"
    with SessionLocal() as s:
        sess = None
        if session_id:
            sess = s.get(ChatSession, session_id)
        if sess is None:
            sess = _active_session(s, who)
        um = ChatMessage(session_id=sess.id, who=who, role="user",
                         content=(question or "")[:4000], qhash=qhash(question))
        s.add(um)
        bm = ChatMessage(session_id=sess.id, who=who, role="bot",
                         content=(answer or "")[:8000], cached=cached)
        s.add(bm)
        s.flush()
        if not sess.title or sess.title == "새 대화":
            sess.title = _title_of(question)
        sess.msg_count = (sess.msg_count or 0) + 2
        sess.last_at = datetime.now()
        s.commit()
        return {"session_id": sess.id, "user_id": um.id, "bot_id": bm.id}


# ---------------- 중복 질문 합치기 ----------------
def find_recent_answer(who: str, question: str) -> dict | None:
    """같은(정규화) 질문에 DEDUP_HOURS 안에 이미 한 답변이 있으면 재사용용으로 반환.
    반환 {answer, session_id, ts} 또는 None."""
    who = "spouse" if who == "spouse" else "me"
    h = qhash(question)
    if not norm(question):
        return None
    cutoff = datetime.now() - timedelta(hours=DEDUP_HOURS)
    with SessionLocal() as s:
        # 같은 질문(user 메시지) 중 최신 → 바로 다음 bot 답변을 찾는다
        um = (s.query(ChatMessage)
              .filter(ChatMessage.who == who, ChatMessage.role == "user",
                      ChatMessage.qhash == h, ChatMessage.ts >= cutoff)
              .order_by(ChatMessage.id.desc()).first())
        if not um:
            return None
        bm = (s.query(ChatMessage)
              .filter(ChatMessage.session_id == um.session_id,
                      ChatMessage.role == "bot", ChatMessage.id > um.id)
              .order_by(ChatMessage.id.asc()).first())
        if bm and bm.content:
            return {"answer": bm.content, "session_id": um.session_id, "ts": str(bm.ts)[:16]}
    return None


# ---------------- 검색 ----------------
def search(who: str, q: str, limit: int = 40) -> list[dict]:
    """과거 Q&A 텍스트 검색. 매칭 메시지 + 세션제목 스니펫."""
    who = "spouse" if who == "spouse" else "me"
    q = (q or "").strip()
    if len(q) < 2:
        return []
    like = f"%{q}%"
    out = []
    with SessionLocal() as s:
        rows = (s.query(ChatMessage)
                .filter(ChatMessage.who == who, ChatMessage.content.ilike(like))
                .order_by(ChatMessage.id.desc()).limit(limit).all())
        titles = {}
        for r in rows:
            if r.session_id not in titles:
                sess = s.get(ChatSession, r.session_id)
                titles[r.session_id] = sess.title if sess else ""
            txt = r.content or ""
            i = txt.lower().find(q.lower())
            snip = txt[max(0, i - 20): i + 60].strip() if i >= 0 else txt[:80]
            out.append({"session_id": r.session_id, "title": titles[r.session_id],
                        "role": r.role, "snippet": snip, "ts": str(r.ts)[:16]})
    return out


# ---------------- 평가(👍/👎) ----------------
def rate(message_id: int, rating: int) -> bool:
    rating = 1 if rating > 0 else (-1 if rating < 0 else 0)
    with SessionLocal() as s:
        m = s.get(ChatMessage, message_id)
        if not m or m.role != "bot":
            return False
        m.rating = rating
        s.commit()
    return True


def rating_stats(who: str = "me") -> dict:
    who = "spouse" if who == "spouse" else "me"
    with SessionLocal() as s:
        up = s.query(ChatMessage).filter(ChatMessage.who == who, ChatMessage.rating == 1).count()
        down = s.query(ChatMessage).filter(ChatMessage.who == who, ChatMessage.rating == -1).count()
    return {"up": up, "down": down}


# ---------------- 요약 ----------------
def summarize(session_id: int) -> str:
    """세션 전체를 LLM으로 3줄 이내 요약해 session.summary에 저장·반환."""
    msgs = get_messages(session_id)
    if not msgs:
        return ""
    convo = "\n".join(f"{'Q' if m['role'] == 'user' else 'A'}: {m['content'][:500]}"
                      for m in msgs[:20])
    prompt = ("아래 투자상담 챗봇 대화를 한국어 2~3줄로 요약해줘(무엇을 물었고 핵심 답이 뭐였는지). "
              "불릿 없이 자연스러운 문장으로.\n\n" + convo + "\n\n요약:")
    try:
        from bot import chateval
        res = chateval.llm_call(prompt, max_tokens=180)
        summ = (res.get("text") or "").strip()
    except Exception as e:  # noqa: BLE001
        log.warning("세션 요약 실패: %s", e)
        summ = ""
    if summ:
        with SessionLocal() as s:
            sess = s.get(ChatSession, session_id)
            if sess:
                sess.summary = summ[:1000]
                s.commit()
    return summ
