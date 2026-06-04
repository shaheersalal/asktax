"""
Messaging system: user ↔ admin/support
Also handles trial/subscription requests.
"""
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from app.db.session import SessionLocal
from app.core.security import decode_token
from app.models.user import User, SubscriptionTier
from jose import JWTError

router = APIRouter()
logger = logging.getLogger(__name__)

ADMIN_PASSWORDS = None  # lazy-loaded from admin.py pattern


def _get_user(token: str):
    db = SessionLocal()
    try:
        payload = decode_token(token)
        uid = int(payload.get("sub"))
        user = db.query(User).filter(User.id == uid).first()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user, db
    except (JWTError, ValueError):
        db.close()
        raise HTTPException(status_code=401, detail="Invalid token")


def _check_admin(admin_token: str):
    """Validate admin token against settings and dynamic passwords."""
    from app.core.config import get_settings
    from app.core.limiter import get_dynamic_role
    settings = get_settings()
    if admin_token == settings.ADMIN_SECRET_KEY or get_dynamic_role(admin_token):
        return True
    raise HTTPException(status_code=403, detail="Invalid admin token")


# ── Template messages ──────────────────────────────────────────────────────────

PAYMENT_TEMPLATE = """Hi {name},

Thank you for requesting access to AskTax.pk {plan} Plan! 🎉

We're currently resolving an issue with our online payment gateway — we apologise for the inconvenience. In the meantime, subscriptions are activated manually after payment confirmation.

━━━━━━━━━━━━━━━━
💳 PAYMENT DETAILS
━━━━━━━━━━━━━━━━
📱 JazzCash / EasyPaisa: 03312228870
🏦 Bank Transfer: (contact us for account details)

Amount:
  • Basic Plan: PKR 2,999/month or PKR 29,990/year
  • Pro Plan: PKR 5,999/month or PKR 59,990/year

━━━━━━━━━━━━━━━━
📋 NEXT STEPS
━━━━━━━━━━━━━━━━
1. Send the subscription amount via JazzCash or EasyPaisa to 03312228870
2. Reply to this message with your payment screenshot
3. Your account will be upgraded within 1 hour (Mon–Sat, 10am–6pm PKT)

If you have any questions, just reply here and we'll get back to you promptly.

Thank you for choosing AskTax.pk!

— AskTax.pk Support Team"""

TRIAL_APPROVED_TEMPLATE = """Hi {name},

Great news! 🎉 Your 1-month free trial for AskTax.pk {plan} Plan has been activated.

Your account is now upgraded. You can log in and start using all features immediately.

Trial duration: 30 days from today
Plan: {plan}

If you need any help getting started, feel free to reply here.

— AskTax.pk Support Team"""

TRIAL_REJECTED_TEMPLATE = """Hi {name},

Thank you for your interest in AskTax.pk {plan} Plan.

Unfortunately, we're unable to approve your trial request at this time. {reason}

You're welcome to continue using the free plan (20 queries). If you have questions, just reply here.

— AskTax.pk Support Team"""


# ── USER ENDPOINTS ─────────────────────────────────────────────────────────────

@router.get("/messages")
def get_my_messages(token: str):
    user, db = _get_user(token)
    try:
        rows = db.execute(text("""
            SELECT id, from_admin, body, read_at, created_at
            FROM messages WHERE user_id = :uid
            ORDER BY created_at ASC
        """), {"uid": user.id}).fetchall()

        # Mark unread admin messages as read
        db.execute(text("""
            UPDATE messages SET read_at = NOW()
            WHERE user_id = :uid AND from_admin = TRUE AND read_at IS NULL
        """), {"uid": user.id})
        db.commit()

        return [
            {
                "id": r[0],
                "from_admin": r[1],
                "body": r[2],
                "read_at": r[3].isoformat() if r[3] else None,
                "created_at": r[4].isoformat() if r[4] else None,
            }
            for r in rows
        ]
    finally:
        db.close()


