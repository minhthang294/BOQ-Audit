import logging
import threading

from fastapi import APIRouter, Cookie, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.deps import current_user, session_user
from app.api.jobs import owned_job
from app.core.config import get_settings
from app.core.database import get_db
from app.core.rate_limit import PersistentRateLimiter
from app.models.entities import ChatConversation, ChatMessage, User
from app.schemas.api import ChatRequest, ChatResponse
from app.services.project_chat import context_version, gateway_reply

router = APIRouter(prefix="/jobs", tags=["chat"])
logger = logging.getLogger("boq-audit.chat")
settings = get_settings()
limiter = PersistentRateLimiter("chat", settings.chat_rate_limit, settings.chat_rate_window_seconds)
# ponytail: one active chat turn globally on the single-instance MVP; per-conversation locks if throughput matters.
turn_lock = threading.Lock()


def snapshot(db: Session, job) -> dict:
    messages = list(db.scalars(select(ChatMessage).where(ChatMessage.job_id == job.id, ChatMessage.context_version == context_version(job)).order_by(ChatMessage.id.desc()).limit(40)).all())
    messages.reverse()
    return {"enabled": bool(settings.chat_gateway_token), "messages": messages, "busy": any(m.status == "PENDING" for m in messages)}


@router.get("/{job_code}/chat", response_model=ChatResponse)
async def get_chat(job_code: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return snapshot(db, owned_job(db, job_code, user))


@router.post("/{job_code}/chat/messages", response_model=ChatResponse)
async def send_message(job_code: str, payload: ChatRequest, session: str | None = Cookie(default=None), user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = owned_job(db, job_code, user)
    if not settings.chat_gateway_token:
        raise HTTPException(503, "SBTech AI chưa được cấu hình. Vui lòng thử lại sau.")
    db.rollback()
    if not limiter.consume(db, str(user.id)):
        raise HTTPException(429, "Bạn đã dùng hết lượt chat trong khoảng thời gian này.", headers={"Retry-After": str(limiter.window_seconds)})
    if not turn_lock.acquire(blocking=False):
        raise HTTPException(409, "AI đang trả lời một câu hỏi. Vui lòng thử lại sau.")
    pending_id = None
    try:
        job = owned_job(db, job_code, user)
        version = context_version(job)
        conversation = db.get(ChatConversation, job.id)
        if conversation is None:
            conversation = ChatConversation(job_id=job.id, user_id=user.id)
            db.add(conversation)
        if conversation.user_id != user.id:
            raise HTTPException(404, "Không tìm thấy hội thoại")
        if conversation.context_version != version:
            conversation.thread_id = None
            conversation.context_version = version
        messages = snapshot(db, job)["messages"]
        if any(m.status == "PENDING" for m in messages):
            raise HTTPException(409, "Câu hỏi trước vẫn đang được xử lý.")
        history = []
        remaining = 12000
        for m in reversed(messages[-20:]):
            if m.status == "COMPLETED" and remaining > 0:
                content = m.content[:remaining]
                history.insert(0, {"role": m.role, "content": content})
                remaining -= len(content)
        thread_id = conversation.thread_id
        if len(messages) >= 40:
            # Bound provider history too; a new thread receives the last bounded transcript.
            thread_id = None
        pending = ChatMessage(job_id=job.id, role="user", content=payload.message, status="PENDING", context_version=version)
        db.add(pending)
        db.commit()
        pending_id = pending.id
        # No open write transaction while waiting for the isolated gateway.
        db.refresh(job)
        reply = await run_in_threadpool(gateway_reply, job, thread_id, version, history, payload.message)
        db.expire_all()
        session_user(session, db)
        job = owned_job(db, job_code, user)
        conversation = db.get(ChatConversation, job.id)
        pending = db.get(ChatMessage, pending_id)
        if context_version(job) != version:
            pending.status = "FAILED"
            db.commit()
            raise HTTPException(409, "Hồ sơ vừa thay đổi. Vui lòng gửi lại câu hỏi.")
        pending.status = "COMPLETED"
        conversation.thread_id = reply["thread_id"]
        db.add(ChatMessage(job_id=job.id, role="assistant", content=reply["text"], status="COMPLETED", context_version=version))
        db.commit()
        return snapshot(db, job)
    except Exception as exc:
        db.rollback()
        if pending_id:
            pending = db.get(ChatMessage, pending_id)
            if pending:
                pending.status = "FAILED"
                conversation = db.get(ChatConversation, pending.job_id)
                if conversation:
                    conversation.thread_id = None
                db.commit()
        if isinstance(exc, HTTPException):
            raise
        # Provider errors can include paths, account information, or tokens.
        logger.warning("chat_reply_failed kind=%s", type(exc).__name__)
        raise HTTPException(503, "SBTech AI chưa thể trả lời. Câu hỏi đã được lưu; vui lòng thử lại sau.") from None
    finally:
        turn_lock.release()
