"""
Main application file
"""

import os
import sys
from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.api.routes.auth import router as auth_router
from app.api.routes.query import router as query_router
from app.api.routes.upload import router as upload_router

# Storage routes
from app.api.routes.storage.storage import router as storage_router
from app.api.routes.local_files import router as local_files_router

# Business API routes
from app.api.routes.api.organizations import router as organizations_router
from app.api.routes.api.members import router as members_router
from app.api.routes.api.roles import router as roles_router
from app.api.routes.api.invitations import router as invitations_router
from app.api.routes.api.trials import router as trials_router
from app.api.routes.api.trial_members import router as trial_members_router
from app.api.routes.api.trial_documents import router as trial_documents_router
from app.api.routes.api.patients import router as patients_router
from app.api.routes.api.trial_patients import router as trial_patients_router
from app.api.routes.api.patient_visits import router as patient_visits_router
from app.api.routes.api.patient_documents import router as patient_documents_router
from app.api.routes.api.chat_sessions import router as chat_sessions_router
from app.api.routes.api.chat_messages import router as chat_messages_router
from app.api.routes.api.qa_repository import router as qa_repository_router

from app.api.routes.api.tasks import router as tasks_router
from app.api.routes.api.activities import router as trial_activities_router
from app.api.routes.api.complete_visit import router as complete_visit_router
from app.api.routes.api.visit_activities import router as visit_activities_router

from contextlib import asynccontextmanager
from redis.asyncio import Redis
import logging

# Load environment variables from .env file
load_dotenv()

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Application state for storing loaded models
app_state = {}


