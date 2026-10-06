from fastapi import APIRouter

from app.api.v1 import (
    applications,
    auth,
    automation,
    documents,
    jobs,
    matching,
    preferences,
    profile,
    questions,
    resumes,
    users,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(profile.router)
api_router.include_router(preferences.router)
api_router.include_router(resumes.router)
api_router.include_router(matching.router)
api_router.include_router(applications.router)
api_router.include_router(documents.router)
api_router.include_router(documents.photo_router)
api_router.include_router(questions.router)
api_router.include_router(automation.router)
api_router.include_router(jobs.router)
