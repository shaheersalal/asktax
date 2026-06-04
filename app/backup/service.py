"""
Backup runner — internal service on port 8099.
Triggered by the main API. Never exposed publicly.
"""
import json
import os
import subprocess
from datetime import datetime, timezone

import httpx
import redis as redis_lib
from fastapi import FastAPI
from minio import Minio

app = FastAPI()

REDIS_URL     = os.getenv("REDIS_URL", "redis://redis:6379")
PG_HOST       = "postgres"
PG_USER       = "fbrbot"
PG_DB         = "fbrbot_db"
PG_PASS       = os.getenv("POSTGRES_PASSWORD", "fbrbot_secure_pass")
QDRANT_URL    = "http://qdrant:6333"
QDRANT_COL    = os.getenv("QDRANT_COLLECTION", "fbr_docs")
MINIO_EP      = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_USER    = os.getenv("MINIO_ACCESS_KEY", "fbrbot_minio")
MINIO_PASS    = os.getenv("MINIO_SECRET_KEY", "fbrbot_minio_pass")
MINIO_BUCKET  = os.getenv("MINIO_BUCKET", "fbr-documents")
BACKUP_DIR    = "/backups/latest"
REDIS_KEY     = "backup:last"

rc = redis_lib.Redis.from_url(REDIS_URL, decode_responses=True)


def _pg_dump() -> dict:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    dest = f"{BACKUP_DIR}/fbrbot_db.dump"
    env = {**os.environ, "PGPASSWORD": PG_PASS}
    result = subprocess.run(
        ["pg_dump", "-h", PG_HOST, "-U", PG_USER, "-d", PG_DB, "-Fc", "-f", dest],
        env=env, capture_output=True, timeout=300,
    )
    if result.returncode != 0:
        return {"status": "failed", "error": result.stderr.decode()}
    size = os.path.getsize(dest)
    return {"status": "ok", "size_bytes": size, "file": dest}


def _qdrant_snapshot() -> dict:
    try:
        # Create a new snapshot
        r = httpx.post(f"{QDRANT_URL}/collections/{QDRANT_COL}/snapshots", timeout=120)
        if r.status_code not in (200, 201):
            return {"status": "failed", "error": r.text}
        name = r.json().get("result", {}).get("name", "unknown")

        # Count how many snapshots exist
        r2 = httpx.get(f"{QDRANT_URL}/collections/{QDRANT_COL}/snapshots", timeout=30)
        snapshots = r2.json().get("result", []) if r2.status_code == 200 else []

        # Keep only the 3 most recent snapshots — delete older ones
        if len(snapshots) > 3:
            for old in snapshots[:-3]:
                httpx.delete(
                    f"{QDRANT_URL}/collections/{QDRANT_COL}/snapshots/{old['name']}",
                    timeout=30,
                )
        return {"status": "ok", "snapshot": name, "total_snapshots": min(len(snapshots), 3)}
    except Exception as e:
        return {"status": "failed", "error": str(e)}


def _minio_mirror() -> dict:
    try:
        client = Minio(MINIO_EP, access_key=MINIO_USER, secret_key=MINIO_PASS, secure=False)
        if not client.bucket_exists(MINIO_BUCKET):
            return {"status": "ok", "files": 0, "note": "bucket empty"}
        objects = list(client.list_objects(MINIO_BUCKET, recursive=True))
        # Save object list as manifest (PDFs can be re-downloaded from FBR if needed)
        manifest = [{"name": o.object_name, "size": o.size, "etag": o.etag} for o in objects]
        os.makedirs(BACKUP_DIR, exist_ok=True)
        with open(f"{BACKUP_DIR}/minio_manifest.json", "w") as f:
            json.dump(manifest, f)
        return {"status": "ok", "files": len(manifest)}
    except Exception as e:
        return {"status": "failed", "error": str(e)}


def _get_live_stats() -> dict:
    stats = {}
    try:
        r = httpx.get(f"{QDRANT_URL}/collections/{QDRANT_COL}", timeout=10)
        stats["qdrant_vectors"] = r.json().get("result", {}).get("points_count", 0)
    except Exception:
        stats["qdrant_vectors"] = None
    try:
        import psycopg2
        conn = psycopg2.connect(host=PG_HOST, user=PG_USER, password=PG_PASS, dbname=PG_DB)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM fbr_documents")
        stats["fbr_documents"] = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM users")
        stats["users"] = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM query_history")
        stats["queries"] = cur.fetchone()[0]
        conn.close()
    except Exception:
        pass
    return stats


@app.post("/run")
def run_backup():
    started = datetime.now(timezone.utc).isoformat()
    live_stats = _get_live_stats()

    pg     = _pg_dump()
    qdrant = _qdrant_snapshot()
    minio  = _minio_mirror()

    all_ok = all(s.get("status") == "ok" for s in [pg, qdrant, minio])
    result = {
        "status": "ok" if all_ok else "partial",
        "timestamp": started,
        "live_stats_at_backup": live_stats,
        "steps": {"postgres": pg, "qdrant": qdrant, "minio": minio},
    }
    rc.set(REDIS_KEY, json.dumps(result))
    return result


@app.get("/status")
def backup_status():
    raw = rc.get(REDIS_KEY)
    if not raw:
        return {"status": "never_run"}
    last = json.loads(raw)

    # Calculate diff: what's changed since last backup
    live = _get_live_stats()
    saved = last.get("live_stats_at_backup", {})
    diff = {}
    for key in live:
        if live[key] is not None and saved.get(key) is not None:
            diff[key] = live[key] - saved[key]

    return {**last, "current_stats": live, "new_since_backup": diff}
