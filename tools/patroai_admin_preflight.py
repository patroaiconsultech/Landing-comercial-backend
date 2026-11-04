#!/usr/bin/env python3
"""PatroAI Dashboard R1: preflight SOMENTE LEITURA, sem expor secrets.

Execucao: dentro do container BACKEND no Railway:
    python tools/patroai_admin_preflight.py

Nao modifica variaveis, PostgreSQL, usuarios, chaves ou tabelas.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

REQUIRED_KEYS = (
    "PATROAI_ADMIN_SESSION_KEY",
    "PATROAI_ADMIN_TOTP_KEY",
    "PATROAI_HANDOFF_ENCRYPTION_KEY",
)
TABLES = (
    "patroai_handoff",
    "patroai_handoff_key",
    "patroai_handoff_audit",
    "patroai_admin_staff",
    "patroai_admin_auth_window",
    "patroai_admin_login_challenge",
    "patroai_admin_session",
    "patroai_admin_audit",
)

@dataclass(frozen=True)
class Settings:
    flag: bool
    origin: bool
    keys: bool
    database_url: bool
    db_url: str

def get_settings(environ=None) -> Settings:
    env = os.environ if environ is None else environ
    flag = env.get("PATROAI_DASHBOARD_ENABLED", "") == "true"
    origin = env.get("PATROAI_ADMIN_ORIGIN", "") == "https://patroai.com.br"
    key_values: list[bytes] = []
    for name in REQUIRED_KEYS:
        raw = env.get(name, "")
        try:
            value = bytes.fromhex(raw)
        except ValueError:
            value = b""
        key_values.append(value)
    keys = all(len(x) == 32 for x in key_values) and len(set(key_values)) == 3
    url = env.get("DATABASE_URL", "")
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    db_ok = url.startswith("postgresql+psycopg://")
    return Settings(flag, origin, keys, db_ok, url if db_ok else "")

def db_checks(url: str) -> tuple[bool, bool, bool, bool]:
    """Return (connected, alembic_head, tables_complete, manager_active).
    This function uses one explicit read-only transaction. Never prints exceptions.
    """
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool
    engine = create_engine(
        url, poolclass=NullPool, connect_args={"connect_timeout": 5}
    )
    try:
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                conn.execute(text("SET TRANSACTION READ ONLY"))
                alembic_exists = conn.scalar(
                    text("SELECT to_regclass('alembic_version') IS NOT NULL")
                )
                head_ok = False
                if alembic_exists:
                    heads = conn.scalars(text("SELECT version_num FROM alembic_version")).all()
                    head_ok = len(heads) == 1 and heads[0] == "0001_patroai"
                table_results = [
                    bool(conn.scalar(text("SELECT to_regclass(:name) IS NOT NULL"), {"name": tbl}))
                    for tbl in TABLES
                ]
                tables_ok = all(table_results)
                active_manager = False
                if table_results[TABLES.index("patroai_admin_staff")]:
                    active_manager = bool(conn.scalar(text(
                        "SELECT EXISTS("
                        "SELECT 1 FROM patroai_admin_staff "
                        "WHERE role='manager' AND active IS TRUE)"
                    )))
                return True, head_ok, tables_ok, active_manager
            finally:
                transaction.rollback()
    finally:
        engine.dispose()

def main() -> int:
    s = get_settings()
    print("PATROAI_DASHBOARD_ENABLED=" + ("ON" if s.flag else "OFF"))
    print("PATROAI_ADMIN_ORIGIN=" + ("OK" if s.origin else "AUSENTE_OU_DIFERENTE"))
    print("CHAVES_ADMINISTRATIVAS=" + ("VALIDAS_E_DISTINTAS" if s.keys else "AUSENTES_OU_INVALIDAS"))
    print("DATABASE_URL_FORMATO=" + ("POSTGRES_OK" if s.database_url else "AUSENTE_OU_INVALIDO"))
    connected = head = tables = manager = False
    if s.database_url:
        try:
            connected, head, tables, manager = db_checks(s.db_url)
        except Exception:
            # Never show full exception: DB URL/credentials/hosts could leak.
            print("POSTGRES_CONEXAO=FALHOU_SEM_DETALHES")
        else:
            print("POSTGRES_CONEXAO=OK")
            print("ALEMBIC_HEAD=" + ("0001_patroai" if head else "OUTRO_OU_AUSENTE"))
            print("TABELAS_ADMIN=" + ("TODAS_PRESENTES" if tables else "AUSENTES_OU_INCOMPLETAS"))
            print("GESTOR_ATIVO=" + ("PRESENTE" if manager else "AUSENTE"))
    ready = all((s.flag, s.origin, s.keys, s.database_url, connected, head, tables, manager))
    print("LOGIN_PREREQUISITOS=" + ("CANDIDATOS_A_TESTE_E2E" if ready else "INCOMPLETOS"))
    # Successful preflight is not evidence of login/MFA runtime success.
    return 0 if ready else 2

if __name__ == "__main__":
    sys.exit(main())
