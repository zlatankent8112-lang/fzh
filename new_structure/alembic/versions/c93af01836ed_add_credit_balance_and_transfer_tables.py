"""add_credit_balance_and_transfer_tables

Revision ID: c93af01836ed
Revises: f0cde944145e
Create Date: 2025-11-01 13:04:03.036556

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c93af01836ed'
down_revision = 'f0cde944145e'
branch_labels = None
depends_on = None


def upgrade():
    # Create student_credit_balance table
    op.create_table(
        'student_credit_balance',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('payment_id', sa.Integer(), nullable=True),
        sa.Column('credit_amount', sa.Numeric(10, 2), nullable=False),
        sa.Column('remaining_credit', sa.Numeric(10, 2), nullable=False),
        sa.Column('status', sa.String(20), nullable=True, server_default='available'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('applied_at', sa.DateTime(), nullable=True),
        sa.Column('refunded_at', sa.DateTime(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['student_id'], ['student.id'], ),
        sa.ForeignKeyConstraint(['payment_id'], ['payment.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_student_credit_student', 'student_credit_balance', ['student_id'])
    op.create_index('idx_student_credit_status', 'student_credit_balance', ['status'])
    
    # Create credit_transfer table
    op.create_table(
        'credit_transfer',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('from_student_id', sa.Integer(), nullable=False),
        sa.Column('to_student_id', sa.Integer(), nullable=False),
        sa.Column('credit_balance_id', sa.Integer(), nullable=False),
        sa.Column('amount', sa.Numeric(10, 2), nullable=False),
        sa.Column('authorized_by', sa.String(100), nullable=True),
        sa.Column('authorization_date', sa.DateTime(), nullable=True),
        sa.Column('approved_by', sa.Integer(), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(20), nullable=True, server_default='pending'),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['from_student_id'], ['student.id'], ),
        sa.ForeignKeyConstraint(['to_student_id'], ['student.id'], ),
        sa.ForeignKeyConstraint(['credit_balance_id'], ['student_credit_balance.id'], ),
        sa.ForeignKeyConstraint(['approved_by'], ['teacher.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_credit_transfer_from', 'credit_transfer', ['from_student_id'])
    op.create_index('idx_credit_transfer_to', 'credit_transfer', ['to_student_id'])
    op.create_index('idx_credit_transfer_status', 'credit_transfer', ['status'])


def downgrade():
    op.drop_index('idx_credit_transfer_status', table_name='credit_transfer')
    op.drop_index('idx_credit_transfer_to', table_name='credit_transfer')
    op.drop_index('idx_credit_transfer_from', table_name='credit_transfer')
    op.drop_table('credit_transfer')
    
    op.drop_index('idx_student_credit_status', table_name='student_credit_balance')
    op.drop_index('idx_student_credit_student', table_name='student_credit_balance')
    op.drop_table('student_credit_balance')
