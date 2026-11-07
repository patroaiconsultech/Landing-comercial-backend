"""R3-A retention purge — PROPOSAL_ONLY; NOT SCHEDULED OR APPROVED.

Usage: python tools/purge_diagnosis_r3a.py             # read-only count
       python tools/purge_diagnosis_r3a.py --execute   # explicit STAGING gate
Never use as an Alembic downgrade. No user transcript is logged.
"""
from __future__ import annotations
import argparse
import os
from datetime import datetime, timedelta, timezone
from sqlalchemy import delete, func, select
from dashboard.config import config, session_factory
from diagnosis.models import Diagnosis, DiagnosisAudit, DiagnosisEvent, DiagnosisRate, DiagnosisTurn

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--execute",action="store_true")
    args=parser.parse_args()
    if os.getenv("RAILWAY_ENVIRONMENT_NAME")!="STAGING":
        raise SystemExit("ABORT=STAGING_ONLY")
    if args.execute and os.getenv("PATROAI_R3_PURGE_APPROVED")!="true":
        raise SystemExit("ABORT=PURGE_GATE_CLOSED")
    # Existing administrator config and PostgreSQL are required.
    cfg=config()
    t=datetime.now(timezone.utc)
    with session_factory()() as db:
        ids=db.scalars(select(Diagnosis.id).where(
            Diagnosis.retention_due_at<=t).order_by(
            Diagnosis.retention_due_at).limit(100)).all()
        rate_count=db.scalar(select(func.count()).select_from(DiagnosisRate).where(
            DiagnosisRate.window_started_at < t-timedelta(days=3)))
        print(f"PURGE_CANDIDATE_DIAGNOSES={len(ids)}")
        print(f"PURGE_CANDIDATE_RATE_BUCKETS={rate_count}")
        if not args.execute:
            print("DRY_RUN_ONLY=YES")
            return
        if ids:
            # Child rows removed first; all in one transaction.
            db.execute(delete(DiagnosisEvent).where(DiagnosisEvent.diagnosis_id.in_(ids)))
            db.execute(delete(DiagnosisAudit).where(DiagnosisAudit.diagnosis_id.in_(ids)))
            db.execute(delete(DiagnosisTurn).where(DiagnosisTurn.diagnosis_id.in_(ids)))
            db.execute(delete(Diagnosis).where(Diagnosis.id.in_(ids)))
        db.execute(delete(DiagnosisRate).where(
            DiagnosisRate.window_started_at < t-timedelta(days=3)))
        db.commit()
        print("PURGE_COMMITTED=YES")
if __name__=="__main__":
    main()
