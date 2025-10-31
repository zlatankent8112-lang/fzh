"""Add fee_invoice, fee_waiver, fee_reminder, receipt, mpesa_transaction tables

Revision ID: f0cde944145e
Revises: 6ebee5af7cda
Create Date: 2025-11-01 01:55:06.219072

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f0cde944145e'
down_revision = '6ebee5af7cda'
branch_labels = None
depends_on = None


def upgrade():
    # fee_invoice
    op.create_table(
        'fee_invoice',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('invoice_number', sa.String(50), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('academic_year', sa.String(10), nullable=False),
        sa.Column('term', sa.String(20), nullable=False),
        sa.Column('total_amount', sa.Numeric(10, 2), nullable=False),
        sa.Column('status', sa.String(20), server_default='issued'),
        sa.Column('due_date', sa.Date(), nullable=True),
        sa.Column('issued_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('invoice_number'),
        sa.ForeignKeyConstraint(['student_id'], ['student.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['teacher.id'], ondelete='SET NULL')
    )

    # fee_waiver
    op.create_table(
        'fee_waiver',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('student_fee_account_id', sa.Integer(), nullable=False),
        sa.Column('amount', sa.Numeric(10, 2), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('approved_by', sa.Integer(), nullable=True),
        sa.Column('approved_at', sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['student_fee_account_id'], ['student_fee_account.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['approved_by'], ['teacher.id'], ondelete='SET NULL')
    )

    # fee_reminder
    op.create_table(
        'fee_reminder',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('channel', sa.String(10), server_default='sms'),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('status', sa.String(20), server_default='sent'),
        sa.Column('invoice_id', sa.Integer(), nullable=True),
        sa.Column('sent_at', sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['student_id'], ['student.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['invoice_id'], ['fee_invoice.id'], ondelete='SET NULL')
    )

    # receipt
    op.create_table(
        'receipt',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('receipt_number', sa.String(50), nullable=False),
        sa.Column('payment_id', sa.Integer(), nullable=False),
        sa.Column('issued_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('issued_by', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('receipt_number'),
        sa.ForeignKeyConstraint(['payment_id'], ['payment.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['issued_by'], ['teacher.id'], ondelete='SET NULL')
    )

    # mpesa_transaction
    op.create_table(
        'mpesa_transaction',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('mpesa_code', sa.String(30), nullable=False),
        sa.Column('phone', sa.String(20), nullable=True),
        sa.Column('payer_name', sa.String(100), nullable=True),
        sa.Column('amount', sa.Numeric(10, 2), nullable=False),
        sa.Column('received_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('status', sa.String(20), server_default='received'),
        sa.Column('raw_payload', sa.Text(), nullable=True),
        sa.Column('payment_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('mpesa_code'),
        sa.ForeignKeyConstraint(['payment_id'], ['payment.id'], ondelete='SET NULL')
    )


def downgrade():
    op.drop_table('mpesa_transaction')
    op.drop_table('receipt')
    op.drop_table('fee_reminder')
    op.drop_table('fee_waiver')
    op.drop_table('fee_invoice')