@router.post("/messages")
def send_message(token: str, body: dict):
    user, db = _get_user(token)
    try:
        text_body = (body.get("body") or "").strip()[:2000]
        if not text_body:
            raise HTTPException(status_code=400, detail="Message cannot be empty")

        db.execute(text("""
            INSERT INTO messages (user_id, from_admin, body)
            VALUES (:uid, FALSE, :body)
        """), {"uid": user.id, "body": text_body})
        db.commit()
        return {"sent": True}
    finally:
        db.close()


@router.post("/trial/start")
def start_trial(token: str, body: dict):
    """Instant self-service trial activation — no admin review needed."""
    user, db = _get_user(token)
    try:
        plan = (body.get("plan") or "basic").strip().lower()
        if plan not in ("basic", "professional"):
            raise HTTPException(status_code=400, detail="Invalid plan")

        if user.firm_id:
            raise HTTPException(status_code=400, detail="Firm users cannot activate individual trials")

        from datetime import datetime, timezone, timedelta
        from app.models.user import SubscriptionTier
        now = datetime.now(timezone.utc)

        current = user.subscription_tier.value
        if current == "professional":
            raise HTTPException(status_code=400, detail="Your account is already on Pro plan")
        if current == "basic" and plan == "basic":
            raise HTTPException(status_code=400, detail="Your account is already on Basic plan")

        # Prevent re-activation if an active trial for same or higher plan exists
        if user.trial_expires_at and user.trial_expires_at > now:
            if current == plan or (current == "professional" and plan == "basic"):
                raise HTTPException(status_code=400, detail="You already have an active trial")

        trial_ends = now + timedelta(days=30)
        tier = SubscriptionTier.professional if plan == "professional" else SubscriptionTier.basic

        db.execute(text("""
            UPDATE users SET subscription_tier = :tier, trial_expires_at = :expires WHERE id = :uid
        """), {"tier": tier.value, "uid": user.id, "expires": trial_ends})
        db.commit()

        plan_label = "Pro" if plan == "professional" else "Basic"
        return {
            "activated": True,
            "plan": plan,
            "trial_expires_at": trial_ends.isoformat(),
            "message": f"{plan_label} trial activated. Expires {trial_ends.strftime('%d %b %Y')}.",
        }
    finally:
        db.close()


@router.post("/trial-request")
def request_trial(token: str, body: dict):
    user, db = _get_user(token)
    try:
        plan = (body.get("plan") or "basic").strip().lower()
        billing = (body.get("billing_cycle") or "monthly").strip().lower()
        if plan not in ("basic", "professional"):
            raise HTTPException(status_code=400, detail="Invalid plan")

        # Check if already requested for this specific plan
        existing = db.execute(text("""
            SELECT id, status, plan FROM trial_requests
            WHERE user_id = :uid AND status IN ('pending', 'approved')
            ORDER BY id DESC LIMIT 1
        """), {"uid": user.id}).fetchone()

        if existing:
            ex_status, ex_plan = existing[1], existing[2]
            if ex_status == "approved" and ex_plan == plan:
                raise HTTPException(status_code=400, detail="Your account is already on this plan.")
            if ex_status == "pending" and ex_plan == plan:
                return {"requested": True, "message": "You already have a pending request for this plan. We'll contact you soon."}
            # Different plan (e.g. approved Basic → requesting Pro): allow upgrade request

        plan_label = "Basic" if plan == "basic" else "Pro"
        db.execute(text("""
            INSERT INTO trial_requests (user_id, user_name, user_email, plan, billing_cycle)
            VALUES (:uid, :name, :email, :plan, :billing)
            ON CONFLICT (user_id) DO UPDATE SET
                plan = EXCLUDED.plan,
                billing_cycle = EXCLUDED.billing_cycle,
                status = 'pending',
                updated_at = NOW()
        """), {
            "uid": user.id,
            "name": user.full_name or user.email.split("@")[0],
            "email": user.email,
            "plan": plan,
            "billing": billing,
        })

        # Auto-send payment instructions message
        msg_body = PAYMENT_TEMPLATE.format(
            name=user.full_name or user.email.split("@")[0],
            plan=plan_label,
        )
        db.execute(text("""
            INSERT INTO messages (user_id, from_admin, body)
            VALUES (:uid, TRUE, :body)
        """), {"uid": user.id, "body": msg_body})
        db.commit()

        return {
            "requested": True,
            "message": f"Your {plan_label} plan trial request has been submitted. Check your messages for payment instructions.",
        }
    finally:
        db.close()


