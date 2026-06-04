"""
Collaborative chat sessions: two firm members query the same session together.
Turn-based: one question at a time, alternating between host and guest.
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import text as sqlt
from app.db.session import get_db
from app.models.user import User
from app.models.query import QueryHistory
from app.core.security import decode_token
from jose import JWTError

router = APIRouter()


def _get_user(token: str, db: Session) -> User:
    try:
        payload = decode_token(token)
        user_id = int(payload.get("sub"))
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _fmt(q: QueryHistory, asker_name: str = None) -> dict:
    d = {
        "id": q.id,
        "query": q.query_text,
        "response_en": q.response_en,
        "response_ur": q.response_ur,
        "confidence_score": q.confidence_score,
        "session_id": q.session_id,
        "created_at": q.created_at.isoformat(),
        "asker_id": q.user_id,
    }
    if asker_name:
        d["asker_name"] = asker_name
    return d


@router.get("/firm/members-list")
def get_firm_members(token: str, db: Session = Depends(get_db)):
    """Return firm members (for collab invite picker)."""
    user = _get_user(token, db)
    if not user.firm_id:
        raise HTTPException(status_code=403, detail="Firm membership required")
    members = db.query(User).filter(
        User.firm_id == user.firm_id,
        User.id != user.id,
        User.invite_accepted == True,
    ).all()
    return [{"id": m.id, "name": m.full_name or m.email.split("@")[0], "email": m.email, "role": m.role.value} for m in members]


@router.post("/collab/invite")
def invite_to_collab(body: dict, token: str, db: Session = Depends(get_db)):
    """Invite a firm member to collaborate on the current session."""
    user = _get_user(token, db)
    if not user.firm_id:
        raise HTTPException(status_code=403, detail="Firm membership required")

    session_id = (body.get("session_id") or "").strip()
    guest_user_id = body.get("guest_user_id")

    if not session_id or not guest_user_id:
        raise HTTPException(status_code=400, detail="session_id and guest_user_id required")

    guest = db.query(User).filter(User.id == guest_user_id, User.firm_id == user.firm_id, User.invite_accepted == True).first()
    if not guest:
        raise HTTPException(status_code=404, detail="Team member not found")
    if guest.id == user.id:
        raise HTTPException(status_code=400, detail="Cannot invite yourself")

    # Cancel any stale pending invites from this host in this session
    db.execute(sqlt("""
        UPDATE collab_sessions SET status = 'ended' WHERE session_id = :sid AND host_user_id = :hid AND status = 'pending'
    """), {"sid": session_id, "hid": user.id})

    # Check if already an active collab
    existing = db.execute(sqlt("""
        SELECT id FROM collab_sessions WHERE session_id = :sid AND status = 'active'
    """), {"sid": session_id}).fetchone()
    if existing:
        raise HTTPException(status_code=400, detail="A collaboration is already active for this session")

    db.execute(sqlt("""
        INSERT INTO collab_sessions (session_id, host_user_id, guest_user_id, status, turn_user_id)
        VALUES (:sid, :host, :guest, 'pending', :turn)
    """), {"sid": session_id, "host": user.id, "guest": guest_user_id, "turn": user.id})
    db.commit()

    return {"status": "invited", "message": f"Invitation sent to {guest.full_name or guest.email}"}


@router.get("/collab/incoming")
def get_incoming_invites(token: str, db: Session = Depends(get_db)):
    """Poll for pending collab invitations."""
    user = _get_user(token, db)
    rows = db.execute(sqlt("""
        SELECT c.id, c.session_id, c.host_user_id, u.full_name, u.email, c.created_at
        FROM collab_sessions c
        JOIN users u ON u.id = c.host_user_id
        WHERE c.guest_user_id = :uid AND c.status = 'pending'
        ORDER BY c.created_at DESC LIMIT 5
    """), {"uid": user.id}).fetchall()
    return [{"id": r[0], "session_id": r[1], "host_id": r[2], "host_name": r[3] or r[4].split("@")[0], "created_at": r[5].isoformat()} for r in rows]


@router.put("/collab/accept/{collab_id}")
def accept_collab(collab_id: int, token: str, db: Session = Depends(get_db)):
    user = _get_user(token, db)
    row = db.execute(sqlt("""
        SELECT session_id, host_user_id FROM collab_sessions
        WHERE id = :id AND guest_user_id = :uid AND status = 'pending'
    """), {"id": collab_id, "uid": user.id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Invite not found")
    db.execute(sqlt("UPDATE collab_sessions SET status = 'active' WHERE id = :id"), {"id": collab_id})
    db.commit()
    return {"status": "active", "session_id": row[0], "host_id": row[1]}


@router.put("/collab/decline/{collab_id}")
def decline_collab(collab_id: int, token: str, db: Session = Depends(get_db)):
    user = _get_user(token, db)
    db.execute(sqlt("UPDATE collab_sessions SET status = 'ended' WHERE id = :id AND guest_user_id = :uid"), {"id": collab_id, "uid": user.id})
    db.commit()
    return {"status": "declined"}


@router.get("/collab/status/{session_id}")
def get_collab_status(session_id: str, token: str, db: Session = Depends(get_db)):
    """Get collab state for a session (both participants poll this)."""
    user = _get_user(token, db)
    row = db.execute(sqlt("""
        SELECT c.id, c.status, c.turn_user_id, c.host_user_id, c.guest_user_id,
               uh.full_name, uh.email, ug.full_name, ug.email
        FROM collab_sessions c
        JOIN users uh ON uh.id = c.host_user_id
        JOIN users ug ON ug.id = c.guest_user_id
        WHERE c.session_id = :sid
          AND (c.host_user_id = :uid OR c.guest_user_id = :uid)
          AND c.status IN ('pending', 'active')
        ORDER BY c.created_at DESC LIMIT 1
    """), {"sid": session_id, "uid": user.id}).fetchone()

    if not row:
        return JSONResponse(content={"status": "none"})

    is_host = row[3] == user.id
    partner_id = row[4] if is_host else row[3]
    partner_name = (row[7] or row[8].split("@")[0]) if is_host else (row[5] or row[6].split("@")[0])

    return JSONResponse(content={
        "collab_id": row[0],
        "status": row[1],
        "is_my_turn": row[2] == user.id,
        "is_host": is_host,
        "partner_id": partner_id,
        "partner_name": partner_name,
        "my_id": user.id,
    })


@router.get("/collab/messages/{session_id}")
def get_collab_messages(session_id: str, token: str, since: str = None, db: Session = Depends(get_db)):
    """Get all messages from both participants in a collab session."""
    user = _get_user(token, db)
    row = db.execute(sqlt("""
        SELECT host_user_id, guest_user_id FROM collab_sessions
        WHERE session_id = :sid AND (host_user_id = :uid OR guest_user_id = :uid)
        AND status IN ('pending', 'active') LIMIT 1
    """), {"sid": session_id, "uid": user.id}).fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="Not a participant in this session")

    participant_ids = [row[0], row[1]]
    members = db.query(User).filter(User.id.in_(participant_ids)).all()
    name_map = {m.id: (m.full_name or m.email.split("@")[0]) for m in members}

    q_filter = [QueryHistory.session_id == session_id, QueryHistory.user_id.in_(participant_ids), QueryHistory.deleted_at.is_(None)]
    if since:
        from datetime import datetime
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            q_filter.append(QueryHistory.created_at > since_dt)
        except Exception:
            pass

    queries = db.query(QueryHistory).filter(*q_filter).order_by(QueryHistory.created_at.asc()).all()
    return JSONResponse(content=[_fmt(q, name_map.get(q.user_id)) for q in queries])


@router.post("/collab/end/{session_id}")
def end_collab(session_id: str, token: str, db: Session = Depends(get_db)):
    user = _get_user(token, db)
    db.execute(sqlt("""
        UPDATE collab_sessions SET status = 'ended', ended_at = NOW()
        WHERE session_id = :sid AND (host_user_id = :uid OR guest_user_id = :uid)
    """), {"sid": session_id, "uid": user.id})
    db.commit()
    return {"status": "ended"}
