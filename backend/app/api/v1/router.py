"""Top-level router that combines all version 1 endpoint groups."""

from fastapi import APIRouter

from app.api.v1.admin_appointments import router as admin_appointments_router
from app.api.v1.admin_audit import router as admin_audit_router
from app.api.v1.admin_chat import router as admin_chat_router
from app.api.v1.admin_chat_sessions import router as admin_chat_sessions_router
from app.api.v1.appointments import router as appointments_router
from app.api.v1.auth import router as auth_router
from app.api.v1.chat import router as chat_router
from app.api.v1.chat_sessions import router as chat_sessions_router
from app.api.v1.health import router as health_router
from app.api.v1.patients import router as patients_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(admin_chat_router)
api_router.include_router(admin_chat_sessions_router)
api_router.include_router(admin_audit_router)
api_router.include_router(admin_appointments_router)
api_router.include_router(patients_router)
api_router.include_router(appointments_router)
api_router.include_router(chat_router)
api_router.include_router(chat_sessions_router)
