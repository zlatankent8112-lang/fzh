"""Add fee_structure table with hybrid allocation support

Revision ID: 0511af7e51b5
Revises: ef02b96bd962
Create Date: 2025-10-31 22:48:54.637109

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0511af7e51b5'
down_revision = 'ef02b96bd962'
branch_labels = None
depends_on = None


def upgrade():
    # Create fee_structure table
    op.create_table(
        'fee_structure',
        sa.Column('id', sa.Integer(), nullable=False),
        
        # Fee Type Information
        sa.Column('fee_type_name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('amount', sa.Numeric(precision=10, scale=2), nullable=False),
        
        # Academic Context
        sa.Column('academic_year', sa.String(length=10), nullable=False),
        sa.Column('term', sa.String(length=20), nullable=True),
        sa.Column('term_id', sa.Integer(), nullable=True),
        
        # Grade/Stream Linkage
        sa.Column('grade_id', sa.Integer(), nullable=True),
        sa.Column('education_level', sa.String(length=50), nullable=True),
        sa.Column('stream_id', sa.Integer(), nullable=True),
        
        # Fee Characteristics
        sa.Column('is_mandatory', sa.Boolean(), nullable=True, default=True),
        sa.Column('is_boarding', sa.Boolean(), nullable=True, default=False),
        sa.Column('frequency', sa.String(length=20), nullable=True, default='termly'),
        
        # Payment Allocation Settings (Hybrid Approach)
        sa.Column('allocation_priority', sa.Integer(), nullable=True, default=1),
        sa.Column('allow_partial_payment', sa.Boolean(), nullable=True, default=True),
        
        # Status
        sa.Column('is_active', sa.Boolean(), nullable=True, default=True),
        
        # Timestamps
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        
        # Primary Key
        sa.PrimaryKeyConstraint('id'),
        
        # Foreign Keys
        sa.ForeignKeyConstraint(['grade_id'], ['grade.id'], ),
        sa.ForeignKeyConstraint(['stream_id'], ['stream.id'], ),
        sa.ForeignKeyConstraint(['term_id'], ['term.id'], ),
        sa.ForeignKeyConstraint(['created_by'], ['teacher.id'], )
    )
    
    # Create indexes for better query performance
    op.create_index('ix_fee_structure_grade_term_year', 'fee_structure', 
                    ['grade_id', 'term', 'academic_year'], unique=False)
    op.create_index('ix_fee_structure_active', 'fee_structure', 
                    ['is_active'], unique=False)
    op.create_index('ix_fee_structure_priority', 'fee_structure', 
                    ['allocation_priority'], unique=False)


def downgrade():
    # Drop indexes
    op.drop_index('ix_fee_structure_priority', table_name='fee_structure')
    op.drop_index('ix_fee_structure_active', table_name='fee_structure')
    op.drop_index('ix_fee_structure_grade_term_year', table_name='fee_structure')
    
    # Drop table
    op.drop_table('fee_structure')