# ── ADMIN ENDPOINTS ────────────────────────────────────────────────────────────

@router.get("/admin/messages")
def admin_get_conversations(admin_token: str):
    _check_admin(admin_token)
    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT
                u.id, u.email, u.full_name,
                (SELECT COUNT(*) FROM messages m WHERE m.user_id = u.id AND m.from_admin = FALSE AND m.read_at IS NULL) as unread,
                (SELECT body FROM messages m WHERE m.user_id = u.id ORDER BY created_at DESC LIMIT 1) as last_msg,
                (SELECT created_at FROM messages m WHERE m.user_id = u.id ORDER BY created_at DESC LIMIT 1) as last_at,
                (SELECT COUNT(*) FROM messages m WHERE m.user_id = u.id) as total
            FROM users u
            WHERE EXISTS (SELECT 1 FROM messages m WHERE m.user_id = u.id)
            ORDER BY last_at DESC NULLS LAST
        """)).fetchall()

        return [
            {
                "user_id": r[0],
                "email": r[1],
                "name": r[2],
                "unread": r[3],
                "last_msg": (r[4] or "")[:80],
                "last_at": r[5].isoformat() if r[5] else None,
                "total": r[6],
            }
            for r in rows
        ]
    finally:
        db.close()


@router.get("/admin/messages/{user_id}")
def admin_get_thread(user_id: int, admin_token: str):
    _check_admin(admin_token)
    db = SessionLocal()
    try:
        # Mark user messages as read by admin
        db.execute(text("""
            UPDATE messages SET read_at = NOW()
            WHERE user_id = :uid AND from_admin = FALSE AND read_at IS NULL
        """), {"uid": user_id})
        db.commit()

        rows = db.execute(text("""
            SELECT id, from_admin, body, read_at, created_at
            FROM messages WHERE user_id = :uid
            ORDER BY created_at ASC
        """), {"uid": user_id}).fetchall()

        user = db.execute(text(
            "SELECT email, full_name FROM users WHERE id = :uid"
        ), {"uid": user_id}).fetchone()

        return {
            "user": {"email": user[0] if user else "", "name": user[1] if user else ""},
            "messages": [
                {
                    "id": r[0],
                    "from_admin": r[1],
                    "body": r[2],
                    "read_at": r[3].isoformat() if r[3] else None,
                    "created_at": r[4].isoformat() if r[4] else None,
                }
                for r in rows
            ],
        }
    finally:
        db.close()


@router.post("/admin/messages/{user_id}/reply")
def admin_reply(user_id: int, admin_token: str, body: dict):
    _check_admin(admin_token)
    db = SessionLocal()
    try:
        text_body = (body.get("body") or "").strip()[:4000]
        if not text_body:
            raise HTTPException(status_code=400, detail="Message cannot be empty")

        db.execute(text("""
            INSERT INTO messages (user_id, from_admin, body)
            VALUES (:uid, TRUE, :body)
        """), {"uid": user_id, "body": text_body})
        db.commit()
        return {"sent": True}
    finally:
        db.close()


@router.get("/admin/trial-requests")
def admin_get_trial_requests(admin_token: str):
    _check_admin(admin_token)
    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT id, user_id, user_name, user_email, plan, billing_cycle, status, admin_note, created_at
            FROM trial_requests
            ORDER BY
                CASE status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,
                created_at DESC
        """)).fetchall()
        return [
            {
                "id": r[0], "user_id": r[1], "user_name": r[2], "user_email": r[3],
                "plan": r[4], "billing_cycle": r[5], "status": r[6],
                "admin_note": r[7], "created_at": r[8].isoformat() if r[8] else None,
            }
            for r in rows
        ]
    finally:
        db.close()


