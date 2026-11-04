"""Disabled by default. No automatic schema creation or bootstrap."""
import os
from dataclasses import dataclass
from functools import lru_cache
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

@dataclass(frozen=True)
class Config:
    origin: str
    session_key: bytes
    totp_key: bytes
    handoff_key: bytes
    db_url: str

def config() -> Config:
    if os.getenv("PATROAI_DASHBOARD_ENABLED") != "true":
        raise RuntimeError("ADMIN_DISABLED")
    origin = os.getenv("PATROAI_ADMIN_ORIGIN", "")
    if not (origin.startswith("https://") and origin.count("/") == 2) and origin != "https://testserver":
        raise RuntimeError("ADMIN_ORIGIN_INVALID")
    def key(name):
        value = os.getenv(name, "")
        try:
            out = bytes.fromhex(value)
        except ValueError:
            raise RuntimeError("ADMIN_CONFIG_INVALID") from None
        if len(out) != 32:
            raise RuntimeError("ADMIN_CONFIG_INVALID")
        return out
    sk = key("PATROAI_ADMIN_SESSION_KEY")
    tk = key("PATROAI_ADMIN_TOTP_KEY")
    hk = key("PATROAI_HANDOFF_ENCRYPTION_KEY")
    if len({sk,tk,hk}) != 3:
        raise RuntimeError("ADMIN_CONFIG_INVALID")
    raw = os.getenv("DATABASE_URL", "")
    if raw.startswith("postgres://"):
        raw = "postgresql+psycopg://" + raw[len("postgres://"):]
    elif raw.startswith("postgresql://"):
        raw = "postgresql+psycopg://" + raw[len("postgresql://"):]
    if not raw.startswith("postgresql+psycopg://"):
        raise RuntimeError("POSTGRES_REQUIRED")
    return Config(origin, sk, tk, hk, raw)

@lru_cache(maxsize=1)
def session_factory():
    c = config()
    engine = create_engine(c.db_url, pool_pre_ping=True, pool_size=4, max_overflow=2)
    return sessionmaker(engine, expire_on_commit=False)

def get_db():
    try:
        factory = session_factory()
    except Exception:
        # Any config/driver/DB error fails closed; public chat is unaffected.
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="ADMIN_UNAVAILABLE") from None
    with factory() as db:
        yield db

def require_enabled():
    try: return config()
    except Exception:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="ADMIN_UNAVAILABLE") from None
