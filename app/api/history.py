import json
import secrets
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.query import QueryHistory
from app.models.user import User, UserRole, SubscriptionTier
from app.models.firm import Firm
from app.core.security import decode_token
from jose import JWTError
from datetime import datetime, timezone

router = APIRouter()


def get_current_user_id(token: str, db: Session) -> int:
    try:
        payload = decode_token(token)
        return int(payload.get("sub"))
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")


def _get_current_user(token: str, db: Session) -> User:
    try:
        payload = decode_token(token)
        user_id = int(payload.get("sub"))
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _is_pro_user(user: User) -> bool:
    if user.firm_id and user.firm:
        return user.firm.plan.value == "pro"
    return user.subscription_tier == SubscriptionTier.professional


def _fmt(q: QueryHistory, include_deleted_at: bool = False) -> dict:
    d = {
        "id": q.id,
        "title": q.title or None,
        "query": q.query_text,
        "response_en": q.response_en,
        "response_ur": q.response_ur,
        "confidence_score": q.confidence_score,
        "share_token": q.share_token,
        "session_id": q.session_id,
        "created_at": q.created_at.isoformat(),
    }
    if include_deleted_at and q.deleted_at:
        d["deleted_at"] = q.deleted_at.isoformat()
    return d


def _fmt_with_user(q: QueryHistory, user: User) -> dict:
    return {
        "id": q.id,
        "title": q.title or None,
        "query": q.query_text,
        "response_en": q.response_en,
        "response_ur": q.response_ur,
        "confidence_score": q.confidence_score,
        "share_token": q.share_token,
        "created_at": q.created_at.isoformat(),
        "user_id": user.id,
        "user_email": user.email,
        "user_name": user.full_name or user.email.split("@")[0],
    }


@router.get("/history")
def get_history(token: str, db: Session = Depends(get_db)):
    user_id = get_current_user_id(token, db)
    queries = (
        db.query(QueryHistory)
        .filter(QueryHistory.user_id == user_id, QueryHistory.deleted_at.is_(None))
        .order_by(QueryHistory.created_at.desc())
        .all()
    )
    return JSONResponse(
        content=[_fmt(q) for q in queries],
        headers={"Cache-Control": "no-store"},
    )


@router.get("/history/deleted")
def get_deleted_history(token: str, db: Session = Depends(get_db)):
    """Return user's soft-deleted chats so they can request restoration."""
    user_id = get_current_user_id(token, db)
    queries = (
        db.query(QueryHistory)
        .filter(QueryHistory.user_id == user_id, QueryHistory.deleted_at.isnot(None))
        .order_by(QueryHistory.deleted_at.desc())
        .limit(50)
        .all()
    )
    return JSONResponse(
        content=[_fmt(q, include_deleted_at=True) for q in queries],
        headers={"Cache-Control": "no-store"},
    )


@router.get("/history/firm")
def get_firm_history(token: str, db: Session = Depends(get_db)):
    """Get chat history for all members of the firm. Only firm admins/owners can access."""
    user = _get_current_user(token, db)
    if not user.firm_id or not user.is_firm_admin:
        raise HTTPException(status_code=403, detail="Firm admin access required")

    members = db.query(User).filter(User.firm_id == user.firm_id).all()
    member_ids = [m.id for m in members]
    member_map = {m.id: m for m in members}

    queries = (
        db.query(QueryHistory)
        .filter(QueryHistory.user_id.in_(member_ids), QueryHistory.deleted_at.is_(None))
        .order_by(QueryHistory.created_at.desc())
        .limit(200)
        .all()
    )

    return JSONResponse(
        content=[_fmt_with_user(q, member_map[q.user_id]) for q in queries if q.user_id in member_map],
        headers={"Cache-Control": "no-store"},
    )


# ── Session-based history (must come BEFORE wildcard /{chat_id} routes) ──────

