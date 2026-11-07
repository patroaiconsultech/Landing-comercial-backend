"""R3-A additive migration PROPOSAL_ONLY — NOT AUTHORIZED TO EXECUTE.

Revision ID: 0003_patroai_diagnosis_r3a
Revises: 0002_patroai_super_admin_r2

Requires AO-01 gate, reviewed privacy/retention policy, staging PostgreSQL
preflight and explicit migration approval. No table locks on R1/R2 tables.
No backfill, no destructive changes; old backend remains compatible.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision="0003_patroai_diagnosis_r3a"
down_revision="0002_patroai_super_admin_r2"
branch_labels=None
depends_on=None

def upgrade():
    U=postgresql.UUID(as_uuid=True)
    op.create_table("patroai_diagnosis",
        sa.Column("id",U,primary_key=True),
        sa.Column("init_key_digest",sa.LargeBinary(32),nullable=False,unique=True),
        sa.Column("init_payload_digest",sa.LargeBinary(32),nullable=False),
        sa.Column("session_digest",sa.LargeBinary(32),nullable=False,unique=True),
        sa.Column("session_expires_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("contact_ciphertext",sa.LargeBinary()),
        sa.Column("consent_ai",sa.Boolean(),nullable=False),
        sa.Column("consent_contact",sa.Boolean(),nullable=False),
        sa.Column("consent_notice_version",sa.String(64),nullable=False),
        sa.Column("consent_accepted_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("status",sa.String(20),nullable=False),
        sa.Column("version",sa.Integer(),nullable=False),
        sa.Column("turn_count",sa.Integer(),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("last_activity_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("retention_due_at",sa.DateTime(timezone=True),nullable=False),
        sa.CheckConstraint("status IN ('registered','in_progress','completed','in_review','qualified','disqualified','contacted','revoked')",
            name="ck_patroai_diagnosis_status"),
        sa.CheckConstraint("version >= 1 AND turn_count >= 0",name="ck_patroai_diagnosis_counts"))
    op.create_index("ix_patroai_diagnosis_status_created","patroai_diagnosis",["status","created_at"])
    op.create_index("ix_patroai_diagnosis_retention","patroai_diagnosis",["retention_due_at"])

    op.create_table("patroai_diagnosis_turn",
        sa.Column("id",U,primary_key=True),
        sa.Column("diagnosis_id",U,sa.ForeignKey("patroai_diagnosis.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("request_digest",sa.LargeBinary(32),nullable=False),
        sa.Column("user_ciphertext",sa.LargeBinary(),nullable=False),
        sa.Column("assistant_ciphertext",sa.LargeBinary()),
        sa.Column("language",sa.String(2),nullable=False),
        sa.Column("status",sa.String(12),nullable=False),
        sa.Column("expected_version",sa.Integer(),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("lease_until",sa.DateTime(timezone=True),nullable=False),
        sa.Column("finished_at",sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('pending','complete','failed')",name="ck_patroai_diagnosis_turn_status"))
    op.create_index("ix_patroai_diag_turn_history","patroai_diagnosis_turn",["diagnosis_id","finished_at"])

    op.create_table("patroai_diagnosis_event",
        sa.Column("id",U,primary_key=True),
        sa.Column("diagnosis_id",U,sa.ForeignKey("patroai_diagnosis.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("kind",sa.String(40),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint("diagnosis_id","kind",name="uq_patroai_diag_event_once"))

    op.create_table("patroai_diagnosis_audit",
        sa.Column("id",U,primary_key=True),
        sa.Column("diagnosis_id",U,sa.ForeignKey("patroai_diagnosis.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("staff_id",U),
        sa.Column("event",sa.String(48),nullable=False),
        sa.Column("happened_at",sa.DateTime(timezone=True),nullable=False))

    op.create_table("patroai_diagnosis_rate",
        sa.Column("key_digest",sa.LargeBinary(32),primary_key=True),
        sa.Column("window_started_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("count",sa.Integer(),nullable=False))

def downgrade():
    raise RuntimeError("R3-A tables may contain consent, PII and audit data. "
                       "Disable flags and revert code; destructive downgrade needs AO-01.")
