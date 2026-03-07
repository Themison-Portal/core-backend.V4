"""
Themison Upload Debugger MCP Server

Diagnoses 500 errors on POST /api/trial-documents/upload by checking
each layer of the request pipeline: config, database, storage, auth,
and performing a full end-to-end test upload.

Usage:
    python mcp_debug_upload.py

Register with Claude Code:
    claude mcp add --transport stdio --scope project upload-debugger -- python mcp_debug_upload.py
"""

import json
import os
import io
import traceback
from pathlib import Path

import httpx
import psycopg2
import psycopg2.extras
from dotenv import dotenv_values
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Resolve .env from the same directory as this script
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
ENV_PATH = SCRIPT_DIR / ".env"
_env = dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}

DATABASE_URL = os.environ.get(
    "MCP_DATABASE_URL",
    _env.get(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:54322/postgres",
    ),
)
# psycopg2 needs a plain postgresql:// URL
SYNC_DB_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")

BASE_URL = os.environ.get("MCP_BASE_URL", "http://localhost:8000")

mcp = FastMCP("upload-debugger")


def _connect():
    return psycopg2.connect(SYNC_DB_URL)


# ---------------------------------------------------------------------------
# Tool 1: check_config
# ---------------------------------------------------------------------------
@mcp.tool()
def check_config() -> str:
    """Check .env configuration relevant to the upload endpoint.

    Validates: DATABASE_URL, REDIS_URL, GCS settings, AUTH_DISABLED,
    OPENAI_API_KEY, ANTHROPIC_API_KEY, UPLOAD_API_KEY.
    """
    lines = ["=== Upload Config Check ===", ""]

    if not ENV_PATH.exists():
        lines.append(f"WARNING: .env file not found at {ENV_PATH}")
        return "\n".join(lines)

    env = dotenv_values(ENV_PATH)

    checks = [
        ("DATABASE_URL", True),
        ("REDIS_URL", True),
        ("OPENAI_API_KEY", True),
        ("ANTHROPIC_API_KEY", True),
        ("UPLOAD_API_KEY", False),
        ("AUTH_DISABLED", False),
        ("AUTH0_DOMAIN", False),
        ("AUTH0_AUDIENCE", False),
        ("GCS_BUCKET_TRIAL_DOCUMENTS", False),
        ("GCS_CREDENTIALS_PATH", False),
        ("GCS_PROJECT_ID", False),
        ("FRONTEND_URL", False),
        ("ALLOW_ALL_ORIGINS", False),
    ]

    for key, required in checks:
        val = env.get(key, "")
        if val:
            # Mask secrets
            if "KEY" in key or "SECRET" in key or "PASSWORD" in key:
                display = val[:8] + "..." if len(val) > 8 else "***"
            else:
                display = val
            lines.append(f"  OK   {key} = {display}")
        elif required:
            lines.append(f"  FAIL {key} — MISSING (required)")
        else:
            lines.append(f"  SKIP {key} — not set (optional)")

    # Storage mode inference
    gcs_bucket = env.get("GCS_BUCKET_TRIAL_DOCUMENTS", "")
    if gcs_bucket:
        lines.append("")
        lines.append(f"Storage mode: GCS (bucket={gcs_bucket})")
        creds = env.get("GCS_CREDENTIALS_PATH", "")
        if creds and not Path(creds).exists():
            lines.append(f"  FAIL GCS credentials file not found: {creds}")
    else:
        lines.append("")
        lines.append("Storage mode: LOCAL FILESYSTEM (./uploads/)")
        uploads_dir = SCRIPT_DIR / "uploads"
        if uploads_dir.exists():
            lines.append(f"  OK   uploads/ directory exists")
        else:
            lines.append(f"  INFO uploads/ will be created on first upload")

    # Auth mode
    auth_disabled = env.get("AUTH_DISABLED", "").lower() == "true"
    lines.append("")
    if auth_disabled:
        lines.append("Auth mode: DISABLED (mock user)")
    else:
        domain = env.get("AUTH0_DOMAIN", "")
        if domain:
            lines.append(f"Auth mode: Auth0 (domain={domain})")
        else:
            lines.append("Auth mode: Auth0 but AUTH0_DOMAIN not set — may fail")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 2: check_database