@router.get("/history/sessions")
def get_sessions(token: str, db: Session = Depends(get_db)):
    """Return sessions (groups of chats) ordered by most recent, with legacy solo messages as sessions."""
    user_id = get_current_user_id(token, db)
    from sqlalchemy import text as sqlt

    rows = db.execute(sqlt("""
        WITH stats AS (
            SELECT session_id, COUNT(*) AS msg_count, MAX(created_at) AS last_at
            FROM query_history
            WHERE user_id = :uid AND deleted_at IS NULL AND session_id IS NOT NULL
            GROUP BY session_id
        ),
        first_msg AS (
            SELECT DISTINCT ON (session_id) session_id, query_text
            FROM query_history
            WHERE user_id = :uid AND deleted_at IS NULL AND session_id IS NOT NULL
            ORDER BY session_id, created_at ASC
        )
        SELECT s.session_id, f.query_text, s.msg_count, s.last_at
        FROM stats s JOIN first_msg f USING (session_id)
        ORDER BY s.last_at DESC
        LIMIT 100
    """), {"uid": user_id}).fetchall()

    legacy = db.execute(sqlt("""
        SELECT id::text, query_text, 1, created_at
        FROM query_history
        WHERE user_id = :uid AND deleted_at IS NULL AND session_id IS NULL
        ORDER BY created_at DESC LIMIT 30
    """), {"uid": user_id}).fetchall()

    sessions = []
    for r in rows:
        t = r[1] or ""
        sessions.append({"session_id": r[0], "title": t[:65] + ("…" if len(t) > 65 else ""), "message_count": r[2], "last_at": r[3].isoformat(), "is_legacy": False})
    for r in legacy:
        t = r[1] or ""
        sessions.append({"session_id": r[0], "title": t[:65] + ("…" if len(t) > 65 else ""), "message_count": r[2], "last_at": r[3].isoformat(), "is_legacy": True})

    sessions.sort(key=lambda x: x["last_at"], reverse=True)
    return JSONResponse(content=sessions[:120], headers={"Cache-Control": "no-store"})


@router.get("/history/sessions/{session_id}")
def get_session_messages(session_id: str, token: str, db: Session = Depends(get_db)):
    """Return all messages in a session, ordered oldest-first."""
    user_id = get_current_user_id(token, db)
    queries = (
        db.query(QueryHistory)
        .filter(QueryHistory.session_id == session_id, QueryHistory.user_id == user_id, QueryHistory.deleted_at.is_(None))
        .order_by(QueryHistory.created_at.asc())
        .all()
    )
    if not queries:
        try:
            chat_id = int(session_id)
            q = db.query(QueryHistory).filter(QueryHistory.id == chat_id, QueryHistory.user_id == user_id, QueryHistory.deleted_at.is_(None)).first()
            if q:
                return JSONResponse(content=[_fmt(q)], headers={"Cache-Control": "no-store"})
        except ValueError:
            pass
        raise HTTPException(status_code=404, detail="Session not found")
    return JSONResponse(content=[_fmt(q) for q in queries], headers={"Cache-Control": "no-store"})


@router.delete("/history/sessions/{session_id}")
def delete_session(session_id: str, token: str, db: Session = Depends(get_db)):
    """Soft-delete all messages in a session."""
    user_id = get_current_user_id(token, db)
    from sqlalchemy import text as sqlt
    try:
        chat_id = int(session_id)
        db.execute(sqlt("UPDATE query_history SET deleted_at = NOW() WHERE id = :id AND user_id = :uid AND deleted_at IS NULL"), {"id": chat_id, "uid": user_id})
    except ValueError:
        db.execute(sqlt("UPDATE query_history SET deleted_at = NOW() WHERE session_id = :sid AND user_id = :uid AND deleted_at IS NULL"), {"sid": session_id, "uid": user_id})
    db.commit()
    return {"deleted": True}


@router.get("/history/{chat_id}")
def get_single(chat_id: int, token: str, db: Session = Depends(get_db)):
    user_id = get_current_user_id(token, db)
    q = db.query(QueryHistory).filter(
        QueryHistory.id == chat_id,
        QueryHistory.user_id == user_id,
    ).first()
    if not q:
        raise HTTPException(status_code=404, detail="Chat not found")
    return JSONResponse(content=_fmt(q), headers={"Cache-Control": "no-store"})


@router.patch("/history/{chat_id}/title")
def rename_chat(chat_id: int, body: dict, token: str, db: Session = Depends(get_db)):
    user_id = get_current_user_id(token, db)
    q = db.query(QueryHistory).filter(
        QueryHistory.id == chat_id,
        QueryHistory.user_id == user_id,
    ).first()
    if not q:
        raise HTTPException(status_code=404, detail="Chat not found")
    new_title = (body.get("title") or "").strip()[:200]
    q.title = new_title or None
    db.commit()
    return {"id": chat_id, "title": q.title}