# --- Lifespan handler ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    redis_client = None

    try:
        logging.info("Initializing Redis…")

        # --- 1) Connect to Redis ---
        try:
            redis_url = os.getenv("REDIS_URL")
            if redis_url:
                redis_client = Redis.from_url(redis_url, decode_responses=False)
                await redis_client.ping()
                app.state.redis_client = redis_client
                logging.info("Redis connection successful.")
            else:
                logging.warning("REDIS_URL not set, skipping Redis initialization.")
                app.state.redis_client = None
        except Exception as e:
            logging.error(f"Redis connection failed: {e}")
            app.state.redis_client = None

        # --- 2) Self-Healing: Run missing migrations ---
        try:
            from sqlalchemy import text
            from app.db.session import engine
            
            async with engine.connect() as conn:
                # Helper to check if column exists
                async def column_exists(table, column):
                    # Check in public schema explicitly to be safe
                    res = await conn.execute(text(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_name = :t AND column_name = :c"
                    ), {"t": table, "c": column})
                    return res.scalar() is not None

                # Ensure organization_member_type ENUM exists
                try:
                    await conn.execute(text("SELECT 'admin'::organization_member_type;"))
                except Exception:
                    logging.info("Creating ENUM organization_member_type...")
                    await conn.execute(text("CREATE TYPE organization_member_type AS ENUM ('admin', 'staff');"))
                    await conn.commit()

                # Profiles
                if not await column_exists('profiles', 'is_active'):
                    logging.info("Adding profiles.is_active...")
                    await conn.execute(text("ALTER TABLE profiles ADD COLUMN is_active BOOLEAN DEFAULT TRUE;"))
                    await conn.commit()

                # Members
                if not await column_exists('members', 'is_active'):
                    logging.info("Adding members.is_active...")
                    await conn.execute(text("ALTER TABLE members ADD COLUMN is_active BOOLEAN DEFAULT TRUE;"))
                    await conn.commit()

                # Invitations
                if not await column_exists('invitations', 'token'):
                    logging.info("Adding and populating invitations.token...")
                    await conn.execute(text("ALTER TABLE invitations ADD COLUMN token TEXT;"))
                    # Populate NULL tokens with unique values
                    await conn.execute(text("UPDATE invitations SET token = MD5(random()::text) WHERE token IS NULL;"))
                    await conn.execute(text("ALTER TABLE invitations ALTER COLUMN token SET NOT NULL;"))
                    await conn.commit()
                
                if not await column_exists('invitations', 'status'):
                    logging.info("Adding invitations.status...")
                    await conn.execute(text("ALTER TABLE invitations ADD COLUMN status TEXT DEFAULT 'pending';"))
                    await conn.commit()
                
                if not await column_exists('invitations', 'invited_at'):
                    logging.info("Adding invitations.invited_at...")
                    await conn.execute(text("ALTER TABLE invitations ADD COLUMN invited_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;"))
                    await conn.commit()

                if not await column_exists('invitations', 'expires_at'):
                    logging.info("Adding invitations.expires_at...")
                    await conn.execute(text("ALTER TABLE invitations ADD COLUMN expires_at TIMESTAMP WITH TIME ZONE;"))
                    await conn.commit()

                if not await column_exists('invitations', 'accepted_at'):
                    logging.info("Adding invitations.accepted_at...")
                    await conn.execute(text("ALTER TABLE invitations ADD COLUMN accepted_at TIMESTAMP WITH TIME ZONE;"))
                    await conn.commit()

                # Trials (New stability columns)
                if not await column_exists('trials', 'visit_schedule_template'):
                    logging.info("Adding trials.visit_schedule_template...")
                    await conn.execute(text("ALTER TABLE trials ADD COLUMN visit_schedule_template JSONB DEFAULT '{}';"))
                    await conn.commit()

                if not await column_exists('trials', 'budget_data'):
                    logging.info("Adding trials.budget_data...")
                    await conn.execute(text("ALTER TABLE trials ADD COLUMN budget_data JSONB DEFAULT '{}';"))
                    await conn.commit()

                # Patient Visits
                if not await column_exists('patient_visits', 'cost_data'):
                    logging.info("Adding patient_visits.cost_data...")
                    await conn.execute(text("ALTER TABLE patient_visits ADD COLUMN cost_data JSONB DEFAULT '{}';"))
                    await conn.commit()

                # Trial Members
                if not await column_exists('trial_members', 'settings'):
                    logging.info("Adding trial_members.settings...")
                    await conn.execute(text("ALTER TABLE trial_members ADD COLUMN settings JSONB DEFAULT '{}';"))
                    await conn.commit()

                # Tasks
                if not await column_exists('tasks', 'category'):
                    logging.info("Adding tasks.category...")
                    await conn.execute(text("ALTER TABLE tasks ADD COLUMN category TEXT;"))
                    await conn.commit()
                
            logging.info("Self-healing: Migration check completed.")
        except Exception as e:
            logging.error(f"Self-healing migrations failed: {e}")

        yield

    finally:
        # --- 3) Shutdown cleanup ---
        if redis_client:
            try:
                await redis_client.close()
                logging.info("Redis connection closed.")
            except Exception as e:
                logging.error(f"Error closing Redis connection: {e}")


app = FastAPI(lifespan=lifespan)
# app = FastAPI()

# Trust proxy headers (X-Forwarded-For, X-Forwarded-Proto) from Cloud Run load balancer
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=["*"])

# CORS configuration for production
# Note: For production, specify exact origins instead of ['*'] for better security
allowed_origins = [
    "https://themison-mvp-v1.vercel.app",
    "https://core-frontendv2.vercel.app",
    "https://core-frontendv2-biobert.vercel.app",
    "https://core-frontend-v3.vercel.app",
    "https://core-frontend-v3-improvements.vercel.app",
    "https://core-frontend-preview.vercel.app",
    "https://themison-frontend-eu-768873408671.europe-west1.run.app",
    "http://localhost:8080",
    "http://localhost:5173",
    "http://localhost:3000",
]

# Allow all origins from environment variable if set
is_allow_all = os.getenv("ALLOW_ALL_ORIGINS", "false").lower() == "true"

# Add FRONTEND_URL from environment if set
frontend_url = os.getenv("FRONTEND_URL")
if frontend_url and frontend_url not in allowed_origins:
    allowed_origins.append(frontend_url)

# If allow_all is requested, we use a regex to allow everything while still allowing credentials
if is_allow_all:
    allowed_origins = []
    allowed_origin_regex = r".*"
