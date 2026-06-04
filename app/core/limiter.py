import json
import time as _time
from datetime import datetime, timezone
import redis
from app.core.config import get_settings

settings = get_settings()
redis_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)

FREE_TIER_LIMIT = 20
REGISTERED_FREE_LIMIT = 50

BLOCKED_KEY = "ip_blocked"       # Redis set of blocked IPs
WHITELIST_KEY = "ip_whitelist"   # Redis set of whitelisted IPs/prefixes
PASSWORDS_HASH = "admin_passwords"  # Redis hash: password → role
SESSION_LIST = "admin_sessions"   # Redis sorted set of session IDs by login time


# ─── IP WHITELIST ─────────────────────────────────────────────────────────────

def _load_env_whitelist() -> list[str]:
    """Return whitelist entries from WHITELISTED_IPS env var (comma-separated)."""
    raw = settings.WHITELISTED_IPS or ""
    return [e.strip() for e in raw.split(",") if e.strip()]


def _ip_matches_entry(ip: str, entry: str) -> bool:
    """Match exact IP or prefix (entry ending with '.' matches entire /24)."""
    return ip == entry or ip.startswith(entry)


def is_ip_whitelisted(ip: str) -> bool:
    """Check env whitelist and Redis runtime whitelist."""
    # Env-based (supports prefix matching)
    for entry in _load_env_whitelist():
        if _ip_matches_entry(ip, entry):
            return True
    # Redis runtime whitelist (exact IPs or prefixes added via admin)
    for entry in redis_client.smembers(WHITELIST_KEY):
        if _ip_matches_entry(ip, entry):
            return True
    return False


def whitelist_ip(ip: str) -> None:
    redis_client.sadd(WHITELIST_KEY, ip)


def unwhitelist_ip(ip: str) -> None:
    redis_client.srem(WHITELIST_KEY, ip)


def get_whitelist() -> list:
    return sorted(redis_client.smembers(WHITELIST_KEY))


# ─── IP RATE LIMITING ────────────────────────────────────────────────────────

def get_ip_query_count(ip: str) -> int:
    count = redis_client.get(f"ip_queries:{ip}")
    return int(count) if count else 0


def increment_ip_query_count(ip: str) -> int:
    key = f"ip_queries:{ip}"
    now = str(_time.time())
    ttl = 60 * 60 * 24 * 30  # 30 days
    pipe = redis_client.pipeline()
    pipe.incr(key)
    pipe.expire(key, ttl)
    pipe.setnx(f"ip_first_seen:{ip}", now)   # only sets if not already present
    pipe.expire(f"ip_first_seen:{ip}", ttl)
    pipe.set(f"ip_last_seen:{ip}", now)       # always overwrite
    pipe.expire(f"ip_last_seen:{ip}", ttl)
    return pipe.execute()[0]


def is_ip_blocked(ip: str) -> bool:
    return redis_client.sismember(BLOCKED_KEY, ip)


def is_ip_allowed(ip: str) -> bool:
    if is_ip_whitelisted(ip):
        return True
    if is_ip_blocked(ip):
        return False
    return get_ip_query_count(ip) < FREE_TIER_LIMIT


def get_ip_queries_remaining(ip: str) -> int:
    return max(0, FREE_TIER_LIMIT - get_ip_query_count(ip))


def block_ip(ip: str) -> None:
    redis_client.sadd(BLOCKED_KEY, ip)


def unblock_ip(ip: str) -> None:
    redis_client.srem(BLOCKED_KEY, ip)


def _ts_to_iso(ts_str) -> str | None:
    if not ts_str:
        return None
    try:
        return datetime.fromtimestamp(float(ts_str), tz=timezone.utc).isoformat()
    except Exception:
        return None