# ---------------------------------------------------------------------------
@mcp.tool()
def check_database() -> str:
    """Check database connectivity and trial_documents table schema.

    Verifies: connection, table existence, columns, enum types,
    foreign keys, and row counts.
    """
    lines = ["=== Database Check ===", ""]

    try:
        conn = _connect()
    except Exception as e:
        lines.append(f"FAIL Cannot connect to database: {e}")
        lines.append(f"  Connection URL: {SYNC_DB_URL[:40]}...")
        return "\n".join(lines)

    lines.append(f"OK   Connected to database")

    try:
        with conn.cursor() as cur:
            # Check trial_documents table
            cur.execute(
                "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name='trial_documents')"
            )
            exists = cur.fetchone()[0]
            if not exists:
                lines.append("FAIL trial_documents table does NOT exist")
                return "\n".join(lines)
            lines.append("OK   trial_documents table exists")

            # Column check
            cur.execute(
                "SELECT column_name, data_type, udt_name, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='trial_documents' "
                "ORDER BY ordinal_position"
            )
            cols = cur.fetchall()
            lines.append(f"OK   {len(cols)} columns found:")
            for col_name, dtype, udt, nullable in cols:
                lines.append(f"       {col_name}: {udt} (nullable={nullable})")

            # Check document_type_enum values
            cur.execute(
                "SELECT e.enumlabel FROM pg_enum e "
                "JOIN pg_type t ON e.enumtypid = t.oid "
                "WHERE t.typname = 'document_type_enum' "
                "ORDER BY e.enumsortorder"
            )
            enums = [r[0] for r in cur.fetchall()]
            if enums:
                lines.append(f"OK   document_type_enum values: {', '.join(enums)}")
            else:
                lines.append("WARN document_type_enum not found or has no values")

            # Row counts
            cur.execute("SELECT count(*) FROM trial_documents")
            doc_count = cur.fetchone()[0]
            lines.append(f"INFO {doc_count} documents in trial_documents")

            # Check members table (needed for auth)
            cur.execute(
                "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name='members')"
            )
            if cur.fetchone()[0]:
                cur.execute("SELECT count(*) FROM members")
                member_count = cur.fetchone()[0]
                lines.append(f"INFO {member_count} members in members table")
                if member_count == 0:
                    lines.append("WARN No members — AUTH_DISABLED mock user will fail (get_current_member needs at least 1 member)")
            else:
                lines.append("WARN members table not found")

            # Check profiles table
            cur.execute(
                "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name='profiles')"
            )
            if cur.fetchone()[0]:
                cur.execute("SELECT count(*) FROM profiles")
                lines.append(f"INFO {cur.fetchone()[0]} profiles in profiles table")

            # Check trials table (trial_id FK)
            cur.execute(
                "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name='trials')"
            )
            if cur.fetchone()[0]:
                cur.execute("SELECT count(*) FROM trials")
                trial_count = cur.fetchone()[0]
                lines.append(f"INFO {trial_count} trials in trials table")
                if trial_count == 0:
                    lines.append("WARN No trials — upload requires a valid trial_id")
            else:
                lines.append("WARN trials table not found")

    except Exception as e:
        lines.append(f"FAIL Query error: {e}")
        lines.append(traceback.format_exc())
    finally:
        conn.close()

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 3: check_redis
# ---------------------------------------------------------------------------
@mcp.tool()
def check_redis() -> str:
    """Check Redis connectivity (required for app startup lifespan)."""
    lines = ["=== Redis Check ===", ""]

    redis_url = _env.get("REDIS_URL", os.environ.get("REDIS_URL", ""))
    if not redis_url:
        lines.append("FAIL REDIS_URL not configured")
        return "\n".join(lines)

    lines.append(f"INFO REDIS_URL = {redis_url}")

    try:
        import redis
        r = redis.from_url(redis_url, decode_responses=True)
        r.ping()
        lines.append("OK   Redis is reachable (PING OK)")
        info = r.info("server")
        lines.append(f"INFO Redis version: {info.get('redis_version', 'unknown')}")
        r.close()
    except ImportError:
        lines.append("WARN redis-py not installed — trying raw connection test")
        try:
            import socket
            from urllib.parse import urlparse
            parsed = urlparse(redis_url)
            host = parsed.hostname or "localhost"
            port = parsed.port or 6379
            s = socket.create_connection((host, port), timeout=3)
            s.close()
            lines.append(f"OK   TCP connection to {host}:{port} succeeded")
        except Exception as e:
            lines.append(f"FAIL Cannot connect to Redis: {e}")
    except Exception as e:
        lines.append(f"FAIL Redis error: {e}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 4: check_server_health
# ---------------------------------------------------------------------------
@mcp.tool()
def check_server_health() -> str:
    """Check if the FastAPI server is running and reachable.

    Hits GET / and GET /docs to verify the app started.
    """
    lines = ["=== Server Health Check ===", ""]

    try:
        r = httpx.get(f"{BASE_URL}/", timeout=5)
        lines.append(f"OK   GET / -> {r.status_code}: {r.text[:200]}")
    except httpx.ConnectError:
        lines.append(f"FAIL Cannot connect to {BASE_URL} — is the server running?")
        lines.append("     Run: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload")
        return "\n".join(lines)
    except Exception as e:
        lines.append(f"FAIL GET / error: {e}")
        return "\n".join(lines)

    try:
        r = httpx.get(f"{BASE_URL}/docs", timeout=5)
        lines.append(f"OK   GET /docs -> {r.status_code}")
    except Exception as e:
        lines.append(f"WARN GET /docs error: {e}")

    # Check the upload endpoint exists (OPTIONS or wrong method)
    try:
        r = httpx.options(f"{BASE_URL}/api/trial-documents/upload", timeout=5)
        lines.append(f"INFO OPTIONS /api/trial-documents/upload -> {r.status_code}")
    except Exception as e:
        lines.append(f"WARN OPTIONS error: {e}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 5: check_auth
# ---------------------------------------------------------------------------
@mcp.tool()
def check_auth(token: str = "") -> str:
    """Test authentication against the server.

    Args:
        token: Optional Bearer token. If empty, tests without auth
               (works when AUTH_DISABLED=true).
    """
    lines = ["=== Auth Check ===", ""]

    auth_disabled = _env.get("AUTH_DISABLED", "").lower() == "true"
    lines.append(f"AUTH_DISABLED = {auth_disabled}")

    # Test /auth/me
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        r = httpx.get(f"{BASE_URL}/auth/me", headers=headers, timeout=5)
        lines.append(f"GET /auth/me -> {r.status_code}")
        if r.status_code == 200:
            lines.append(f"  OK   User: {r.text[:300]}")
        else:
            lines.append(f"  FAIL Response: {r.text[:300]}")
            if not token and not auth_disabled:
                lines.append("  HINT Set AUTH_DISABLED=true in .env for testing without a token")
    except httpx.ConnectError:
        lines.append(f"FAIL Server not reachable at {BASE_URL}")
    except Exception as e:
        lines.append(f"FAIL Error: {e}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 6: test_upload
# ---------------------------------------------------------------------------
@mcp.tool()
def test_upload(
    trial_id: str = "",
    token: str = "",
    document_type: str = "other",
) -> str:
    """Perform a test upload to POST /api/trial-documents/upload.

    Sends a small dummy PDF and reports the full response including
    status code, headers, and body. This is the main diagnostic tool.

    Args:
        trial_id: UUID of an existing trial. If empty, picks the first trial from DB.
        token: Bearer token. Leave empty when AUTH_DISABLED=true.
        document_type: Document type enum value (default: "other").
    """
    lines = ["=== Test Upload ===", ""]

    # Auto-detect trial_id if not provided
    if not trial_id:
        try:
            conn = _connect()
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM trials LIMIT 1")
                row = cur.fetchone()
                if row:
                    trial_id = str(row[0])
                    lines.append(f"INFO Auto-selected trial_id: {trial_id}")
                else:
                    lines.append("FAIL No trials in database — cannot test upload")
                    lines.append("     Insert a trial first or provide trial_id manually")
                    return "\n".join(lines)
            conn.close()
        except Exception as e:
            lines.append(f"FAIL Cannot query trials: {e}")
            return "\n".join(lines)

    # Build multipart request
    dummy_pdf = b"%PDF-1.4 dummy test content for upload debugging"
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    files = {
        "file": ("test_debug.pdf", io.BytesIO(dummy_pdf), "application/pdf"),
    }
    data = {
        "trial_id": trial_id,
        "document_name": "MCP Debug Test Upload",
        "document_type": document_type,
        "description": "Automated test from upload-debugger MCP server",
    }

    lines.append(f"Uploading to: POST {BASE_URL}/api/trial-documents/upload")
    lines.append(f"  trial_id: {trial_id}")
    lines.append(f"  document_type: {document_type}")
    lines.append("")

    try:
        r = httpx.post(
            f"{BASE_URL}/api/trial-documents/upload",
            files=files,
            data=data,
            headers=headers,
            timeout=30,
        )

        lines.append(f"Status: {r.status_code}")
        lines.append(f"Headers: {dict(r.headers)}")
        lines.append("")

        if r.status_code == 201:
            lines.append("OK   Upload succeeded!")
            try:
                body = r.json()
                lines.append(json.dumps(body, indent=2, default=str))
            except Exception:
                lines.append(r.text[:500])
        elif r.status_code == 500:
            lines.append("FAIL 500 Internal Server Error")
            lines.append("")
            try:
                body = r.json()
                lines.append(f"Detail: {json.dumps(body, indent=2, default=str)}")
            except Exception:
                lines.append(f"Raw body: {r.text[:1000]}")
            lines.append("")
            lines.append("Common causes of 500 on this endpoint:")
            lines.append("  1. Database: trial_documents table missing or schema mismatch")
            lines.append("  2. Enum: document_type value not in document_type_enum")
            lines.append("  3. Storage: GCS misconfigured or local uploads/ not writable")
            lines.append("  4. Auth: get_current_member fails (no members in DB)")
            lines.append("  5. Redis: app lifespan failed to connect")
            lines.append("  6. Missing .env variables (DATABASE_URL, etc.)")
        elif r.status_code == 401:
            lines.append("FAIL 401 Unauthorized — provide a valid Bearer token or set AUTH_DISABLED=true")
        elif r.status_code == 403:
            lines.append("FAIL 403 Forbidden — user has no member record in database")
        elif r.status_code == 422:
            lines.append("FAIL 422 Validation Error")
            try:
                lines.append(json.dumps(r.json(), indent=2))
            except Exception:
                lines.append(r.text[:500])
        else:
            lines.append(f"Unexpected status {r.status_code}")
            lines.append(r.text[:500])

    except httpx.ConnectError:
        lines.append(f"FAIL Cannot connect to {BASE_URL}")
        lines.append("     Is the server running?")
    except Exception as e:
        lines.append(f"FAIL Request error: {e}")
        lines.append(traceback.format_exc())

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 7: check_server_logs
# ---------------------------------------------------------------------------
@mcp.tool()
def check_server_logs(lines_count: int = 50) -> str:
    """Read the last N lines of uvicorn server output (if running in background).

    This checks common log file locations. For real-time logs,
    check your terminal where uvicorn is running.

    Args:
        lines_count: Number of recent log lines to return (default: 50).
    """
    result = ["=== Server Log Hints ===", ""]

    # Try common log locations
    log_paths = [
        SCRIPT_DIR / "uvicorn.log",
        SCRIPT_DIR / "app.log",
        SCRIPT_DIR / "server.log",
        Path("/tmp/uvicorn.log"),
    ]

    for lp in log_paths:
        if lp.exists():
            try:
                text = lp.read_text(encoding="utf-8", errors="replace")
                log_lines = text.strip().split("\n")
                tail = log_lines[-lines_count:]
                result.append(f"Found log file: {lp}")
                result.append("--- last {lines_count} lines ---")
                result.extend(tail)
                return "\n".join(result)
            except Exception as e:
                result.append(f"Error reading {lp}: {e}")

    result.append("No log files found in common locations.")
    result.append("")
    result.append("To capture logs, run the server with:")
    result.append("  uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload 2>&1 | tee uvicorn.log")
    result.append("")
    result.append("Or check the terminal where uvicorn is running for the traceback.")
    result.append("The 500 error traceback will appear there with the full Python exception.")

    return "\n".join(result)


# ---------------------------------------------------------------------------
# Tool 8: run_full_diagnostic
# ---------------------------------------------------------------------------
@mcp.tool()
def run_full_diagnostic(trial_id: str = "", token: str = "") -> str:
    """Run ALL checks in sequence and produce a full diagnostic report.

    This is the one-stop tool — it runs config, database, redis, server,
    auth, and test upload checks and combines all results.

    Args:
        trial_id: Optional trial UUID for test upload.
        token: Optional Bearer token.
    """
    sections = []
    sections.append(check_config())
    sections.append(check_database())
    sections.append(check_redis())
    sections.append(check_server_health())
    sections.append(check_auth(token=token))
    sections.append(test_upload(trial_id=trial_id, token=token))
    sections.append("")
    sections.append("=== Diagnostic Complete ===")

    return "\n\n".join(sections)


if __name__ == "__main__":
    mcp.run()
