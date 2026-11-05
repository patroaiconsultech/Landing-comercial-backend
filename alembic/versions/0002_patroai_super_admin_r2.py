"""Explicit PatroAI Commercial super_admin role, NO global/platform privilege.

Revision ID: 0002_patroai_super_admin_r2
Revises: 0001_patroai
Source-only proposal. Run only after independent AO-01 gate and STAGING rehearsal.
PostgreSQL ALTER TABLE CHECK constraint takes an ACCESS EXCLUSIVE lock briefly.
No automatic downgrade: existing super_admin rows would violate R1 constraint.
"""
from alembic import op

revision = "0002_patroai_super_admin_r2"
down_revision = "0001_patroai"
branch_labels = None
depends_on = None

def upgrade():
    op.drop_constraint("ck_patroai_staff_role", "patroai_admin_staff", type_="check")
    op.create_check_constraint(
        "ck_patroai_staff_role",
        "patroai_admin_staff",
        "role IN ('analyst','manager','super_admin')",
    )

def downgrade():
    raise RuntimeError("Super admin role may have live accounts; downgrade requires AO-01-reviewed plan")
