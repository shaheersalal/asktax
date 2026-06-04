from fastapi import APIRouter, HTTPException, Depends, Header, UploadFile, File, Form, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from app.db.session import get_db
from app.models.user import User, SubscriptionTier
from app.models.firm import Firm, FirmPlan, FirmStatus
from app.models.query import QueryHistory
from app.models.document import FBRDocument
from app.core.config import get_settings
from app.core.limiter import (
    get_all_ip_stats, block_ip, unblock_ip,
    whitelist_ip, unwhitelist_ip, get_whitelist,
    get_dynamic_role, create_admin_password, get_all_admin_passwords, delete_admin_password,
    session_create, session_event, sessions_all,
)
from datetime import datetime, timezone
import logging
import uuid
import io

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()

# ─── ROLE DEFINITIONS ────────────────────────────────────────────────────────
ADMIN_ROLES = {
    settings.ADMIN_SECRET_KEY: "owner",
}
try:
    extra = getattr(settings, 'ASSISTANT_KEYS', '')
    if extra:
        for k in extra.split(','):
            k = k.strip()
            if k:
                ADMIN_ROLES[k] = "assistant"
except Exception as e:
    logger.warning(f"Could not load ASSISTANT_KEYS: {e}")


def get_admin_role(x_admin_token: str = Header(...)):
    role = ADMIN_ROLES.get(x_admin_token) or get_dynamic_role(x_admin_token)
    if not role:
        raise HTTPException(status_code=403, detail="Invalid admin token")
    return role


def require_owner(role: str = Depends(get_admin_role)):
    if role != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return role


def log_activity(db: Session, role: str, token: str, action: str, detail: str = ""):
    try:
        db.execute(text("""
            INSERT INTO admin_activity_log (role, token_hint, action, detail, created_at)
            VALUES (:role, :hint, :action, :detail, :ts)
        """), {
            "role": role,
            "hint": token[-6:] if len(token) > 6 else token,
            "action": action,
            "detail": detail,
            "ts": datetime.now(timezone.utc)
        })
        db.commit()
    except Exception as e:
        logger.warning(f"Failed to log activity: {e}")
        db.rollback()