@router.delete("/history/{chat_id}")
def soft_delete_chat(chat_id: int, token: str, db: Session = Depends(get_db)):
    """Soft-delete a chat: hides it from user but keeps it in DB for recovery."""
    user_id = get_current_user_id(token, db)
    q = db.query(QueryHistory).filter(
        QueryHistory.id == chat_id,
        QueryHistory.user_id == user_id,
        QueryHistory.deleted_at.is_(None),
    ).first()
    if not q:
        raise HTTPException(status_code=404, detail="Chat not found")
    q.deleted_at = datetime.now(timezone.utc)
    db.commit()
    return {"id": chat_id, "deleted": True}


@router.post("/history/{chat_id}/request-restore")
def request_restore(chat_id: int, body: dict, token: str, db: Session = Depends(get_db)):
    """Submit a restore request to admin. User provides context of the chat."""
    user = _get_current_user(token, db)
    q = db.query(QueryHistory).filter(
        QueryHistory.id == chat_id,
        QueryHistory.user_id == user.id,
        QueryHistory.deleted_at.isnot(None),
    ).first()
    if not q:
        raise HTTPException(status_code=404, detail="Deleted chat not found")

    context = (body.get("context") or "").strip()[:1000]
    from sqlalchemy import text as sqlt
    db.execute(sqlt("""
        INSERT INTO restore_requests (user_id, user_name, user_email, chat_id, chat_title, context)
        VALUES (:uid, :name, :email, :cid, :title, :ctx)
        ON CONFLICT DO NOTHING
    """), {
        "uid": user.id,
        "name": user.full_name or user.email.split("@")[0],
        "email": user.email,
        "cid": q.id,
        "title": q.title or q.query_text[:80],
        "ctx": context,
    })
    db.commit()
    return {"requested": True, "message": "Restore request submitted. Admin will review within 1 business day."}


@router.post("/history/{chat_id}/share")
def generate_share_token(chat_id: int, token: str, db: Session = Depends(get_db)):
    """Generate a public share link for a chat. Pro users only."""
    user = _get_current_user(token, db)
    if not _is_pro_user(user):
        raise HTTPException(status_code=403, detail="Chat sharing is a Pro feature. Please upgrade.")

    q = db.query(QueryHistory).filter(
        QueryHistory.id == chat_id,
        QueryHistory.user_id == user.id,
        QueryHistory.deleted_at.is_(None),
    ).first()
    if not q:
        raise HTTPException(status_code=404, detail="Chat not found")

    if not q.share_token:
        q.share_token = secrets.token_urlsafe(32)
        db.commit()

    return {"share_token": q.share_token, "chat_id": chat_id}


@router.delete("/history/{chat_id}/share")
def revoke_share_token(chat_id: int, token: str, db: Session = Depends(get_db)):
    """Revoke the public share link for a chat."""
    user_id = get_current_user_id(token, db)
    q = db.query(QueryHistory).filter(
        QueryHistory.id == chat_id,
        QueryHistory.user_id == user_id,
    ).first()
    if not q:
        raise HTTPException(status_code=404, detail="Chat not found")
    q.share_token = None
    db.commit()
    return {"chat_id": chat_id, "unshared": True}


@router.get("/shared/{share_token}")
def get_shared_chat(share_token: str, db: Session = Depends(get_db)):
    """Public endpoint — returns a shared chat without requiring login."""
    q = db.query(QueryHistory).filter(
        QueryHistory.share_token == share_token,
        QueryHistory.deleted_at.is_(None),
    ).first()
    if not q:
        raise HTTPException(status_code=404, detail="Shared chat not found or link has been revoked.")

    user = db.query(User).filter(User.id == q.user_id).first()
    return JSONResponse(content={
        "id": q.id,
        "title": q.title or q.query_text[:80],
        "query": q.query_text,
        "response_en": q.response_en,
        "response_ur": q.response_ur,
        "confidence_score": q.confidence_score,
        "created_at": q.created_at.isoformat(),
        "shared_by": user.full_name or user.email.split("@")[0] if user else "AskTax User",
    })


class ShareConversationRequest(BaseModel):
    chat_ids: List[int]


