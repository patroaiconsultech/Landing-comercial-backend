"""PROPOSAL_ONLY: initial PatroAI commercial and admin schema, additive only.

Revision ID: 0001_patroai
Revises:
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision="0001_patroai"
down_revision=None
branch_labels=None
depends_on=None
def upgrade():
    U=postgresql.UUID(as_uuid=True)
    op.create_table("patroai_handoff",
        sa.Column("id",U,primary_key=True),sa.Column("conversation_id",U,nullable=False),
        sa.Column("transcript_ciphertext",sa.LargeBinary(),nullable=False),
        sa.Column("contact_ciphertext",sa.LargeBinary()),
        sa.Column("contact_preference",sa.String(12),nullable=False),
        sa.Column("consent_version",sa.String(64),nullable=False),
        sa.Column("transcript_provenance",sa.String(32),nullable=False),
        sa.Column("status",sa.String(12),nullable=False),sa.Column("version",sa.Integer(),nullable=False),
        sa.Column("received_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("retention_due_at",sa.DateTime(timezone=True),nullable=False),
        sa.CheckConstraint("status IN ('new','in_review','closed')",name="ck_patroai_handoff_status"),
        sa.CheckConstraint("transcript_provenance = 'user_submitted'",name="ck_patroai_handoff_provenance"),
        sa.CheckConstraint("contact_preference IN ('none','email','whatsapp')",name="ck_patroai_handoff_preference"))
    op.create_index("ix_patroai_handoff_status_received","patroai_handoff",["status","received_at"])
    op.create_index("ix_patroai_handoff_retention","patroai_handoff",["retention_due_at"])
    op.create_table("patroai_handoff_key",
        sa.Column("key_hmac",sa.LargeBinary(32),primary_key=True),
        sa.Column("request_hmac",sa.LargeBinary(32),nullable=False),
        sa.Column("handoff_id",U,sa.ForeignKey("patroai_handoff.id",ondelete="RESTRICT")),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("replay_until",sa.DateTime(timezone=True),nullable=False),
        sa.Column("tombstone_until",sa.DateTime(timezone=True),nullable=False))
    op.create_table("patroai_handoff_audit",
        sa.Column("id",U,primary_key=True),sa.Column("handoff_id",U,sa.ForeignKey("patroai_handoff.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("event",sa.String(40),nullable=False),
        sa.Column("happened_at",sa.DateTime(timezone=True),nullable=False))
    op.create_table("patroai_admin_staff",
        sa.Column("id",U,primary_key=True),sa.Column("email",sa.String(254),nullable=False,unique=True),
        sa.Column("password_salt",sa.LargeBinary(16),nullable=False),
        sa.Column("password_digest",sa.LargeBinary(32),nullable=False),
        sa.Column("totp_ciphertext",sa.LargeBinary(),nullable=False),
        sa.Column("last_totp_step",sa.BigInteger(),nullable=False,server_default="-1"),
        sa.Column("role",sa.String(16),nullable=False),sa.Column("active",sa.Boolean(),nullable=False,server_default=sa.true()),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.CheckConstraint("role IN ('analyst','manager')",name="ck_patroai_staff_role"))
    op.create_table("patroai_admin_auth_window",
        sa.Column("key_digest",sa.LargeBinary(32),primary_key=True),
        sa.Column("window_started_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("failures",sa.Integer(),nullable=False))
    op.create_table("patroai_admin_login_challenge",
        sa.Column("token_digest",sa.LargeBinary(32),primary_key=True),
        sa.Column("staff_id",U,sa.ForeignKey("patroai_admin_staff.id"),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("expires_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("failures",sa.Integer(),nullable=False))
    op.create_table("patroai_admin_session",
        sa.Column("token_digest",sa.LargeBinary(32),primary_key=True),
        sa.Column("staff_id",U,sa.ForeignKey("patroai_admin_staff.id"),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("seen_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("expires_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("revoked_at",sa.DateTime(timezone=True)))
    op.create_table("patroai_admin_audit",
        sa.Column("id",U,primary_key=True),sa.Column("staff_id",U),
        sa.Column("handoff_id",U),sa.Column("event",sa.String(48),nullable=False),
        sa.Column("request_id",sa.String(40),nullable=False),
        sa.Column("occurred_at",sa.DateTime(timezone=True),nullable=False))
    op.create_index("ix_patroai_admin_audit_occurred","patroai_admin_audit",["occurred_at"])

def downgrade():
    # Down migrations could destroy consent evidence, records and session/audit trails.
    # Operational rollback is code/feature flag rollback, followed by a separate
    # independently reviewed data retention/migration plan.
    raise RuntimeError("Destructive downgrade forbidden; manual AO-01 gate required")
