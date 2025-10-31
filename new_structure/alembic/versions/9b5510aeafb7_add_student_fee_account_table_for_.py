"""Add student_fee_account table for individual fee tracking

Revision ID: 9b5510aeafb7
Revises: 0511af7e51b5
Create Date: 2025-11-01 00:31:09.750065

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '9b5510aeafb7'
down_revision = '0511af7e51b5'
branch_labels = None
depends_on = None


def upgrade():
    # Create student_fee_account table
    op.create_table(
        'student_fee_account',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('fee_structure_id', sa.Integer(), nullable=False),
        sa.Column('academic_year', sa.String(10), nullable=False),
        sa.Column('term', sa.String(20), nullable=False),
        sa.Column('term_id', sa.Integer(), nullable=True),
        sa.Column('total_amount', sa.Numeric(10, 2), nullable=False),
        sa.Column('amount_paid', sa.Numeric(10, 2), default=0.00),
        sa.Column('balance', sa.Numeric(10, 2), nullable=False),
        sa.Column('discount_amount', sa.Numeric(10, 2), default=0.00),
        sa.Column('discount_reason', sa.Text(), nullable=True),
        sa.Column('due_date', sa.Date(), nullable=True),
        sa.Column('status', sa.String(20), default='pending'),
        sa.Column('is_overdue', sa.Boolean(), default=False),
        sa.Column('has_payment_plan', sa.Boolean(), default=False),
        sa.Column('payment_plan_id', sa.Integer(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), default=sa.func.now(), onupdate=sa.func.now()),
        sa.Column('last_payment_date', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['student_id'], ['student.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['fee_structure_id'], ['fee_structure.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['term_id'], ['term.id'], ondelete='SET NULL')
    )
    
    # Create indexes for performance
    op.create_index('ix_student_fee_account_student_term', 'student_fee_account', 
                    ['student_id', 'term', 'academic_year'])
    op.create_index('ix_student_fee_account_status', 'student_fee_account', ['status'])
    op.create_index('ix_student_fee_account_overdue', 'student_fee_account', ['is_overdue', 'due_date'])


def downgrade():
    # Drop indexes
    op.drop_index('ix_student_fee_account_overdue', 'student_fee_account')
    op.drop_index('ix_student_fee_account_status', 'student_fee_account')
    op.drop_index('ix_student_fee_account_student_term', 'student_fee_account')
    
    # Drop table
    op.drop_table('student_fee_account')