@router.post("/history/share-conversation")
def share_conversation(body: ShareConversationRequest, token: str, db: Session = Depends(get_db)):
    """Create a public share link for a multi-message conversation."""
    user = _get_current_user(token, db)
    chat_ids = list(dict.fromkeys(body.chat_ids))  # deduplicate, preserve order
    if not chat_ids:
        raise HTTPException(status_code=400, detail="No chat IDs provided")

    owned = db.query(QueryHistory).filter(
        QueryHistory.id.in_(chat_ids),
        QueryHistory.user_id == user.id,
        QueryHistory.deleted_at.is_(None),
    ).all()
    owned_ids = {q.id for q in owned}
    if not all(cid in owned_ids for cid in chat_ids):
        raise HTTPException(status_code=403, detail="One or more chats not found")

    share_token = secrets.token_urlsafe(32)
    from sqlalchemy import text as sqlt
    db.execute(sqlt("""
        INSERT INTO shared_conversations (share_token, user_id, chat_ids)
        VALUES (:token, :uid, :cids)
    """), {"token": share_token, "uid": user.id, "cids": json.dumps(chat_ids)})
    db.commit()
    return {"share_token": share_token}


@router.get("/shared/conversation/{share_token}")
def get_shared_conversation(share_token: str, db: Session = Depends(get_db)):
    """Public endpoint — returns all messages in a shared conversation."""
    from sqlalchemy import text as sqlt
    row = db.execute(sqlt(
        "SELECT user_id, chat_ids FROM shared_conversations WHERE share_token = :token"
    ), {"token": share_token}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Shared conversation not found or link has expired.")

    user_id, chat_ids_json = row
    chat_ids = json.loads(chat_ids_json)

    chats = db.query(QueryHistory).filter(
        QueryHistory.id.in_(chat_ids),
        QueryHistory.deleted_at.is_(None),
    ).all()
    chat_map = {q.id: q for q in chats}
    ordered = [chat_map[cid] for cid in chat_ids if cid in chat_map]

    user = db.query(User).filter(User.id == user_id).first()
    shared_by = (user.full_name or user.email.split("@")[0]) if user else "AskTax User"

    return JSONResponse(content={
        "share_token": share_token,
        "shared_by": shared_by,
        "messages": [_fmt(q) for q in ordered],
    })


# ── Admin restore-request endpoints ──────────────────────────────────────────

@router.get("/admin/restore-requests")
def list_restore_requests(admin_token: str, db: Session = Depends(get_db)):
    from app.core.config import get_settings
    from app.core.limiter import get_dynamic_role
    from sqlalchemy import text as sqlt
    s = get_settings()
    if admin_token != s.ADMIN_SECRET_KEY and not get_dynamic_role(admin_token):
        raise HTTPException(status_code=403, detail="Admin access required")

    rows = db.execute(sqlt("""
        SELECT id, user_id, user_name, user_email, chat_id, chat_title, context, status, created_at
        FROM restore_requests
        ORDER BY created_at DESC
        LIMIT 200
    """)).fetchall()
    return JSONResponse(content=[{
        "id": r[0], "user_id": r[1], "user_name": r[2], "user_email": r[3],
        "chat_id": r[4], "chat_title": r[5], "context": r[6],
        "status": r[7], "created_at": r[8].isoformat() if r[8] else None,
    } for r in rows])


@router.post("/admin/restore-requests/{req_id}/approve")
def approve_restore_request(req_id: int, admin_token: str, db: Session = Depends(get_db)):
    from app.core.config import get_settings
    from sqlalchemy import text as sqlt
    s = get_settings()
    if admin_token != s.ADMIN_SECRET_KEY:
        raise HTTPException(status_code=403, detail="Owner access required")

    row = db.execute(sqlt(
        "SELECT chat_id FROM restore_requests WHERE id = :id"
    ), {"id": req_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Request not found")

    chat_id = row[0]
    q = db.query(QueryHistory).filter(QueryHistory.id == chat_id).first()
    if q:
        q.deleted_at = None

    db.execute(sqlt(
        "UPDATE restore_requests SET status = 'approved' WHERE id = :id"
    ), {"id": req_id})
    db.commit()
    return {"approved": True, "chat_id": chat_id}


@router.post("/admin/restore-requests/{req_id}/reject")
def reject_restore_request(req_id: int, admin_token: str, db: Session = Depends(get_db)):
    from app.core.config import get_settings
    from sqlalchemy import text as sqlt
    s = get_settings()
    if admin_token != s.ADMIN_SECRET_KEY:
        raise HTTPException(status_code=403, detail="Owner access required")

    result = db.execute(sqlt(
        "UPDATE restore_requests SET status = 'rejected' WHERE id = :id"
    ), {"id": req_id})
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Request not found")
    return {"rejected": True}
