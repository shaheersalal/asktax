from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.services.rag import query_rag, query_rag_stream
from app.core.limiter import is_ip_allowed, increment_ip_query_count
from app.core.security import decode_token
from app.models.query import QueryHistory
from app.models.user import User
from app.models.firm import Firm, FirmStatus
from app.db.session import SessionLocal
from jose import JWTError
import json
import logging

logger = logging.getLogger(__name__)
router = APIRouter()


class QueryRequest(BaseModel):
    query: str
    token: str = None
    session_id: str = None


class QueryResponse(BaseModel):
    query: str
    detected_language: str
    response_en: str
    response_ur: str
    sources: list


def get_client_ip(request: Request) -> str:
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip.strip()
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host


@router.post("/query", response_model=QueryResponse)
async def ask_question(request: Request, body: QueryRequest):
    if not body.query or len(body.query.strip()) < 3:
        raise HTTPException(status_code=400, detail="Query too short")
    if len(body.query) > 1000:
        raise HTTPException(status_code=400, detail="Query too long")

    client_ip = get_client_ip(request)
    user_id = None
    user = None

    # ── Authenticate if token provided ───────────────────────────────────────
    if body.token:
        try:
            payload = decode_token(body.token)
            user_id = int(payload.get("sub"))
            db: Session = SessionLocal()
            try:
                user = db.query(User).filter(User.id == user_id).first()
                if not user:
                    user_id = None
                elif user.status != "active":
                    raise HTTPException(
                        status_code=403,
                        detail=f"Your account is {user.status.replace('_', ' ')}."
                    )
                elif user.firm_id:
                    # Check firm status
                    firm = db.query(Firm).filter(Firm.id == user.firm_id).first()
                    if firm and firm.status == FirmStatus.suspended:
                        raise HTTPException(status_code=403, detail="Your firm account has been suspended.")
                    # Check query limit
                    if user.queries_remaining <= 0:
                        raise HTTPException(
                            status_code=429,
                            detail={
                                "message": "Monthly query limit reached. Contact your firm admin.",
                                "queries_remaining": 0,
                                "limit_reached": True,
                            }
                        )
                else:
                    # Individual user — auto-expire trial if past expiry date
                    from datetime import datetime, timezone as _tz
                    from app.models.user import SubscriptionTier as _ST
                    if user.trial_expires_at and user.trial_expires_at < datetime.now(_tz.utc):
                        user.subscription_tier = _ST.free
                        user.trial_expires_at = None
                        db.commit()
                        db.refresh(user)
                    # Check limit
                    if user.queries_remaining <= 0 and user.effective_query_limit < 999999:
                        raise HTTPException(
                            status_code=429,
                            detail={
                                "message": "Monthly query limit reached. Please upgrade your plan.",
                                "queries_remaining": 0,
                                "signup_required": False,
                                "upgrade_required": True,
                            }
                        )
            finally:
                db.close()
        except (JWTError, ValueError):
            user_id = None
            user = None

    # ── IP rate limit for guests ──────────────────────────────────────────────
    if not user_id:
        if not is_ip_allowed(client_ip):
            raise HTTPException(
                status_code=429,
                detail={
                    "message": "You have used your 20 free queries. Sign up to continue.",
                    "queries_remaining": 0,
                    "signup_required": True,
                }
            )
        increment_ip_query_count(client_ip)

    # ── Run RAG ───────────────────────────────────────────────────────────────
    result = query_rag(body.query)

    # ── Save history + increment counter ─────────────────────────────────────
    if user_id:
        db: Session = SessionLocal()
        try:
            history = QueryHistory(
                user_id=user_id,
                query_text=body.query,
                query_language=result["detected_language"],
                response_en=result["response_en"],
                response_ur=result["response_ur"],
                sources_used=str([s["title"] for s in result["sources"]]),
                tokens_used=result.get("tokens_used", 0),
                confidence_score=result.get("confidence_score"),
                client_ip=client_ip,
            )
            db.add(history)

            # Increment user query counter
            user_obj = db.query(User).filter(User.id == user_id).first()
            if user_obj:
                user_obj.queries_used_this_month += 1

            db.commit()
        except Exception as e:
            logger.warning(f"Failed to save query history: {e}")
            db.rollback()
        finally:
            db.close()

    return result


