"""Add fee_invoice, fee_waiver, fee_reminder, receipt, mpesa_transaction tables

Revision ID: 6ebee5af7cda
Revises: e3b5cdc2488c
Create Date: 2025-11-01 01:23:58.777681

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '6ebee5af7cda'
down_revision = 'e3b5cdc2488c'
branch_labels = None
depends_on = None


def upgrade():
    # Defensive cleanup in case of partial previous attempts
    op.execute("DROP TABLE IF EXISTS `mpesa_transaction`")
    op.execute("DROP TABLE IF EXISTS `receipt`")
    op.execute("DROP TABLE IF EXISTS `fee_reminder`")
    op.execute("DROP TABLE IF EXISTS `fee_waiver`")
    op.execute("DROP TABLE IF EXISTS `fee_invoice`")
    # fee_invoice
    op.create_table(
        'fee_invoice',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('invoice_number', sa.String(50), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('academic_year', sa.String(10), nullable=False),
        sa.Column('term', sa.String(20), nullable=False),
    sa.Column('issue_date', sa.Date(), nullable=True),
        sa.Column('due_date', sa.Date(), nullable=True),
        sa.Column('total_amount', sa.Numeric(10, 2), nullable=False),
        sa.Column('status', sa.String(20), server_default='issued'),
        sa.Column('generated_by', sa.Integer(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('invoice_number', name='uq_fee_invoice_number'),
        sa.ForeignKeyConstraint(['student_id'], ['student.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['generated_by'], ['teacher.id'], ondelete='SET NULL')
    )
    op.create_index('ix_fee_invoice_student_term', 'fee_invoice', ['student_id', 'term', 'academic_year'])

    # fee_waiver
    op.create_table(
        'fee_waiver',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('student_fee_account_id', sa.Integer(), nullable=True),
        sa.Column('amount', sa.Numeric(10, 2), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('approved_by', sa.Integer(), nullable=True),
        sa.Column('approved_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['student_id'], ['student.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['student_fee_account_id'], ['student_fee_account.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['approved_by'], ['teacher.id'], ondelete='SET NULL')
    )
    op.create_index('ix_fee_waiver_student', 'fee_waiver', ['student_id'])

    # fee_reminder
    op.create_table(
        'fee_reminder',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('channel', sa.String(20), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('sent_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('status', sa.String(20), server_default='sent'),
        sa.Column('delivery_ref', sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['student_id'], ['student.id'], ondelete='CASCADE')
    )
    op.create_index('ix_fee_reminder_student', 'fee_reminder', ['student_id'])

    # receipt
    op.create_table(
        'receipt',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('receipt_number', sa.String(50), nullable=False),
        sa.Column('payment_id', sa.Integer(), nullable=False),
        sa.Column('issued_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('issued_by', sa.Integer(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('receipt_number', name='uq_receipt_number'),
        sa.ForeignKeyConstraint(['payment_id'], ['payment.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['issued_by'], ['teacher.id'], ondelete='SET NULL')
    )

    # mpesa_transaction
    op.create_table(
        'mpesa_transaction',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('transaction_id', sa.String(50), nullable=False),
        sa.Column('phone_number', sa.String(20), nullable=True),
        sa.Column('amount', sa.Numeric(10, 2), nullable=False),
        sa.Column('trans_time', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('status', sa.String(20), server_default='received'),
        sa.Column('result_code', sa.String(10), nullable=True),
        sa.Column('raw_payload', sa.Text(), nullable=True),
        sa.Column('payment_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('transaction_id', name='uq_mpesa_txn_id'),
        sa.ForeignKeyConstraint(['payment_id'], ['payment.id'], ondelete='SET NULL')
    )
    op.create_index('ix_mpesa_txn_status', 'mpesa_transaction', ['status'])


def downgrade():
    op.drop_index('ix_mpesa_txn_status', 'mpesa_transaction')
    op.drop_table('mpesa_transaction')

    op.drop_table('receipt')

    op.drop_index('ix_fee_reminder_student', 'fee_reminder')
    op.drop_table('fee_reminder')

    op.drop_index('ix_fee_waiver_student', 'fee_waiver')
    op.drop_table('fee_waiver')

    op.drop_index('ix_fee_invoice_student_term', 'fee_invoice')
    op.drop_table('fee_invoice')