@router.post("/admin/trial-requests/{req_id}/approve")
def admin_approve_trial(req_id: int, admin_token: str, body: dict = {}):
    _check_admin(admin_token)
    db = SessionLocal()
    try:
        req = db.execute(text(
            "SELECT user_id, plan, user_name FROM trial_requests WHERE id = :id"
        ), {"id": req_id}).fetchone()
        if not req:
            raise HTTPException(status_code=404, detail="Request not found")

        user_id, plan, user_name = req

        # Upgrade user plan
        tier = "professional" if plan == "professional" else "basic"
        db.execute(text(
            "UPDATE users SET subscription_tier = :tier WHERE id = :uid"
        ), {"tier": tier, "uid": user_id})

        # Update request status
        db.execute(text(
            "UPDATE trial_requests SET status = 'approved', updated_at = NOW() WHERE id = :id"
        ), {"id": req_id})

        # Send approval message
        plan_label = "Pro" if plan == "professional" else "Basic"
        msg = TRIAL_APPROVED_TEMPLATE.format(name=user_name or "there", plan=plan_label)
        db.execute(text("""
            INSERT INTO messages (user_id, from_admin, body)
            VALUES (:uid, TRUE, :body)
        """), {"uid": user_id, "body": msg})
        db.commit()

        return {"approved": True}
    finally:
        db.close()


@router.post("/admin/trial-requests/{req_id}/reject")
def admin_reject_trial(req_id: int, admin_token: str, body: dict = {}):
    _check_admin(admin_token)
    db = SessionLocal()
    try:
        req = db.execute(text(
            "SELECT user_id, plan, user_name FROM trial_requests WHERE id = :id"
        ), {"id": req_id}).fetchone()
        if not req:
            raise HTTPException(status_code=404, detail="Request not found")

        user_id, plan, user_name = req
        reason = (body.get("reason") or "").strip()[:500]
        plan_label = "Pro" if plan == "professional" else "Basic"

        db.execute(text("""
            UPDATE trial_requests
            SET status = 'rejected', admin_note = :note, updated_at = NOW()
            WHERE id = :id
        """), {"id": req_id, "note": reason})

        msg = TRIAL_REJECTED_TEMPLATE.format(
            name=user_name or "there",
            plan=plan_label,
            reason=reason or "We will review and get back to you.",
        )
        db.execute(text("""
            INSERT INTO messages (user_id, from_admin, body)
            VALUES (:uid, TRUE, :body)
        """), {"uid": user_id, "body": msg})
        db.commit()

        return {"rejected": True}
    finally:
        db.close()


@router.post("/admin/messages/{user_id}/send-payment-template")
def admin_send_payment_template(user_id: int, admin_token: str, body: dict = {}):
    """Send the payment instructions template to a user."""
    _check_admin(admin_token)
    db = SessionLocal()
    try:
        user = db.execute(text(
            "SELECT full_name, email FROM users WHERE id = :uid"
        ), {"uid": user_id}).fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        plan = (body.get("plan") or "Basic").strip()
        name = user[0] or user[1].split("@")[0]
        msg = PAYMENT_TEMPLATE.format(name=name, plan=plan)
        db.execute(text("""
            INSERT INTO messages (user_id, from_admin, body)
            VALUES (:uid, TRUE, :body)
        """), {"uid": user_id, "body": msg})
        db.commit()
        return {"sent": True}
    finally:
        db.close()
