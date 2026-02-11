"""
Main application file
"""

import os
import sys
from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes.auth import router as auth_router
from app.api.routes.query import router as query_router
from app.api.routes.upload import router as upload_router
from app.dependencies.auth import auth

# Storage routes
from app.api.routes.storage.storage import router as storage_router

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
            redis_client = Redis.from_url(redis_url, decode_responses=False)
            await redis_client.ping()
            app.state.redis_client = redis_client
            logging.info("Redis connection successful.")
        except Exception as e:
            logging.error(f"Redis connection failed: {e}")
            raise RuntimeError("Failed to connect to Redis") from e            
        
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

# CORS configuration for production
# Note: For production, specify exact origins instead of ['*'] for better security
allowed_origins = [
    "https://themison-mvp-v1.vercel.app",
    "https://core-frontendv2.vercel.app",
    "https://core-frontendv2-biobert.vercel.app",
    "https://core-frontend-v3.vercel.app",
    "https://core-frontend-v3-improvements.vercel.app",
    "https://core-frontend-preview.vercel.app",
    "http://localhost:8080",
    "http://localhost:5173",
]

# Add FRONTEND_URL from environment if set
frontend_url = os.getenv("FRONTEND_URL")
if frontend_url and frontend_url not in allowed_origins:
    allowed_origins.append(frontend_url)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True if allowed_origins != ["*"] else False,
    allow_methods=["*"],  # Allow all methods including OPTIONS for preflight
    allow_headers=["*"],
    expose_headers=["*"],
)

# Allow all origins from environment variable if set
if os.getenv("ALLOW_ALL_ORIGINS", "false").lower() == "true":
    allowed_origins = ["*"]
logging.info(f"calling root endpoint with allowed origins")
@app.get("/")
def root():
    return {"status": "ok"}

app.include_router(
    auth_router,
    prefix="/auth",
    tags=["auth"]
)

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

# --- Business API routes ---
app.include_router(organizations_router, prefix="/api/organizations", tags=["organizations"])
app.include_router(members_router, prefix="/api/members", tags=["members"])
app.include_router(roles_router, prefix="/api/roles", tags=["roles"])
app.include_router(invitations_router, prefix="/api/invitations", tags=["invitations"])
app.include_router(trials_router, prefix="/api/trials", tags=["trials"])
app.include_router(trial_members_router, prefix="/api/trial-members", tags=["trial-members"])
app.include_router(trial_documents_router, prefix="/api/trial-documents", tags=["trial-documents"])
app.include_router(patients_router, prefix="/api/patients", tags=["patients"])
app.include_router(trial_patients_router, prefix="/api/trial-patients", tags=["trial-patients"])
app.include_router(patient_visits_router, prefix="/api/patient-visits", tags=["patient-visits"])
app.include_router(patient_documents_router, prefix="/api/patient-documents", tags=["patient-documents"])
app.include_router(chat_sessions_router, prefix="/api/chat-sessions", tags=["chat-sessions"])
app.include_router(chat_messages_router, prefix="/api/chat-messages", tags=["chat-messages"])
app.include_router(qa_repository_router, prefix="/api/qa-repository", tags=["qa-repository"])