# ─── ANALYTICS ───────────────────────────────────────────────────────────────
@router.get("/admin/analytics")
def get_analytics(role: str = Depends(get_admin_role), db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    total_users = db.query(User).count()
    total_queries = db.query(QueryHistory).count()
    monthly_queries = db.query(QueryHistory).filter(QueryHistory.created_at >= start_of_month).count()

    total_tokens = db.query(func.sum(QueryHistory.tokens_used)).scalar() or 0
    monthly_tokens_result = db.query(func.sum(QueryHistory.tokens_used)).filter(QueryHistory.created_at >= start_of_month).scalar()
    monthly_tokens = monthly_tokens_result or 0

    api_billed_total = round((total_tokens / 1_000_000) * 0.50, 4)
    api_billed_month = round((monthly_tokens / 1_000_000) * 0.50, 4)

    return {
        "total_users": total_users,
        "total_queries": total_queries,
        "monthly_queries": monthly_queries,
        "total_tokens": total_tokens,
        "monthly_tokens": monthly_tokens,
        "api_billed_usd": api_billed_total,
        "api_billed_month_usd": api_billed_month,
    }


# ─── USERS ───────────────────────────────────────────────────────────────────
@router.get("/admin/users")
def list_users(role: str = Depends(get_admin_role), db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.created_at.desc()).all()

    lifetime_counts = dict(
        db.query(QueryHistory.user_id, func.count(QueryHistory.id))
        .group_by(QueryHistory.user_id).all()
    )

    now = datetime.now(timezone.utc)
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    monthly_counts = dict(
        db.query(QueryHistory.user_id, func.count(QueryHistory.id))
        .filter(QueryHistory.created_at >= start_of_month)
        .group_by(QueryHistory.user_id).all()
    )

    user_tokens = dict(
        db.query(QueryHistory.user_id, func.sum(QueryHistory.tokens_used))
        .group_by(QueryHistory.user_id).all()
    )

    # Last known IP from query history
    try:
        ip_rows = db.execute(text(
            "SELECT DISTINCT ON (user_id) user_id, client_ip FROM query_history WHERE client_ip IS NOT NULL ORDER BY user_id, created_at DESC"
        )).fetchall()
        ip_map = {row[0]: row[1] for row in ip_rows}
    except Exception as e:
        logger.warning(f"Could not fetch IPs: {e}")
        ip_map = {}

    result = []
    for u in users:
        tokens = user_tokens.get(u.id, 0) or 0
        result.append({
            "id": u.id,
            "email": u.email,
            "full_name": u.full_name or "-",
            "subscription_tier": u.subscription_tier.value,
            "queries_used": u.queries_used_this_month,
            "monthly_queries": monthly_counts.get(u.id, 0),
            "lifetime_queries": lifetime_counts.get(u.id, 0),
            "status": getattr(u, 'status', 'active'),
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_ip": ip_map.get(u.id, "-"),
            "tokens_used": tokens,
            "api_cost_usd": round((tokens / 1_000_000) * 0.50, 4),
            "role": u.role.value if u.role else "individual",
            "firm_id": u.firm_id,
            "firm_name": u.firm.name if u.firm else None,
            "firm_plan": u.firm.plan.value if u.firm else None,
            "firm_status": u.firm.status.value if u.firm else None,
            "trial_expires_at": u.trial_expires_at.isoformat() if getattr(u, 'trial_expires_at', None) else None,
        })
    return result


_FIRM_PLAN_MAP = {
    "trial": FirmPlan.trial,
    "basic": FirmPlan.basic,
    "pro": FirmPlan.pro,
}

@router.put("/admin/users/{user_id}/upgrade")
def upgrade_user(user_id: int, tier: str, x_admin_token: str = Header(...), role: str = Depends(get_admin_role), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.queries_used_this_month = 0

    if user.firm_id:
        # Firm users — update the firm plan directly (trial/basic/pro)
        new_firm_plan = _FIRM_PLAN_MAP.get(tier)
        if not new_firm_plan:
            raise HTTPException(status_code=400, detail="Invalid firm plan. Use: trial, basic, pro")
        firm = db.query(Firm).filter(Firm.id == user.firm_id).first()
        if not firm:
            raise HTTPException(status_code=404, detail="Firm not found")
        firm.plan = new_firm_plan
        firm.status = FirmStatus.trial if tier == "trial" else FirmStatus.active
        db.commit()
        log_activity(db, role, x_admin_token, "upgrade_user", f"user_id={user_id} firm_plan={tier}")
        return {"status": "success", "new_tier": tier}
    else:
        # Individual users
        if tier in ("trial_basic", "trial_professional"):
            from datetime import timedelta
            plan = "basic" if tier == "trial_basic" else "professional"
            user.subscription_tier = SubscriptionTier(plan)
            user.trial_expires_at = datetime.now(timezone.utc) + timedelta(days=30)
            db.commit()
            log_activity(db, role, x_admin_token, "upgrade_user", f"user_id={user_id} tier={tier}")
            return {"status": "success", "new_tier": tier}
        try:
            new_tier = SubscriptionTier(tier)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid tier. Use: free, basic, professional, trial_basic, trial_professional")
        user.subscription_tier = new_tier
        user.trial_expires_at = None  # clear trial when setting a permanent plan
        db.commit()
        log_activity(db, role, x_admin_token, "upgrade_user", f"user_id={user_id} tier={tier}")
        return {"status": "success", "new_tier": new_tier.value}


@router.post("/admin/users/{user_id}/trial")
def admin_give_trial(user_id: int, body: dict, x_admin_token: str = Header(...), role: str = Depends(get_admin_role), db: Session = Depends(get_db)):
    """Admin: instantly give or cancel a timed trial for an individual user."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    plan = (body.get("plan") or "").strip().lower()
    days = max(1, min(int(body.get("days") or 30), 365))

    if plan == "cancel":
        user.subscription_tier = SubscriptionTier.free
        user.trial_expires_at = None
        db.commit()
        log_activity(db, role, x_admin_token, "cancel_trial", f"user_id={user_id}")
        return {"status": "cancelled"}

    if plan not in ("basic", "professional"):
        raise HTTPException(status_code=400, detail="Invalid plan. Use: basic, professional, or cancel")

    from datetime import timedelta
    trial_ends = datetime.now(timezone.utc) + timedelta(days=days)
    user.subscription_tier = SubscriptionTier.basic if plan == "basic" else SubscriptionTier.professional
    user.trial_expires_at = trial_ends
    db.commit()
    log_activity(db, role, x_admin_token, "give_trial", f"user_id={user_id} plan={plan} days={days}")
    return {"status": "success", "plan": plan, "trial_expires_at": trial_ends.isoformat()}


@router.put("/admin/users/{user_id}/status")
def update_user_status(user_id: int, status: str, x_admin_token: str = Header(...), role: str = Depends(get_admin_role), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if status not in ["active", "blocked", "temporarily_blocked"]:
        raise HTTPException(status_code=400, detail="Invalid status")
    user.status = status
    db.commit()
    log_activity(db, role, x_admin_token, "update_status", f"user_id={user_id} status={status}")
    return {"status": "success"}


@router.delete("/admin/users/{user_id}")
def delete_user(user_id: int, x_admin_token: str = Header(...), role: str = Depends(require_owner), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    db.query(QueryHistory).filter(QueryHistory.user_id == user_id).delete()
    # Preserve payment records — null out user_id so history survives
    db.execute(text("UPDATE payment_history SET user_id = NULL WHERE user_id = :uid"), {"uid": user_id})
    db.delete(user)
    db.commit()
    log_activity(db, role, x_admin_token, "delete_user", f"user_id={user_id}")
    return {"status": "success"}


# ─── PAYMENT HISTORY ─────────────────────────────────────────────────────────
@router.get("/admin/payments")
def list_payments(role: str = Depends(get_admin_role), db: Session = Depends(get_db)):
    try:
        rows = db.execute(text(
            "SELECT id, user_id, user_name, amount_pkr, method, plan, receipt_url, notes, created_at FROM payment_history ORDER BY created_at DESC"
        )).fetchall()
        return [dict(zip(["id","user_id","user_name","amount_pkr","method","plan","receipt_url","notes","created_at"], r)) for r in rows]
    except Exception as e:
        logger.warning(f"Could not fetch payments: {e}")
        return []


@router.post("/admin/payments")
async def add_payment(
    user_id: int = Form(...),
    user_name: str = Form(...),
    amount_pkr: int = Form(...),
    method: str = Form(...),
    plan: str = Form(...),
    notes: str = Form(""),
    receipt: UploadFile = File(None),
    x_admin_token: str = Header(...),
    role: str = Depends(get_admin_role),
    db: Session = Depends(get_db)
):
    receipt_url = None
    if receipt and receipt.filename:
        try:
            from minio import Minio
            client = Minio(
                settings.MINIO_ENDPOINT,
                access_key=settings.MINIO_ACCESS_KEY,
                secret_key=settings.MINIO_SECRET_KEY,
                secure=False
            )
            content = await receipt.read()
            ext = receipt.filename.split(".")[-1]
            obj_name = f"receipts/{uuid.uuid4()}.{ext}"
            client.put_object(
                settings.MINIO_BUCKET,
                obj_name,
                io.BytesIO(content),
                length=len(content),
                content_type=receipt.content_type
            )
            receipt_url = f"http://{settings.MINIO_ENDPOINT}/{settings.MINIO_BUCKET}/{obj_name}"
        except Exception as e:
            logger.warning(f"Receipt upload failed: {e}")
            receipt_url = None

    try:
        db.execute(text("""
            INSERT INTO payment_history (user_id, user_name, amount_pkr, method, plan, receipt_url, notes, created_at)
            VALUES (:uid, :uname, :amt, :method, :plan, :url, :notes, :ts)
        """), {
            "uid": user_id, "uname": user_name, "amt": amount_pkr,
            "method": method, "plan": plan, "url": receipt_url,
            "notes": notes, "ts": datetime.now(timezone.utc)
        })
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    log_activity(db, role, x_admin_token, "add_payment", f"user_id={user_id} amount={amount_pkr} method={method}")
    return {"status": "success", "receipt_url": receipt_url}


@router.delete("/admin/payments/{payment_id}")
def delete_payment(payment_id: int, role: str = Depends(require_owner), db: Session = Depends(get_db)):
    db.execute(text("DELETE FROM payment_history WHERE id = :id"), {"id": payment_id})
    db.commit()
    return {"status": "success"}


# ─── ACTIVITY LOG (owner only) ───────────────────────────────────────────────
@router.get("/admin/activity")
def get_activity(role: str = Depends(require_owner), db: Session = Depends(get_db)):
    try:
        rows = db.execute(text(
            "SELECT id, role, token_hint, action, detail, created_at FROM admin_activity_log ORDER BY created_at DESC LIMIT 200"
        )).fetchall()
        return [dict(zip(["id","role","token_hint","action","detail","created_at"], r)) for r in rows]
    except Exception as e:
        logger.warning(f"Could not fetch activity log: {e}")
        return []


# ─── ANONYMOUS IP STATS ──────────────────────────────────────────────────────
@router.get("/admin/ip-stats")
def get_ip_stats(role: str = Depends(get_admin_role), db: Session = Depends(get_db)):
    stats = get_all_ip_stats()
    if not stats:
        return stats
    # Enrich with token usage from DB (best-effort — first/last seen come from Redis)
    ips = [s["ip"] for s in stats]
    try:
        rows = db.execute(text("""
            SELECT client_ip, COALESCE(SUM(tokens_used), 0) AS total_tokens
            FROM query_history
            WHERE client_ip = ANY(:ips)
            GROUP BY client_ip
        """), {"ips": ips}).fetchall()
        db_tokens = {row[0]: int(row[1] or 0) for row in rows}
    except Exception:
        db_tokens = {}
    for s in stats:
        tokens = db_tokens.get(s["ip"], 0)
        s["tokens_used"] = tokens
        s["api_cost_usd"] = round((tokens / 1_000_000) * 0.50, 6)
    return stats


@router.post("/admin/ip-block/{ip}")
def block_ip_endpoint(ip: str, x_admin_token: str = Header(...), role: str = Depends(get_admin_role), db: Session = Depends(get_db)):
    block_ip(ip)
    log_activity(db, role, x_admin_token, "block_ip", f"ip={ip}")
    return {"status": "blocked", "ip": ip}


@router.delete("/admin/ip-block/{ip}")
def unblock_ip_endpoint(ip: str, x_admin_token: str = Header(...), role: str = Depends(get_admin_role), db: Session = Depends(get_db)):
    unblock_ip(ip)
    log_activity(db, role, x_admin_token, "unblock_ip", f"ip={ip}")
    return {"status": "unblocked", "ip": ip}


@router.get("/admin/ip-whitelist")
def list_whitelist(role: str = Depends(get_admin_role)):
    return {"whitelist": get_whitelist()}


@router.post("/admin/ip-whitelist/{ip:path}")
def add_whitelist(ip: str, x_admin_token: str = Header(...), role: str = Depends(get_admin_role), db: Session = Depends(get_db)):
    whitelist_ip(ip)
    log_activity(db, role, x_admin_token, "whitelist_ip", f"ip={ip}")
    return {"status": "whitelisted", "ip": ip}


@router.delete("/admin/ip-whitelist/{ip:path}")
def remove_whitelist(ip: str, x_admin_token: str = Header(...), role: str = Depends(get_admin_role), db: Session = Depends(get_db)):
    unwhitelist_ip(ip)
    log_activity(db, role, x_admin_token, "unwhitelist_ip", f"ip={ip}")
    return {"status": "removed", "ip": ip}


# ─── PASSWORD MANAGEMENT (owner only) ────────────────────────────────────────
@router.get("/admin/passwords")
def list_passwords(role: str = Depends(require_owner)):
    return get_all_admin_passwords()


@router.post("/admin/passwords")
def create_password_endpoint(
    password: str,
    x_admin_token: str = Header(...),
    role: str = Depends(require_owner),
    db: Session = Depends(get_db),
):
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password too short (min 6 chars)")
    assigned_role = create_admin_password(password)
    log_activity(db, role, x_admin_token, "create_password", f"role={assigned_role} hint=...{password[-4:]}")
    return {"status": "created", "role": assigned_role, "password": password}


@router.delete("/admin/passwords/{password}")
def delete_password_endpoint(
    password: str,
    x_admin_token: str = Header(...),
    role: str = Depends(require_owner),
    db: Session = Depends(get_db),
):
    delete_admin_password(password)
    log_activity(db, role, x_admin_token, "delete_password", f"hint=...{password[-4:]}")
    return {"status": "deleted"}


# ─── SESSION TRACKING ────────────────────────────────────────────────────────
class SessionEventBody(BaseModel):
    session_id: str
    event_type: str
    detail: dict = {}


@router.post("/admin/session-event")
async def record_session_event(
    request: Request,
    body: SessionEventBody,
    x_admin_token: str = Header(...),
    role: str = Depends(get_admin_role),
):
    if body.event_type == "session_login":
        from app.api.query import get_client_ip
        ip = get_client_ip(request)
        hint = x_admin_token[-6:] if len(x_admin_token) > 6 else x_admin_token
        session_create(body.session_id, role, hint, ip)
    else:
        session_event(body.session_id, body.event_type, body.detail)
    return {"status": "ok"}


@router.get("/admin/session-stats")
def get_session_stats(role: str = Depends(require_owner)):
    return sessions_all()


# ─── PIPELINE ────────────────────────────────────────────────────────────────
@router.get("/admin/check-new-data")
def check_new_data(role: str = Depends(get_admin_role), db: Session = Depends(get_db)):
    """
    HTTP-only harvest to detect new FBR documents (no Selenium, ~15-25s).
    Returns how many new URLs were found per category without downloading anything.
    """
    try:
        from app.scrapers.harvester import harvest_quick_check
        links = harvest_quick_check()

        # Bulk-fetch all existing URLs in one query for speed
        existing_urls = set(
            row[0] for row in db.query(FBRDocument.url).all()
        )

        new_links = [l for l in links if l["url"] not in existing_urls]

        by_category: dict = {}
        for link in new_links:
            cat = link.get("category", "unknown")
            by_category[cat] = by_category.get(cat, 0) + 1

        return {
            "total_new": len(new_links),
            "total_checked": len(links),
            "by_category": by_category,
        }
    except Exception as e:
        logger.error(f"check-new-data failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/admin/pipeline-stats")
def pipeline_stats(role: str = Depends(get_admin_role), db: Session = Depends(get_db)):
    """Document counts by category and status from PostgreSQL, plus Qdrant vector count."""
    # ── PostgreSQL counts ────────────────────────────────────────────────────
    by_cat: dict = {}
    totals: dict = {"completed": 0, "pending": 0, "processing": 0, "failed": 0}
    total_docs = 0
    try:
        rows = db.execute(text(
            "SELECT category, status, COUNT(*) FROM fbr_documents "
            "GROUP BY category, status ORDER BY category, status"
        )).fetchall()
        for cat, status, count in rows:
            by_cat.setdefault(cat, {})[status] = count
            totals[status] = totals.get(status, 0) + count
        total_docs = db.execute(text("SELECT COUNT(*) FROM fbr_documents")).scalar() or 0
    except Exception as e:
        logger.warning(f"pipeline-stats DB query failed: {e}")

    # ── Qdrant vector count ──────────────────────────────────────────────────
    qdrant_points = None
    qdrant_error = None
    try:
        from qdrant_client import QdrantClient
        qc = QdrantClient(host=settings.QDRANT_HOST, port=settings.QDRANT_PORT)
        result = qc.count(collection_name=settings.QDRANT_COLLECTION)
        qdrant_points = result.count
    except Exception as e:
        qdrant_error = str(e)

    return {
        "total_documents": total_docs,
        "completed": totals.get("completed", 0),
        "pending": totals.get("pending", 0),
        "processing": totals.get("processing", 0),
        "failed": totals.get("failed", 0),
        "qdrant_vectors": qdrant_points,
        "qdrant_error": qdrant_error,
        "by_category": by_cat,
    }


@router.post("/admin/run-pipeline")
def trigger_pipeline(
    x_admin_token: str = Header(...),
    role: str = Depends(require_owner),
    db: Session = Depends(get_db),
):
    """
    Trigger a pipeline run: harvest new links (HTTP-only), download PDFs, then
    push a processing trigger to the scheduler via Redis for the heavy OCR/embed step.
    """
    import threading

    def _run():
        try:
            from app.scrapers.harvester import harvest_quick_check
            from app.scrapers.saver import save_links
            from app.scrapers.downloader import run_downloader
            logger.info("Pipeline: harvesting new links (HTTP-only)…")
            links = harvest_quick_check()
            logger.info(f"Pipeline: found {len(links)} links, saving…")
            save_links(links)
            logger.info("Pipeline: downloading new PDFs…")
            dl = run_downloader(batch_size=200)
            logger.info(f"Pipeline: download done {dl}. Queuing embedding on scheduler…")
            # Kick off embedding on the scheduler container (has more memory/CPU for OCR)
            try:
                import redis as _redis
                r = _redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
                r.set("pipeline:run:trigger", "1", ex=3600)
            except Exception as redis_e:
                logger.warning(f"Could not queue embedding on scheduler: {redis_e}")
                # Fallback: run locally
                from app.scrapers.processor import run_processor
                run_processor(batch_size=100)
            logger.info("Pipeline: harvest+download complete, embedding queued.")
        except Exception as e:
            logger.error(f"Background pipeline failed: {e}", exc_info=True)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    log_activity(db, role, x_admin_token, "run_pipeline", "triggered via admin panel")
    return {
        "status": "started",
        "message": "Pipeline started: harvesting new links + downloading PDFs. Embedding will run on scheduler. Check pipeline stats in ~10 minutes.",
    }



@router.post("/admin/upload-document")
async def upload_document(
    x_admin_token: str = Header(...),
    role: str = Depends(require_owner),
    file: UploadFile = File(...),
    title: str = Form(...),
    category: str = Form(...),
    doc_date: str = Form(""),
    circular_number: str = Form(""),
    fiscal_year: str = Form(""),
    db: Session = Depends(get_db),
):
    """
    Manually upload a PDF into the pipeline. Saves to MinIO and queues embedding.
    Useful for case law, Finance Acts, or any PDF not available via FBR's website.
    """
    from app.models.document import DocumentStatus

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="File must be a PDF")

    title = title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title is required")

    category = category.strip()
    if not category:
        raise HTTPException(status_code=400, detail="Category is required")

    content = await file.read()
    if len(content) < 100:
        raise HTTPException(status_code=400, detail="File too small — check the PDF is valid")

    file_uuid = str(uuid.uuid4())
    minio_path = f"documents/manual/{file_uuid}.pdf"
    synthetic_url = f"manual://{file_uuid}"

    try:
        from minio import Minio
        client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=False,
        )
        if not client.bucket_exists(settings.MINIO_BUCKET):
            client.make_bucket(settings.MINIO_BUCKET)
        client.put_object(
            settings.MINIO_BUCKET,
            minio_path,
            io.BytesIO(content),
            length=len(content),
            content_type="application/pdf",
        )
    except Exception as e:
        logger.error(f"MinIO upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"Storage upload failed: {e}")

    try:
        doc = FBRDocument(
            title=title,
            url=synthetic_url,
            category=category,
            minio_path=minio_path,
            status=DocumentStatus.processing.value,
            doc_date=doc_date.strip() or None,
            circular_number=circular_number.strip() or None,
            fiscal_year=fiscal_year.strip() or None,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        doc_id = doc.id
    except Exception as e:
        logger.error(f"DB insert failed: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Database insert failed: {e}")

    import threading
    def _process():
        try:
            from app.scrapers.processor import run_processor
            run_processor(batch_size=5, status_filter="processing")
        except Exception as proc_e:
            logger.error(f"Background processor failed for manual upload: {proc_e}")

    threading.Thread(target=_process, daemon=True).start()

    log_activity(db, role, x_admin_token, "upload_document",
                 f"title={title[:50]} category={category} doc_id={doc_id}")

    return {
        "status": "ok",
        "doc_id": doc_id,
        "minio_path": minio_path,
        "message": f"PDF uploaded and queued for embedding. Doc ID: {doc_id}",
    }



# ─── BACKUP ──────────────────────────────────────────────────────────────────
BACKUP_SERVICE = "http://fbrbot_backup:8099"

@router.post("/admin/backup")
def trigger_backup(
    x_admin_token: str = Header(...),
    role: str = Depends(get_admin_role),
    db: Session = Depends(get_db),
):
    import httpx as _httpx
    try:
        r = _httpx.post(f"{BACKUP_SERVICE}/run", timeout=360)
        result = r.json()
        log_activity(db, role, x_admin_token, "backup", f"status={result.get('status')}")
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backup service unreachable: {e}")


@router.get("/admin/backup-status")
def get_backup_status(role: str = Depends(get_admin_role)):
    import httpx as _httpx
    try:
        r = _httpx.get(f"{BACKUP_SERVICE}/status", timeout=15)
        return r.json()
    except Exception as e:
        return {"status": "unavailable", "error": str(e)}


# ─── DB INIT ─────────────────────────────────────────────────────────────────
@router.post("/admin/init-db")
def init_admin_db(role: str = Depends(require_owner), db: Session = Depends(get_db)):
    try:
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS payment_history (
                id SERIAL PRIMARY KEY,
                user_id INTEGER,
                user_name VARCHAR(200),
                amount_pkr INTEGER,
                method VARCHAR(50),
                plan VARCHAR(50),
                receipt_url TEXT,
                notes TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """))
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS admin_activity_log (
                id SERIAL PRIMARY KEY,
                role VARCHAR(50),
                token_hint VARCHAR(20),
                action VARCHAR(100),
                detail TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """))
        db.execute(text("""
            ALTER TABLE query_history ADD COLUMN IF NOT EXISTS client_ip VARCHAR(50)
        """))
        db.commit()
        return {"status": "success", "message": "DB initialized"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))