def get_all_ip_stats() -> list:
    blocked = redis_client.smembers(BLOCKED_KEY)
    stats = []
    for key in redis_client.keys("ip_queries:*"):
        ip = key.split("ip_queries:", 1)[1]
        count = int(redis_client.get(key) or 0)
        first_seen = _ts_to_iso(redis_client.get(f"ip_first_seen:{ip}"))
        last_seen  = _ts_to_iso(redis_client.get(f"ip_last_seen:{ip}"))
        stats.append({
            "ip": ip,
            "queries": count,
            "blocked": ip in blocked,
            "first_seen": first_seen,
            "last_seen": last_seen,
        })
    stats.sort(key=lambda x: x["queries"], reverse=True)
    return stats


# ─── ADMIN PASSWORD MANAGEMENT ───────────────────────────────────────────────

def create_admin_password(password: str) -> str:
    """First 3 chars determine role: 'own' → owner, anything else → assistant."""
    role = "owner" if password[:3].lower() == "own" else "assistant"
    redis_client.hset(PASSWORDS_HASH, password, role)
    return role


def get_dynamic_role(password: str):
    return redis_client.hget(PASSWORDS_HASH, password)


def get_all_admin_passwords() -> list:
    return [{"password": k, "role": v} for k, v in redis_client.hgetall(PASSWORDS_HASH).items()]


def delete_admin_password(password: str) -> None:
    redis_client.hdel(PASSWORDS_HASH, password)


# ─── ADMIN SESSION TRACKING ──────────────────────────────────────────────────

def session_create(session_id: str, role: str, token_hint: str, ip: str) -> None:
    data = {
        "role": role, "token_hint": token_hint, "ip": ip,
        "login_at": _time.time(), "last_active": _time.time(),
        "heartbeats": 0, "idle_secs": 0.0, "hidden_secs": 0.0,
        "clicks": [], "idle_start": None, "hidden_start": None,
    }
    redis_client.setex(f"admin_session:{session_id}", 7 * 86400, json.dumps(data))
    redis_client.zadd(SESSION_LIST, {session_id: _time.time()})
    redis_client.expire(SESSION_LIST, 7 * 86400)


def session_event(session_id: str, event_type: str, detail: dict) -> None:
    key = f"admin_session:{session_id}"
    raw = redis_client.get(key)
    if not raw:
        return
    d = json.loads(raw)
    now = _time.time()
    d["last_active"] = now

    if event_type == "heartbeat":
        d["heartbeats"] += 1
    elif event_type == "click":
        d["clicks"].append(detail.get("element", "?"))
        if len(d["clicks"]) > 200:
            d["clicks"] = d["clicks"][-200:]
    elif event_type == "idle_start":
        d["idle_start"] = now
    elif event_type == "idle_end":
        if d.get("idle_start"):
            d["idle_secs"] += now - d["idle_start"]
            d["idle_start"] = None
    elif event_type == "tab_hide":
        d["hidden_start"] = now
    elif event_type == "tab_show":
        if d.get("hidden_start"):
            d["hidden_secs"] += now - d["hidden_start"]
            d["hidden_start"] = None

    redis_client.setex(key, 7 * 86400, json.dumps(d))


def sessions_all() -> list:
    ids = redis_client.zrevrange(SESSION_LIST, 0, 199)
    out = []
    for sid in ids:
        raw = redis_client.get(f"admin_session:{sid}")
        if not raw:
            continue
        d = json.loads(raw)
        duration = d["last_active"] - d["login_at"]
        active = max(0.0, duration - d.get("idle_secs", 0) - d.get("hidden_secs", 0))
        out.append({
            "session_id": sid,
            "role": d["role"],
            "token_hint": d["token_hint"],
            "ip": d["ip"],
            "login_at": d["login_at"],
            "last_active": d["last_active"],
            "duration_secs": round(duration),
            "active_secs": round(active),
            "idle_secs": round(d.get("idle_secs", 0)),
            "hidden_secs": round(d.get("hidden_secs", 0)),
            "click_count": len(d.get("clicks", [])),
            "clicks": d.get("clicks", [])[-30:],
        })
    return out
