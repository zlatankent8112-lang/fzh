"""Run an end-to-end demo: ensure student fee accounts and record a payment with auto-allocation."""
import os
from decimal import Decimal
from dotenv import load_dotenv

load_dotenv()

# Ensure development config
os.environ.setdefault('FLASK_ENV', 'development')

from new_structure import create_app
from new_structure.extensions import db
from new_structure.models import Student, SchoolConfiguration, FeeStructure, StudentFeeAccount
from new_structure.services.fee_management_service import record_payment_auto_allocate, ensure_fee_accounts_for_student


def main(student_id: int | None = None, amount: Decimal | float = 1500):
    app = create_app('development')
    with app.app_context():
        cfg = SchoolConfiguration.get_config()
        term = cfg.current_term
        year = cfg.current_academic_year

        # Pick a student
        student = Student.query.get(student_id) if student_id else Student.query.first()
        if not student:
            print("❌ No student found in database. Please seed students first.")
            return
        print(f"🎯 Using student: {student.id} - {student.name} (Grade {student.grade_id})")
        print(f"📅 Context: {term} / {year}")

        # Ensure accounts
        accounts = ensure_fee_accounts_for_student(student, term, year)
        if not accounts:
            print("❌ No applicable fees found to create accounts. Add FeeStructure rows first.")
            return
        print(f"🧾 Fee accounts available: {len(accounts)}")
        for acc in accounts:
            fs = acc.fee_structure
            print(f"  - {fs.fee_type_name if fs else 'Unknown'} | total={float(acc.total_amount)} paid={float(acc.amount_paid)} balance={float(acc.balance)}")

        # Record payment and auto-allocate
        result = record_payment_auto_allocate(
            student_id=student.id,
            term=term,
            academic_year=year,
            amount=Decimal(str(amount)),
            method_name='Cash',
            reference='DEMO-ALLOC-001',
        )
        print("\n✅ Payment recorded and allocated:")
        print(f"   Payment ID: {result['payment_id']}")
        print(f"   Total Amount: {result['amount']}")
        print(f"   Unallocated: {result['unallocated']}")
        for a in result['allocations']:
            print(f"   -> {a['fee_type']}: allocated={a['allocated']} new_balance={a['new_balance']}")

        # Show final balances
        refreshed = StudentFeeAccount.query.filter_by(student_id=student.id, term=term, academic_year=year).all()
        print("\n📊 Updated balances:")
        for acc in refreshed:
            fs = acc.fee_structure
            print(f"  - {fs.fee_type_name if fs else 'Unknown'} | balance={float(acc.balance)} status={acc.status}")


if __name__ == '__main__':
    import sys
    sid = int(sys.argv[1]) if len(sys.argv) > 1 else None
    amt = Decimal(sys.argv[2]) if len(sys.argv) > 2 else Decimal('1500')
    main(sid, amt)
