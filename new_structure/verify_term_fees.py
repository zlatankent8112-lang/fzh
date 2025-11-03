"""
Quick verification script to check term-specific fee structures
"""
import sys
import os
sys.path.insert(0, 'c:/Users/MKT/desktop/fzh')
os.environ['MYSQL_PASSWORD'] = '2078@lk//K.'

from new_structure.extensions import db
from new_structure.models.fee_management import FeeStructure
from new_structure.config import Config
from flask import Flask

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

def verify_term_specific_fees():
    """Verify term-specific fee structures are set up correctly"""
    with app.app_context():
        print("\n" + "="*80)
        print("TERM-SPECIFIC FEE STRUCTURES VERIFICATION")
        print("="*80)
        
        # Check Pre-Primary fees by term
        for term in ['Term 1', 'Term 2', 'Term 3']:
            print(f"\n📋 {term} - Pre-Primary Fees:")
            print("-" * 80)
            
            fees = FeeStructure.query.filter_by(
                education_level='pre_primary',
                term=term,
                is_active=True,
                academic_year='2025-2026'
            ).order_by(FeeStructure.allocation_priority).all()
            
            total = sum(fee.amount for fee in fees)
            
            for fee in fees:
                print(f"  {fee.allocation_priority}. {fee.fee_type_name:40} KES {fee.amount:>10,.2f}")
            
            print(f"\n  {'TOTAL ' + term + ':':40} KES {total:>10,.2f}")
            print(f"  Number of fees: {len(fees)}")
        
        # Check for any remaining "All Terms" fees
        print("\n" + "="*80)
        print("CHECKING FOR OLD 'ALL TERMS' FEES (should be inactive)")
        print("="*80)
        
        old_fees = FeeStructure.query.filter_by(
            education_level='pre_primary',
            is_active=True
        ).filter(
            (FeeStructure.term == None) | (FeeStructure.term == 'All Terms')
        ).all()
        
        if old_fees:
            print("⚠️  WARNING: Found active 'All Terms' fees:")
            for fee in old_fees:
                print(f"  - ID {fee.id}: {fee.fee_type_name} (should be deactivated)")
        else:
            print("✅ No active 'All Terms' fees found - Good!")
        
        # Summary
        print("\n" + "="*80)
        print("SUMMARY")
        print("="*80)
        
        total_active = FeeStructure.query.filter_by(
            education_level='pre_primary',
            is_active=True
        ).count()
        
        print(f"Total active pre-primary fee structures: {total_active}")
        print(f"Expected: 12 (4 fee types × 3 terms)")
        
        if total_active == 12:
            print("✅ VERIFICATION PASSED - All term-specific fees set up correctly!")
        else:
            print(f"⚠️  WARNING: Expected 12 but found {total_active}")
        
        print("="*80 + "\n")

if __name__ == '__main__':
    verify_term_specific_fees()
