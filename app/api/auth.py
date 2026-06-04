from fastapi import APIRouter, HTTPException, Depends, UploadFile, File
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.user import User, SubscriptionTier, UserRole
from app.models.firm import Firm, FirmStatus
from app.core.security import hash_password, verify_password, create_access_token, decode_token
from app.core.config import get_settings
from jose import JWTError
import io

router = APIRouter()
settings = get_settings()

ALLOWED_IMG_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_AVATAR_BYTES = 5 * 1024 * 1024  # 5 MB


def _get_minio():
    from minio import Minio
    return Minio(
        settings.MINIO_ENDPOINT,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        secure=False,
    )


@router.post("/auth/register")
def register(body: dict, db: Session = Depends(get_db)):
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")
    full_name = body.get("full_name", "").strip()

    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password required")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email=email,
        hashed_password=hash_password(password),
        full_name=full_name or email.split("@")[0],
        subscription_tier=SubscriptionTier.free,
        role=UserRole.individual,
        invite_accepted=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token({"sub": str(user.id), "email": user.email})
    return {"access_token": token, "token_type": "bearer"}


@router.post("/auth/login")
def login(body: dict, db: Session = Depends(get_db)):
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")

    user = db.query(User).filter(User.email == email).first()
    if not user or not user.hashed_password:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not verify_password(password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.invite_accepted:
        raise HTTPException(status_code=403, detail="Please accept your invite first")
    if user.status != "active":
        raise HTTPException(status_code=403, detail=f"Account is {user.status.replace('_', ' ')}")

    if user.firm_id:
        firm = db.query(Firm).filter(Firm.id == user.firm_id).first()
        if firm and firm.status == FirmStatus.suspended:
            raise HTTPException(status_code=403, detail="Your firm account has been suspended")

    payload = {"sub": str(user.id), "email": user.email}
    if user.firm_id:
        payload["firm_id"] = user.firm_id

    firm_name = None
    if user.firm_id and user.firm:
        firm_name = user.firm.name

    token = create_access_token(payload)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role.value,
            "firm_id": user.firm_id,
            "firm_name": firm_name,
            "queries_remaining": user.queries_remaining,
            "is_firm_admin": user.is_firm_admin,
        }
    }


@router.get("/auth/me")
def get_me(token: str, db: Session = Depends(get_db)):
    try:
        payload = decode_token(token)
        user_id = int(payload.get("sub"))
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    firm_info = None
    if user.firm_id:
        firm = db.query(Firm).filter(Firm.id == user.firm_id).first()
        if firm:
            firm_info = {
                "id": firm.id,
                "name": firm.name,
                "plan": firm.plan.value,
                "status": firm.status.value,
            }

    limit = user.effective_query_limit
    avatar_url = f"/api/auth/avatar?token={token}" if user.avatar_url else None

    # Determine trial status — individual trial_expires_at takes priority over firm status
    is_on_trial = False
    from datetime import datetime, timezone as tz
    if user.trial_expires_at and user.trial_expires_at > datetime.now(tz.utc):
        is_on_trial = True
    elif user.firm_id and user.firm:
        from app.models.firm import FirmStatus
        if user.firm.status == FirmStatus.trial:
            is_on_trial = True

    is_pro = False
    if user.firm_id and user.firm:
        is_pro = user.firm.plan.value == "pro"
    else:
        from app.models.user import SubscriptionTier
        is_pro = user.subscription_tier == SubscriptionTier.professional

    data = {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role.value,
        "subscription_tier": user.subscription_tier.value,
        "queries_remaining": user.queries_remaining,
        "queries_used": user.queries_used_this_month,
        "query_limit": limit if limit < 999999 else None,
        "is_firm_admin": user.is_firm_admin,
        "is_on_trial": is_on_trial,
        "is_pro": is_pro,
        "trial_expires_at": user.trial_expires_at.isoformat() if user.trial_expires_at else None,
        "firm": firm_info,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "avatar_url": avatar_url,
    }
    return JSONResponse(content=data, headers={"Cache-Control": "no-store, no-cache, must-revalidate"})


@router.patch("/auth/me")
def update_profile(token: str, body: dict, db: Session = Depends(get_db)):
    """Update user's full_name."""
    try:
        payload = decode_token(token)
        user_id = int(payload.get("sub"))
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    new_name = (body.get("full_name") or "").strip()
    if not new_name or len(new_name) < 2:
        raise HTTPException(status_code=400, detail="Name must be at least 2 characters")
    if len(new_name) > 200:
        raise HTTPException(status_code=400, detail="Name too long")

    user.full_name = new_name
    db.commit()
    return {"full_name": user.full_name}


@router.delete("/auth/me")
def delete_account(token: str, db: Session = Depends(get_db)):
    """Soft-delete the user's own account by marking status='deleted'."""
    try:
        payload = decode_token(token)
        user_id = int(payload.get("sub"))
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.status = "deleted"
    db.commit()
    return {"deleted": True, "message": "Your account has been deleted. Contact us to recover it."}


@router.post("/auth/change-password")
def change_password(body: dict, db: Session = Depends(get_db)):
    try:
        payload = decode_token(body.get("token", ""))
        user_id = int(payload.get("sub"))
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.hashed_password or not verify_password(body.get("old_password", ""), user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    new_pw = body.get("new_password", "")
    if len(new_pw) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")

    user.hashed_password = hash_password(new_pw)
    db.commit()
    return {"status": "ok", "message": "Password changed successfully"}


@router.post("/auth/avatar")
async def upload_avatar(token: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    try:
        payload = decode_token(token)
        user_id = int(payload.get("sub"))
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")

    if file.content_type not in ALLOWED_IMG_TYPES:
        raise HTTPException(status_code=400, detail="Only JPEG, PNG, WebP, or GIF allowed")

    content = await file.read()
    if len(content) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=400, detail="Image must be under 5 MB")

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else "jpg"
    obj_name = f"avatars/user_{user_id}.{ext}"

    try:
        mc = _get_minio()
        if not mc.bucket_exists(settings.MINIO_BUCKET):
            mc.make_bucket(settings.MINIO_BUCKET)
        mc.put_object(
            settings.MINIO_BUCKET,
            obj_name,
            io.BytesIO(content),
            length=len(content),
            content_type=file.content_type,
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Avatar upload failed for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.avatar_url = obj_name
    db.commit()

    return {"avatar_url": f"/api/auth/avatar?token={token}"}


@router.get("/auth/avatar")
def get_avatar(token: str, db: Session = Depends(get_db)):
    try:
        payload = decode_token(token)
        user_id = int(payload.get("sub"))
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.avatar_url:
        raise HTTPException(status_code=404, detail="No avatar")

    try:
        mc = _get_minio()
        obj = mc.get_object(settings.MINIO_BUCKET, user.avatar_url)
        data = obj.read()
        ext = user.avatar_url.rsplit(".", 1)[-1].lower()
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                "webp": "image/webp", "gif": "image/gif"}.get(ext, "image/jpeg")
        return StreamingResponse(
            io.BytesIO(data),
            media_type=mime,
            headers={"Cache-Control": "max-age=3600"},
        )
    except Exception:
        raise HTTPException(status_code=404, detail="Avatar not found")
