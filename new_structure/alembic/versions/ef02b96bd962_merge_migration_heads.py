"""Merge migration heads

Revision ID: ef02b96bd962
Revises: 874fb2be6ad0, a2_add_auth_security_fields
Create Date: 2025-10-31 22:48:43.213398

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'ef02b96bd962'
down_revision = ('874fb2be6ad0', 'a2_add_auth_security_fields')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
