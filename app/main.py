from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.query import router as query_router
from app.api.auth import router as auth_router
from app.api.history import router as history_router
from app.api.admin import router as admin_router
from app.api.firm_api import router as firm_router
from app.api.feedback import router as feedback_router
from app.api.messages import router as messages_router
from app.api.collab import router as collab_router

app = FastAPI(
    title="AskTax.pk API",
    description="AI-powered Pakistan Tax Assistant",
    version="2.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(query_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(history_router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(firm_router, prefix="/api")
app.include_router(feedback_router, prefix="/api")
app.include_router(messages_router, prefix="/api")
app.include_router(collab_router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "AskTax.pk", "version": "2.0.0"}


@app.on_event("startup")
def startup_event():
    from app.db.session import engine
    from sqlalchemy import text
    migrations = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS status VARCHAR DEFAULT 'active'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS firm_id INTEGER REFERENCES firms(id)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR DEFAULT 'individual'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS invite_token VARCHAR UNIQUE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS invite_accepted BOOLEAN DEFAULT TRUE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS invited_by_id INTEGER",
        "ALTER TABLE query_history ADD COLUMN IF NOT EXISTS client_ip VARCHAR(50)",
        "ALTER TABLE query_history ADD COLUMN IF NOT EXISTS tokens_used INTEGER DEFAULT 0",
        "ALTER TABLE query_history ADD COLUMN IF NOT EXISTS title VARCHAR(200)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url TEXT",
        "ALTER TABLE query_history ADD COLUMN IF NOT EXISTS confidence_score FLOAT",
        "ALTER TABLE query_history ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE query_history ADD COLUMN IF NOT EXISTS share_token VARCHAR(64) UNIQUE",
        "ALTER TABLE query_history ADD COLUMN IF NOT EXISTS session_id VARCHAR(64)",
        "CREATE INDEX IF NOT EXISTS idx_qh_session ON query_history (session_id)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_expires_at TIMESTAMP WITH TIME ZONE",
    ]
    with engine.connect() as conn:
        # Create firms table first
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS firms (
                id SERIAL PRIMARY KEY,
                name VARCHAR(200) NOT NULL,
                slug VARCHAR(100) UNIQUE NOT NULL,
                billing_email VARCHAR(200),
                phone VARCHAR(50),
                plan VARCHAR(50) DEFAULT 'trial',
                status VARCHAR(50) DEFAULT 'trial',
                seats_limit INTEGER DEFAULT 5,
                queries_per_seat INTEGER DEFAULT 200,
                trial_ends_at TIMESTAMP WITH TIME ZONE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE
            )
        """))
        conn.commit()
        for sql in migrations:
            try:
                conn.execute(text(sql))
                conn.commit()
            except Exception:
                conn.rollback()

        # ── FBR documents table ────────────────────────────────────────────────
        # Create it if it doesn't exist (fresh installs)
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS fbr_documents (
                id SERIAL PRIMARY KEY,
                title VARCHAR NOT NULL,
                url VARCHAR UNIQUE NOT NULL,
                category VARCHAR NOT NULL,
                circular_number VARCHAR,
                fiscal_year VARCHAR,
                minio_path VARCHAR,
                checksum VARCHAR,
                status VARCHAR DEFAULT 'pending',
                chunk_count INTEGER DEFAULT 0,
                raw_text TEXT,
                doc_date VARCHAR,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE
            )
        """))
        conn.commit()

        # Indexes for common query patterns (safe to run multiple times)
        for sql in [
            "CREATE INDEX IF NOT EXISTS idx_fbr_status ON fbr_documents (status)",
            "CREATE INDEX IF NOT EXISTS idx_fbr_category ON fbr_documents (category)",
            "CREATE INDEX IF NOT EXISTS idx_fbr_fiscal_year ON fbr_documents (fiscal_year)",
        ]:
            try:
                conn.execute(text(sql))
                conn.commit()
            except Exception:
                conn.rollback()

        # ── Restore requests table ─────────────────────────────────────────────
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS restore_requests (
                id SERIAL PRIMARY KEY,
                user_id INTEGER,
                user_name VARCHAR(200),
                user_email VARCHAR(200),
                chat_id INTEGER,
                chat_title VARCHAR(200),
                context TEXT,
                status VARCHAR(20) DEFAULT 'pending',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                UNIQUE (user_id, chat_id)
            )
        """))
        conn.commit()

        # ── Messaging system ───────────────────────────────────────────────────
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                from_admin BOOLEAN DEFAULT FALSE,
                body TEXT NOT NULL,
                read_at TIMESTAMP WITH TIME ZONE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_messages_user ON messages (user_id, created_at)"
        ))
        conn.commit()

        # ── Shared conversations (multi-message share bundles) ─────────────────
        try:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS shared_conversations (
                    id SERIAL PRIMARY KEY,
                    share_token VARCHAR(64) UNIQUE NOT NULL,
                    user_id INTEGER NOT NULL,
                    chat_ids TEXT NOT NULL,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """))
            conn.commit()
        except Exception:
            conn.rollback()
        try:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_shared_conv_token ON shared_conversations (share_token)"
            ))
            conn.commit()
        except Exception:
            conn.rollback()

        # ── Collab sessions ────────────────────────────────────────────────────
        try:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS collab_sessions (
                    id SERIAL PRIMARY KEY,
                    session_id VARCHAR(64) NOT NULL,
                    host_user_id INTEGER NOT NULL REFERENCES users(id),
                    guest_user_id INTEGER NOT NULL REFERENCES users(id),
                    status VARCHAR(20) DEFAULT 'pending',
                    turn_user_id INTEGER REFERENCES users(id),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    ended_at TIMESTAMP WITH TIME ZONE
                )
            """))
            conn.commit()
        except Exception:
            conn.rollback()
        try:
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_collab_session ON collab_sessions (session_id)"))
            conn.commit()
        except Exception:
            conn.rollback()

        # ── Trial / subscription requests ──────────────────────────────────────
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS trial_requests (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL UNIQUE,
                user_name VARCHAR(200),
                user_email VARCHAR(200),
                plan VARCHAR(50) NOT NULL DEFAULT 'basic',
                billing_cycle VARCHAR(20) DEFAULT 'monthly',
                status VARCHAR(20) DEFAULT 'pending',
                admin_note TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE
            )
        """))
        conn.commit()

