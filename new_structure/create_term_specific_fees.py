"""
Script to create term-specific fee structures for any education level.
Converts current "All Terms" fees into separate Term 1, Term 2, Term 3 structures.
"""
import sys
import os
sys.path.insert(0, 'c:/Users/MKT/desktop/fzh')

# Set database password environment variable BEFORE importing Config
os.environ['MYSQL_PASSWORD'] = '2078@lk//K.'

from new_structure.extensions import db
from new_structure.models.fee_management import FeeStructure
from new_structure.config import Config
from flask import Flask
from decimal import Decimal

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

# Set the education level to process
EDUCATION_LEVEL = 'junior_secondary'  # Change this to: pre_primary, lower_primary, upper_primary, junior_secondary, senior_secondary

LEVEL_NAMES = {
    'pre_primary': 'Pre-Primary',
    'lower_primary': 'Lower Primary', 
    'upper_primary': 'Upper Primary',
    'junior_secondary': 'Junior Secondary',
    'senior_secondary': 'Senior Secondary'
}

def view_current_fees():
    """View current fee structures for the selected education level"""
    with app.app_context():
        fees = FeeStructure.query.filter_by(
            education_level=EDUCATION_LEVEL,
            is_active=True
        ).order_by(FeeStructure.allocation_priority).all()
        
        level_name = LEVEL_NAMES.get(EDUCATION_LEVEL, EDUCATION_LEVEL.replace('_', ' ').title())
        print(f'\n=== Current {level_name} Fee Structures ===')
        print(f'Total: {len(fees)} fee structures\n')
        
        total = Decimal('0')
        for fee in fees:
            print(f'ID: {fee.id} | {fee.fee_type_name}: KES {fee.amount}')
            print(f'  Term: {fee.term or "All Terms"} | Year: {fee.academic_year}')
            print(f'  Priority: {fee.allocation_priority} | Partial Payments: {fee.allow_partial_payment}')
            print('-' * 70)
            total += fee.amount
        
        print(f'\nTotal Fees: KES {total}\n')
        return fees

def create_term_specific_fees():
    """Create separate fee structures for Term 1, Term 2, Term 3"""
    with app.app_context():
        # Get current "All Terms" fees (including both term=None and any existing)
        base_fees = FeeStructure.query.filter_by(
            education_level=EDUCATION_LEVEL,
            is_active=True
        ).filter(
            (FeeStructure.term == None) | (FeeStructure.term == 'All Terms')
        ).all()
        
        level_name = LEVEL_NAMES.get(EDUCATION_LEVEL, EDUCATION_LEVEL.replace('_', ' ').title())
        if not base_fees:
            print(f"❌ No 'All Terms' {level_name} fees found to duplicate!")
            return
        
        print(f"\n📋 Found {len(base_fees)} base fee structures to duplicate")
        print("\n🔄 Creating term-specific fee structures...\n")
        
        # Deactivate old "All Terms" fees
        for fee in base_fees:
            fee.is_active = False
            print(f"⏸️  Deactivated: {fee.fee_type_name} (All Terms)")
        
        # Create new term-specific fees
        terms = ['Term 1', 'Term 2', 'Term 3']
        created_count = 0
        
        for term in terms:
            print(f"\n--- Creating {term} Fees ---")
            for base_fee in base_fees:
                new_fee = FeeStructure(
                    fee_type_name=base_fee.fee_type_name,
                    description=base_fee.description,
                    amount=base_fee.amount,  # Same amount for now (can be edited later)
                    academic_year=base_fee.academic_year,
                    term=term,  # Specific term
                    education_level=base_fee.education_level,
                    is_mandatory=base_fee.is_mandatory,
                    is_boarding=base_fee.is_boarding,
                    frequency=base_fee.frequency,
                    category=base_fee.category,
                    is_refundable=base_fee.is_refundable,
                    allocation_priority=base_fee.allocation_priority,
                    allow_partial_payment=base_fee.allow_partial_payment,
                    is_active=True
                )
                db.session.add(new_fee)
                print(f"  ✅ Created: {new_fee.fee_type_name} - {term} - KES {new_fee.amount}")
                created_count += 1
        
        db.session.commit()
        print(f"\n✅ Successfully created {created_count} term-specific fee structures!")
        print("💡 You can now edit individual term fees to adjust amounts as needed.\n")

if __name__ == '__main__':
    level_name = LEVEL_NAMES.get(EDUCATION_LEVEL, EDUCATION_LEVEL.replace('_', ' ').title())
    print("=" * 70)
    print(f"{level_name} Term-Specific Fee Structure Creator")
    print("=" * 70)
    
    # View current fees
    current_fees = view_current_fees()
    
    if current_fees:
        print("\n" + "=" * 70)
        print("Creating term-specific fees...")
        print("=" * 70)
        
        create_term_specific_fees()
        print("\n" + "=" * 70)
        print("Viewing new term-specific fees:")
        print("=" * 70)
        view_current_fees()
    else:
        print(f"\n⚠️ No {level_name} fee structures found!")
