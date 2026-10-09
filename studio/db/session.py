"""Database engine, connection pooling, and session management for Phase 2."""
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from ..config import DATA, settings

DEFAULT_PG_URL = 'postgresql+psycopg2://postgres:postgres@127.0.0.1:5433/studio'

class DatabaseConnectionError(RuntimeError):
    """Raised when PostgreSQL database cannot be reached."""
    pass


def get_database_url() -> str:
    """Resolve PostgreSQL connection string with environment precedence."""
    env_url = os.environ.get('STUDIO_DATABASE_URL') or os.environ.get('DATABASE_URL')
    if env_url:
        return env_url
    cfg = settings()
    if cfg.get('database_url'):
        return str(cfg['database_url'])
    return DEFAULT_PG_URL


_engine: Optional[Engine] = None
_SessionFactory: Optional[sessionmaker] = None


def get_engine(url: Optional[str] = None) -> Engine:
    """Get or create singleton SQLAlchemy engine with pre-ping connection pool."""
    global _engine, _SessionFactory
    resolved_url = url or get_database_url()
    if _engine is None or str(_engine.url) != resolved_url:
        _engine = create_engine(
            resolved_url,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=1800,
            echo=False
        )
        _SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=_engine)
    return _engine


def get_session_factory(url: Optional[str] = None) -> sessionmaker:
    """Get configured sessionmaker."""
    global _SessionFactory
    if _SessionFactory is None or url:
        get_engine(url)
    return _SessionFactory


def check_connection(engine: Optional[Engine] = None, timeout_seconds: int = 5) -> bool:
    """Verify that PostgreSQL is reachable, raising an actionable error on failure."""
    eng = engine or get_engine()
    try:
        with eng.connect() as conn:
            conn.execute(text('SELECT 1'))
        return True
    except Exception as exc:
        url_display = str(eng.url).split('@')[-1] if '@' in str(eng.url) else str(eng.url)
        raise DatabaseConnectionError(
            f"Could not connect to PostgreSQL ({url_display}): {exc}\n"
            "Actionable remedy:\n"
            "1. Ensure the PostgreSQL service is running (WSL Ubuntu service or Docker Compose).\n"
            "   Command to start in WSL: 'wsl -u root -d Ubuntu -- service postgresql start'\n"
            "   Or run launch script: 'scripts\\launch_postgres.bat'\n"
            "2. Verify host, port (5433), credentials and database in STUDIO_DATABASE_URL."
        ) from exc


def get_db() -> Generator[Session, None, None]:
    """FastAPI request-scoped database session dependency."""
    factory = get_session_factory()
    session: Session = factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def session_scope(engine: Optional[Engine] = None) -> Generator[Session, None, None]:
    """Context manager for worker jobs, CLI scripts and background tasks."""
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine or get_engine())
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
