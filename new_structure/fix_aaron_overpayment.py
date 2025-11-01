"""
Fix Aaron's overpayment by creating a credit balance record.
This script retroactively creates credit for existing overpayments.
"""
import os
import sys
from decimal import Decimal
from datetime import datetime

# Add project to path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.insert(0, parent_dir)
sys.path.insert(0, current_dir)

from dotenv import load_dotenv
load_dotenv()

from __init__ import create_app
from extensions import db
from models.fee_management import StudentFeeAccount, Payment, StudentCreditBalance
from models.academic import Student

app = create_app()

with app.app_context():
    print("🔍 Checking Aaron's payment status...")
    
    # Get Aaron (student_id = 115)
    student = Student.query.get(115)
    if not student:
        print("❌ Aaron not found!")
        sys.exit(1)
    
    print(f"✅ Found: {student.name} (ADM: {student.admission_number})")
    
    # Calculate totals
    total_fees = db.session.query(
        db.func.sum(StudentFeeAccount.total_amount)
    ).filter(
        StudentFeeAccount.student_id == 115,
        StudentFeeAccount.academic_year == '2025-2026'
    ).scalar() or Decimal('0')
    
    total_paid = db.session.query(
        db.func.sum(Payment.amount)
    ).filter(Payment.student_id == 115).scalar() or Decimal('0')
    
    print(f"\n💰 Financial Summary:")
    print(f"   Total Fees:  KES {total_fees:,.2f}")
    print(f"   Total Paid:  KES {total_paid:,.2f}")
    print(f"   Difference:  KES {(total_paid - total_fees):,.2f}")
    
    overpayment = total_paid - total_fees
    
    if overpayment <= 0:
        print(f"\n✅ No overpayment found. Balance: KES {abs(overpayment):,.2f}")
        sys.exit(0)
    
    print(f"\n⚠️  Overpayment detected: KES {overpayment:,.2f}")
    
    # Check if credit already exists
    existing_credit = StudentCreditBalance.query.filter_by(
        student_id=115,
        status='available'
    ).first()
    
    if existing_credit:
        print(f"\n✅ Credit already exists: KES {existing_credit.remaining_credit}")
        print("   No action needed.")
        sys.exit(0)
    
    # Create credit balance
    print(f"\n📝 Creating credit balance...")
    credit = StudentCreditBalance(
        student_id=115,
        payment_id=None,  # Multiple payments contributed
        credit_amount=overpayment,
        remaining_credit=overpayment,
        status='available',
        notes=f'Retroactive credit for overpayment. Total paid: KES {total_paid}, Total fees: KES {total_fees}'
    )
    
    db.session.add(credit)
    db.session.commit()
    
    print(f"✅ Credit balance created successfully!")
    print(f"\n📊 Credit Details:")
    print(f"   Credit ID:        {credit.id}")
    print(f"   Amount:           KES {credit.credit_amount:,.2f}")
    print(f"   Remaining:        KES {credit.remaining_credit:,.2f}")
    print(f"   Status:           {credit.status}")
    print(f"   Created:          {credit.created_at}")
    
    print(f"\n💡 Next Steps:")
    print(f"   - Credit can be applied to future term fees")
    print(f"   - Can be transferred to sibling (if any)")
    print(f"   - Can be refunded to parent")
    print(f"\n✅ Done!")
