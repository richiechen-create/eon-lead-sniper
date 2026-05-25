import os

# Force SQLite + fixed env BEFORE app modules import their cached settings.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("APOLLO_API_KEY", "test-key")
os.environ.setdefault("RESEND_API_KEY", "")  # disable real sends
os.environ.setdefault("ADMIN_EMAIL", "admin@example.com")
os.environ.setdefault("DEFAULT_REP_EMAIL", "dan@example.com")
os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("CREDIT_BUDGET_MONTHLY", "9500")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base


@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def session(engine) -> Session:
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    s = SessionLocal()
    try:
        yield s
        s.commit()
    finally:
        s.close()
