"""Alembic is the only schema authority. No app create_all."""
from __future__ import annotations
import os
from alembic import context
from sqlalchemy import create_engine, pool
from commercial.models import Base
from dashboard import models  # noqa: F401
from diagnosis import models as diagnosis_models  # noqa: F401; R3-A proposal metadata
cfg=context.config
target_metadata=Base.metadata
url=os.environ.get("DATABASE_URL","")
if url.startswith("postgres://"): url="postgresql+psycopg://"+url[len("postgres://"):]
if url.startswith("postgresql://"): url="postgresql+psycopg://"+url[len("postgresql://"):]
if not url.startswith("postgresql+psycopg://"):
    raise RuntimeError("PostgreSQL is required; no credentials bundled")
if os.environ.get("PATROAI_ALLOW_MIGRATION_EXECUTION")!="true":
    raise RuntimeError("Explicit independently authorized migration execution gate required")
def run_migrations_online():
    engine=create_engine(url,poolclass=pool.NullPool)
    with engine.connect() as conn:
        context.configure(connection=conn,target_metadata=target_metadata,compare_type=True)
        with context.begin_transaction(): context.run_migrations()
    engine.dispose()
if context.is_offline_mode():
    raise RuntimeError("Offline SQL emission not allowed without separate gate")
run_migrations_online()