else:
    allowed_origin_regex = r"https://.*\.run\.app$"

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=allowed_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Length", "X-Job-ID", "X-Document-ID"],
    max_age=600,
)
logging.info(f"CORS initialized. Allow All: {is_allow_all}. Origins: {len(allowed_origins)}")


@app.get("/")
def root():
    return {"status": "ok", "version": "4.0.2-CORS-FORCE", "message": "Themison Backend API"}


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "core-backend-eu",
        "version": "1.1.0",
    }


@app.get("/debug-config")
async def debug_config():
    from app.config import get_settings
    from sqlalchemy import select, func
    from app.dependencies.db import get_db
    from app.models.profiles import Profile
    from app.models.members import Member
    from app.models.organizations import Organization
    
    settings = get_settings()
    
    p_count = -1
    m_count = -1
    o_count = -1
    
    try:
        # Get DB session from generator
        db_gen = get_db()
        db = await db_gen.__anext__()
        try:
            p_count = (await db.execute(select(func.count()).select_from(Profile))).scalar()
            m_count = (await db.execute(select(func.count()).select_from(Member))).scalar()
            o_count = (await db.execute(select(func.count()).select_from(Organization))).scalar()
        finally:
            # We don't strictly need to close here as it's a debug endpoint 
            # and the generator will handle it if we let it, but simple is better
            pass
    except Exception as e:
        logging.error(f"Debug stats failed: {e}")

    return {
        "upload_api_key_len": len(settings.upload_api_key),
        "upload_api_key_prefix": (
            settings.upload_api_key[:3] if settings.upload_api_key else "EMPTY"
        ),
        "rag_address": settings.rag_service_address,
        "use_grpc": settings.use_grpc_rag,
        "auth0_domain": settings.auth0_domain,
        "auth0_audience": settings.auth0_audience,
        "auth0_domain_len": len(settings.auth0_domain),
        "db_stats": {
            "profiles": p_count,
            "members": m_count,
            "organizations": o_count
        }
    }


app.include_router(auth_router, prefix="/auth", tags=["auth"])

app.include_router(
    upload_router,
    prefix="/upload",
    tags=["upload"],
)

app.include_router(
    query_router,
    prefix="/query",
    tags=["query"],
)

# --- Storage routes ---
app.include_router(storage_router, prefix="/storage", tags=["storage"])
app.include_router(local_files_router, prefix="/local-files", tags=["local-files"])

# --- Business API routes ---
app.include_router(
    organizations_router, prefix="/api/organizations", tags=["organizations"]
)
app.include_router(members_router, prefix="/api/members", tags=["members"])
app.include_router(roles_router, prefix="/api/roles", tags=["roles"])
app.include_router(invitations_router, prefix="/api/invitations", tags=["invitations"])
app.include_router(trials_router, prefix="/api/trials", tags=["trials"])
app.include_router(
    trial_members_router, prefix="/api/trial-members", tags=["trial-members"]
)

app.include_router(
    trial_activities_router,
    prefix="/api/trials/{trial_id}/activities",
    tags=["trial-activities"],
)
app.include_router(
    trial_documents_router, prefix="/api/trial-documents", tags=["trial-documents"]
)
app.include_router(patients_router, prefix="/api/patients", tags=["patients"])
app.include_router(
    trial_patients_router, prefix="/api/trial-patients", tags=["trial-patients"]
)
app.include_router(
    patient_visits_router, prefix="/api/patient-visits", tags=["patient-visits"]
)

app.include_router(
    complete_visit_router, prefix="/api/patient-visits", tags=["patient-visits"]
)
app.include_router(
    visit_activities_router, prefix="/api/patient-visits", tags=["patient-visits"]
)
app.include_router(tasks_router, prefix="/api/tasks", tags=["tasks"])
app.include_router(
    patient_documents_router,
    prefix="/api/patient-documents",
    tags=["patient-documents"],
)
app.include_router(
    chat_sessions_router, prefix="/api/chat-sessions", tags=["chat-sessions"]
)
app.include_router(
    chat_messages_router, prefix="/api/chat-messages", tags=["chat-messages"]
)
app.include_router(
    qa_repository_router, prefix="/api/qa-repository", tags=["qa-repository"]
)
