from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.db.session import get_db
from app.core.security import decode_token
from app.core.config import get_settings
from app.models.user import User
from jose import JWTError

router = APIRouter()
settings = get_settings()


def _ensure_feedback_table(db: Session):
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS user_feedback (
            id SERIAL PRIMARY KEY,
            user_id INTEGER,
            user_name VARCHAR(200),
            user_email VARCHAR(200),
            chat_id INTEGER,
            message TEXT NOT NULL,
            rating INTEGER,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """))
    db.commit()


@router.post("/feedback")
def submit_feedback(body: dict, db: Session = Depends(get_db)):
    """Submit feedback. Works for both logged-in and anonymous users."""
    _ensure_feedback_table(db)

    message = (body.get("message") or "").strip()
    if not message or len(message) < 5:
        raise HTTPException(status_code=400, detail="Feedback message too short")
    if len(message) > 2000:
        raise HTTPException(status_code=400, detail="Feedback message too long")

    user_id = None
    user_name = body.get("user_name", "Anonymous")
    user_email = body.get("user_email", "")
    chat_id = body.get("chat_id")
    rating = body.get("rating")  # optional 1-5

    token = body.get("token")
    if token:
        try:
            payload = decode_token(token)
            uid = int(payload.get("sub"))
            user = db.query(User).filter(User.id == uid).first()
            if user:
                user_id = user.id
                user_name = user.full_name or user.email.split("@")[0]
                user_email = user.email
        except (JWTError, ValueError):
            pass

    db.execute(text("""
        INSERT INTO user_feedback (user_id, user_name, user_email, chat_id, message, rating)
        VALUES (:uid, :name, :email, :cid, :msg, :rating)
    """), {
        "uid": user_id,
        "name": user_name,
        "email": user_email,
        "cid": chat_id,
        "msg": message,
        "rating": rating,
    })
    db.commit()
    return {"submitted": True, "message": "Thank you for your feedback!"}


@router.get("/admin/feedback")
def get_feedback(admin_token: str, db: Session = Depends(get_db)):
    """Admin endpoint to view all submitted feedback."""
    from app.core.limiter import get_dynamic_role
    if admin_token != settings.ADMIN_SECRET_KEY and not get_dynamic_role(admin_token):
        raise HTTPException(status_code=403, detail="Admin access required")

    _ensure_feedback_table(db)
    rows = db.execute(text("""
        SELECT id, user_id, user_name, user_email, chat_id, message, rating, created_at
        FROM user_feedback
        ORDER BY created_at DESC
        LIMIT 200
    """)).fetchall()

    return JSONResponse(content=[{
        "id": r[0],
        "user_id": r[1],
        "user_name": r[2],
        "user_email": r[3],
        "chat_id": r[4],
        "message": r[5],
        "rating": r[6],
        "created_at": r[7].isoformat() if r[7] else None,
    } for r in rows])


@router.delete("/admin/feedback/{feedback_id}")
def delete_feedback(feedback_id: int, admin_token: str, db: Session = Depends(get_db)):
    if admin_token != settings.ADMIN_SECRET_KEY:
        raise HTTPException(status_code=403, detail="Owner access required")
    _ensure_feedback_table(db)
    db.execute(text("DELETE FROM user_feedback WHERE id = :id"), {"id": feedback_id})
    db.commit()
    return {"deleted": True}
