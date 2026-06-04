from fastapi import APIRouter, HTTPException, Depends, Header
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.db.session import get_db
from app.models.firm import Firm, FirmPlan, FirmStatus
from app.models.user import User, UserRole, SubscriptionTier
from app.core.config import get_settings
from app.core.security import hash_password, create_access_token, decode_token
from datetime import datetime, timezone, timedelta
from jose import JWTError
import re
import secrets

router = APIRouter()
settings = get_settings()


def make_slug(name: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    return slug[:50]


def get_current_user(token: str, db: Session) -> User:
    try:
        payload = decode_token(token)
        user_id = int(payload.get("sub"))
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def require_firm_admin(token: str, db: Session) -> tuple[User, Firm]:
    user = get_current_user(token, db)
    if not user.firm_id:
        raise HTTPException(status_code=403, detail="No firm associated")
    if not user.is_firm_admin:
        raise HTTPException(status_code=403, detail="Firm admin access required")
    firm = db.query(Firm).filter(Firm.id == user.firm_id).first()
    if not firm:
        raise HTTPException(status_code=404, detail="Firm not found")
    return user, firm


# ── Register a firm ───────────────────────────────────────────────────────────
@router.post("/firm/register")
def register_firm(body: dict, db: Session = Depends(get_db)):
    """Create a new firm and its owner account."""
    name = body.get("name", "").strip()
    billing_email = body.get("billing_email", "").strip()
    owner_email = body.get("owner_email", "").strip()
    owner_password = body.get("owner_password", "")
    owner_name = body.get("owner_name", "").strip()
    phone = body.get("phone", "").strip()

    if not name or not owner_email or not owner_password:
        raise HTTPException(status_code=400, detail="Name, email and password required")
    if len(owner_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    # Check email not taken
    if db.query(User).filter(User.email == owner_email).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    # Create unique slug
    base_slug = make_slug(name)
    slug = base_slug
    counter = 1
    while db.query(Firm).filter(Firm.slug == slug).first():
        slug = f"{base_slug}-{counter}"
        counter += 1

    # Create firm (30 day trial)
    firm = Firm(
        name=name,
        slug=slug,
        billing_email=billing_email or owner_email,
        phone=phone,
        plan=FirmPlan.trial,
        status=FirmStatus.trial,
        seats_limit=5,
        queries_per_seat=200,
        trial_ends_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db.add(firm)
    db.flush()  # get firm.id

    # Create owner
    owner = User(
        email=owner_email,
        hashed_password=hash_password(owner_password),
        full_name=owner_name or owner_email.split("@")[0],
        firm_id=firm.id,
        role=UserRole.firm_owner,
        invite_accepted=True,
        subscription_tier=SubscriptionTier.professional,
    )
    db.add(owner)
    db.commit()
    db.refresh(owner)

    token = create_access_token({"sub": str(owner.id), "email": owner.email, "firm_id": firm.id})
    return {
        "access_token": token,
        "token_type": "bearer",
        "firm": {
            "id": firm.id,
            "name": firm.name,
            "slug": firm.slug,
            "plan": firm.plan.value,
            "status": firm.status.value,
            "trial_ends_at": firm.trial_ends_at.isoformat() if firm.trial_ends_at else None,
        }
    }


# ── Auto-create firm for a Pro individual user ────────────────────────────────
@router.post("/firm/auto-create")
def auto_create_firm(token: str, db: Session = Depends(get_db)):
    """Create a firm on-demand for a Pro individual user who has no firm yet."""
    user = get_current_user(token, db)
    if user.firm_id:
        raise HTTPException(status_code=400, detail="Already in a firm")
    if user.subscription_tier != SubscriptionTier.professional:
        raise HTTPException(status_code=403, detail="Pro plan required to create a firm")

    name = user.full_name or user.email.split("@")[0]
    base_slug = make_slug(name)
    slug = base_slug
    counter = 1
    while db.query(Firm).filter(Firm.slug == slug).first():
        slug = f"{base_slug}-{counter}"
        counter += 1

    firm = Firm(
        name=name,
        slug=slug,
        billing_email=user.email,
        plan=FirmPlan.pro,
        status=FirmStatus.active,
        seats_limit=10,
        queries_per_seat=999999,
    )
    db.add(firm)
    db.flush()

    user.firm_id = firm.id
    user.role = UserRole.firm_owner
    db.commit()
    db.refresh(firm)

    return {"firm_id": firm.id, "firm_name": firm.name}


# ── Get firm info ─────────────────────────────────────────────────────────────
@router.get("/firm/me")
def get_firm(token: str, db: Session = Depends(get_db)):
    user, firm = require_firm_admin(token, db)
    seats_used = db.query(User).filter(
        User.firm_id == firm.id,
        User.invite_accepted == True
    ).count()
    pending = db.query(User).filter(
        User.firm_id == firm.id,
        User.invite_accepted == False
    ).count()
    return {
        "id": firm.id,
        "name": firm.name,
        "slug": firm.slug,
        "plan": firm.plan.value,
        "status": firm.status.value,
        "seats_limit": firm.seats_limit,
        "seats_used": seats_used,
        "seats_pending": pending,
        "queries_per_seat": firm.queries_per_seat,
        "trial_ends_at": firm.trial_ends_at.isoformat() if firm.trial_ends_at else None,
    }


# ── List firm members ─────────────────────────────────────────────────────────
@router.get("/firm/members")
def list_members(token: str, db: Session = Depends(get_db)):
    user, firm = require_firm_admin(token, db)
    members = db.query(User).filter(User.firm_id == firm.id).all()
    return [{
        "id": m.id,
        "email": m.email,
        "full_name": m.full_name or "-",
        "role": m.role.value,
        "status": m.status,
        "invite_accepted": m.invite_accepted,
        "invite_link": f"https://asktax.pk/join.html?token={m.invite_token}" if not m.invite_accepted and m.invite_token else None,
        "queries_used": m.queries_used_this_month,
        "queries_remaining": m.queries_remaining,
        "joined": m.created_at.isoformat() if m.created_at else None,
    } for m in members]


# ── Invite a member ───────────────────────────────────────────────────────────
@router.post("/firm/invite")
def invite_member(body: dict, token: str, db: Session = Depends(get_db)):
    user, firm = require_firm_admin(token, db)

    email = body.get("email", "").strip().lower()
    role = body.get("role", "firm_member")

    if not email:
        raise HTTPException(status_code=400, detail="Email required")
    if role not in ("firm_admin", "firm_member"):
        raise HTTPException(status_code=400, detail="Invalid role")

    # Check seats
    seats_used = db.query(User).filter(
        User.firm_id == firm.id,
        User.invite_accepted == True
    ).count()
    if seats_used >= firm.seats_limit:
        raise HTTPException(status_code=400, detail=f"Seat limit reached ({firm.seats_limit} seats)")

    invite_token = secrets.token_urlsafe(32)
    invite_link = f"https://asktax.pk/join.html?token={invite_token}"

    # Check if already member or registered
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        if existing.firm_id == firm.id:
            raise HTTPException(status_code=400, detail="Already a member of this firm")
        if existing.firm_id is not None:
            raise HTTPException(status_code=400, detail="This person is already a member of another firm")
        # Existing individual user — offer firm membership
        existing.firm_id = firm.id
        existing.role = UserRole.firm_admin if role == "firm_admin" else UserRole.firm_member
        existing.invite_token = invite_token
        existing.invite_accepted = False
        existing.invited_by_id = user.id
        db.commit()
        return {"status": "invited", "email": email, "invite_link": invite_link, "message": f"Share this link with {email} to join {firm.name}"}

    # New user — create pending account
    pending = User(
        email=email,
        hashed_password=None,
        firm_id=firm.id,
        role=UserRole.firm_admin if role == "firm_admin" else UserRole.firm_member,
        invite_token=invite_token,
        invite_accepted=False,
        invited_by_id=user.id,
        subscription_tier=SubscriptionTier.professional,
    )
    db.add(pending)
    db.commit()
    return {"status": "invited", "email": email, "invite_link": invite_link, "message": f"Share this link with {email} to join {firm.name}"}


# ── Accept invite ─────────────────────────────────────────────────────────────
@router.post("/firm/accept-invite")
def accept_invite(body: dict, db: Session = Depends(get_db)):
    invite_token = body.get("token", "").strip()
    full_name = body.get("full_name", "").strip()
    password = body.get("password", "")

    if not invite_token or not password:
        raise HTTPException(status_code=400, detail="Token and password required")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    user = db.query(User).filter(User.invite_token == invite_token).first()
    if not user:
        raise HTTPException(status_code=404, detail="Invalid or expired invite link")
    if user.invite_accepted:
        raise HTTPException(status_code=400, detail="Invite already accepted")

    # Check firm still active
    firm = db.query(Firm).filter(Firm.id == user.firm_id).first()
    if not firm or firm.status == FirmStatus.suspended:
        raise HTTPException(status_code=403, detail="Firm account is not active")

    user.hashed_password = hash_password(password)
    user.full_name = full_name or user.email.split("@")[0]
    user.invite_accepted = True
    user.invite_token = None
    db.commit()
    db.refresh(user)

    token = create_access_token({"sub": str(user.id), "email": user.email, "firm_id": firm.id})
    return {
        "access_token": token,
        "token_type": "bearer",
        "firm_name": firm.name,
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role.value,
        }
    }


# ── Get invite info (for join page) ──────────────────────────────────────────
@router.get("/firm/invite-info")
def get_invite_info(token: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.invite_token == token).first()
    if not user:
        raise HTTPException(status_code=404, detail="Invalid invite link")
    if user.invite_accepted:
        raise HTTPException(status_code=400, detail="Invite already accepted")
    firm = db.query(Firm).filter(Firm.id == user.firm_id).first()
    return {
        "email": user.email,
        "firm_name": firm.name if firm else "Unknown",
        "role": user.role.value,
    }


# ── Get invite link for a pending member ─────────────────────────────────────
@router.get("/firm/members/{member_id}/invite-link")
def get_invite_link(member_id: int, token: str, db: Session = Depends(get_db)):
    admin, firm = require_firm_admin(token, db)
    member = db.query(User).filter(User.id == member_id, User.firm_id == firm.id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    if member.invite_accepted:
        raise HTTPException(status_code=400, detail="Member already accepted the invite")
    if not member.invite_token:
        member.invite_token = secrets.token_urlsafe(32)
        db.commit()
    return {"invite_link": f"https://asktax.pk/join.html?token={member.invite_token}"}


# ── Remove member ─────────────────────────────────────────────────────────────
@router.delete("/firm/members/{member_id}")
def remove_member(member_id: int, token: str, db: Session = Depends(get_db)):
    admin, firm = require_firm_admin(token, db)
    member = db.query(User).filter(User.id == member_id, User.firm_id == firm.id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    if member.role == UserRole.firm_owner:
        raise HTTPException(status_code=403, detail="Cannot remove firm owner")
    if member.id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot remove yourself")
    member.firm_id = None
    member.role = UserRole.individual
    db.commit()
    return {"status": "success"}


# ── Update member role ────────────────────────────────────────────────────────
@router.put("/firm/members/{member_id}/role")
def update_member_role(member_id: int, body: dict, token: str, db: Session = Depends(get_db)):
    admin, firm = require_firm_admin(token, db)
    member = db.query(User).filter(User.id == member_id, User.firm_id == firm.id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    if member.role == UserRole.firm_owner:
        raise HTTPException(status_code=403, detail="Cannot change owner role")
    role = body.get("role", "")
    if role not in ("firm_admin", "firm_member"):
        raise HTTPException(status_code=400, detail="Invalid role")
    member.role = UserRole.firm_admin if role == "firm_admin" else UserRole.firm_member
    db.commit()
    return {"status": "success"}