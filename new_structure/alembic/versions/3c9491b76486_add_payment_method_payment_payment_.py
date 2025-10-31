"""Add payment_method, payment, payment_allocation tables

Revision ID: 3c9491b76486
Revises: 9b5510aeafb7
Create Date: 2025-11-01 00:37:05.272350

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '3c9491b76486'
down_revision = '9b5510aeafb7'
branch_labels = None
depends_on = None


def upgrade():
    # payment_method
    op.create_table(
        'payment_method',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(50), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), default=True),
        sa.Column('config_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_payment_method_name')
    )

    # payment
    op.create_table(
        'payment',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('method_id', sa.Integer(), nullable=True),
        sa.Column('payment_date', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('amount', sa.Numeric(10, 2), nullable=False),
        sa.Column('currency', sa.String(10), server_default='KES'),
        sa.Column('reference', sa.String(100), nullable=True),
        sa.Column('allocation_mode', sa.String(10), server_default='auto'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('recorded_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['student_id'], ['student.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['method_id'], ['payment_method.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['recorded_by'], ['teacher.id'], ondelete='SET NULL')
    )
    op.create_index('ix_payment_student_date', 'payment', ['student_id', 'payment_date'])

    # payment_allocation
    op.create_table(
        'payment_allocation',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('payment_id', sa.Integer(), nullable=False),
        sa.Column('student_fee_account_id', sa.Integer(), nullable=False),
        sa.Column('amount_allocated', sa.Numeric(10, 2), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['payment_id'], ['payment.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['student_fee_account_id'], ['student_fee_account.id'], ondelete='CASCADE')
    )
    op.create_index('ix_payment_allocation_payment', 'payment_allocation', ['payment_id'])
    op.create_index('ix_payment_allocation_account', 'payment_allocation', ['student_fee_account_id'])


def downgrade():
    op.drop_index('ix_payment_allocation_account', 'payment_allocation')
    op.drop_index('ix_payment_allocation_payment', 'payment_allocation')
    op.drop_table('payment_allocation')

    op.drop_index('ix_payment_student_date', 'payment')
    op.drop_table('payment')

    op.drop_table('payment_method')
