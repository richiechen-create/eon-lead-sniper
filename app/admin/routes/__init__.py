from fastapi import APIRouter

from app.admin.routes import (
    assignments,
    auth,
    companies,
    dashboard,
    do_not_contact,
    leads,
    profiles,
    reps,
    routing_rules,
    runs,
    triggers,
)

router = APIRouter(prefix="/admin")
router.include_router(auth.router)
router.include_router(dashboard.router)
router.include_router(triggers.router)
router.include_router(leads.router)
router.include_router(companies.router)
router.include_router(assignments.router)
router.include_router(profiles.router)
router.include_router(reps.router)
router.include_router(routing_rules.router)
router.include_router(do_not_contact.router)
router.include_router(runs.router)