# ── Streaming endpoint (SSE) ──────────────────────────────────────────────────
@router.post("/query/stream")
async def ask_question_stream(request: Request, body: QueryRequest):
    if not body.query or len(body.query.strip()) < 3:
        raise HTTPException(status_code=400, detail="Query too short")
    if len(body.query) > 1000:
        raise HTTPException(status_code=400, detail="Query too long")

    client_ip = get_client_ip(request)
    user_id = None

    if body.token:
        try:
            payload = decode_token(body.token)
            user_id = int(payload.get("sub"))
            db: Session = SessionLocal()
            try:
                user = db.query(User).filter(User.id == user_id).first()
                if not user or user.status != "active":
                    user_id = None
                elif user.firm_id:
                    firm = db.query(Firm).filter(Firm.id == user.firm_id).first()
                    if firm and firm.status == FirmStatus.suspended:
                        raise HTTPException(status_code=403, detail="Firm account suspended.")
                    if user.queries_remaining <= 0:
                        raise HTTPException(status_code=429, detail={"message": "Monthly query limit reached.", "limit_reached": True})
                else:
                    from datetime import datetime, timezone as _tz
                    from app.models.user import SubscriptionTier as _ST
                    if user.trial_expires_at and user.trial_expires_at < datetime.now(_tz.utc):
                        user.subscription_tier = _ST.free
                        user.trial_expires_at = None
                        db.commit()
                        db.refresh(user)
                    if user.queries_remaining <= 0 and user.effective_query_limit < 999999:
                        raise HTTPException(status_code=429, detail={"message": "Monthly query limit reached. Please upgrade.", "upgrade_required": True})
            finally:
                db.close()
        except (JWTError, ValueError):
            user_id = None

    if not user_id:
        if not is_ip_allowed(client_ip):
            raise HTTPException(status_code=429, detail={"message": "Free queries used up. Sign up to continue.", "signup_required": True})
        increment_ip_query_count(client_ip)

    def generate():
        final_data = {}
        for event_str in query_rag_stream(body.query):
            yield event_str
            if '"type": "done"' in event_str or '"type":"done"' in event_str:
                try:
                    final_data = json.loads(event_str.replace("data: ", "").strip())
                except Exception:
                    pass

        if user_id and final_data:
            db: Session = SessionLocal()
            try:
                history = QueryHistory(
                    user_id=user_id,
                    query_text=body.query,
                    query_language=final_data.get("lang", "en"),
                    response_en=final_data.get("response_en", ""),
                    response_ur=final_data.get("response_ur", ""),
                    sources_used=str([s["title"] for s in final_data.get("sources", [])]),
                    tokens_used=final_data.get("tokens_used", 0),
                    confidence_score=final_data.get("confidence_score"),
                    client_ip=client_ip,
                    session_id=body.session_id,
                )
                db.add(history)
                user_obj = db.query(User).filter(User.id == user_id).first()
                if user_obj:
                    user_obj.queries_used_this_month += 1
                db.commit()
                db.refresh(history)
                yield f"data: {json.dumps({'type': 'saved', 'chat_id': history.id, 'session_id': body.session_id})}\n\n"
                # Flip collab turn if this is a collab session
                if body.session_id:
                    from sqlalchemy import text as sqlt
                    collab = db.execute(sqlt("""
                        SELECT id, host_user_id, guest_user_id FROM collab_sessions
                        WHERE session_id = :sid AND status = 'active'
                        AND (host_user_id = :uid OR guest_user_id = :uid)
                    """), {"sid": body.session_id, "uid": user_id}).fetchone()
                    if collab:
                        other = collab[2] if collab[1] == user_id else collab[1]
                        db.execute(sqlt("UPDATE collab_sessions SET turn_user_id = :other WHERE id = :id"), {"other": other, "id": collab[0]})
                        db.commit()
            except Exception as e:
                logger.warning(f"Failed to save streaming history: {e}")
                db.rollback()
            finally:
                db.close()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )