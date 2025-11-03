"""Add M-PESA integration tables

Revision ID: add_mpesa_tables
Revises: 
Create Date: 2025-11-03

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = 'add_mpesa_tables'
down_revision = 'c93af01836ed'
branch_labels = None
depends_on = None


def upgrade():
    # M-PESA Configuration table (one per school/tenant)
    op.create_table('mpesa_config',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('school_id', sa.Integer(), nullable=True),
        sa.Column('environment', sa.Enum('sandbox', 'production'), nullable=False, server_default='sandbox'),
        sa.Column('consumer_key', sa.String(255), nullable=True),
        sa.Column('consumer_secret', sa.String(255), nullable=True),
        sa.Column('shortcode', sa.String(20), nullable=True),
        sa.Column('passkey', sa.Text(), nullable=True),
        sa.Column('initiator_name', sa.String(100), nullable=True),
        sa.Column('security_credential', sa.Text(), nullable=True),
        sa.Column('is_enabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('callback_url', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id')
    )
    
    # M-PESA Transactions table
    op.create_table('mpesa_transaction',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('school_id', sa.Integer(), nullable=True),
        sa.Column('merchant_request_id', sa.String(100), nullable=True),
        sa.Column('checkout_request_id', sa.String(100), nullable=True),
        sa.Column('transaction_type', sa.Enum('stk_push', 'b2c', 'c2b'), nullable=False),
        sa.Column('phone_number', sa.String(20), nullable=False),
        sa.Column('amount', sa.Numeric(10, 2), nullable=False),
        sa.Column('account_reference', sa.String(50), nullable=True),
        sa.Column('transaction_desc', sa.String(200), nullable=True),
        sa.Column('mpesa_receipt_number', sa.String(50), nullable=True),
        sa.Column('transaction_date', sa.DateTime(), nullable=True),
        sa.Column('status', sa.Enum('pending', 'success', 'failed', 'cancelled', 'timeout'), nullable=False, server_default='pending'),
        sa.Column('result_code', sa.String(10), nullable=True),
        sa.Column('result_desc', sa.String(255), nullable=True),
        sa.Column('student_id', sa.Integer(), nullable=True),
        sa.Column('payment_id', sa.Integer(), nullable=True),
        sa.Column('callback_received', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('callback_data', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('idx_checkout_request_id', 'checkout_request_id'),
        sa.Index('idx_mpesa_receipt', 'mpesa_receipt_number'),
        sa.Index('idx_phone_number', 'phone_number'),
        sa.Index('idx_status', 'status'),
        sa.Index('idx_student_id', 'student_id')
    )


def downgrade():
    op.drop_table('mpesa_transaction')
    op.drop_table('mpesa_config')
