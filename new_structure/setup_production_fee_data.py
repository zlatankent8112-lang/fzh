"""
Production Fee Data Setup Script
=================================
This script sets up the fee management system with production-ready data:
1. Creates payment methods (Cash, M-PESA, Bank Transfer)
2. Creates fee structures for Term 1, 2025-2026
3. Assigns fees to all students in the system

Run this script once to initialize the fee management system.
"""

import os
import sys
import json
from datetime import datetime, date
from decimal import Decimal
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add current directory to path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.insert(0, parent_dir)
sys.path.insert(0, current_dir)

from new_structure import create_app
from new_structure.extensions import db
from new_structure.models.fee_management import (
    FeeStructure, StudentFeeAccount, PaymentMethod
)
from new_structure.models.academic import Student, Grade

def setup_payment_methods():
    """Create standard payment methods"""
    print("\n📱 Setting up Payment Methods...")
    
    methods = [
        {
            'name': 'Cash',
            'code': 'CASH',
            'description': 'Cash payment at school office',
            'requires_reference': False,
            'is_active': True,
            'configuration': {
                'accepted_currencies': ['KES'],
                'requires_receipt': True
            }
        },
        {
            'name': 'M-PESA',
            'code': 'MPESA',
            'description': 'M-PESA mobile money payment',
            'requires_reference': True,
            'is_active': True,
            'configuration': {
                'paybill_number': '400200',  # Update with your actual paybill
                'account_number': 'HILLVIEW',  # Update with your account
                'requires_confirmation': True
            }
        },
        {
            'name': 'Bank Transfer',
            'code': 'BANK',
            'description': 'Direct bank deposit or transfer',
            'requires_reference': True,
            'is_active': True,
            'configuration': {
                'bank_name': 'Equity Bank',  # Update with your bank
                'account_number': '0123456789',  # Update with your account
                'account_name': 'Hillview School',
                'branch': 'Kiambu Road Branch'
            }
        }
    ]
    
    created = 0
    for method_data in methods:
        existing = PaymentMethod.query.filter_by(name=method_data['name']).first()
        if not existing:
            # Remove fields not in model and map correctly
            method = PaymentMethod(
                name=method_data['name'],
                description=method_data['description'],
                is_active=method_data['is_active'],
                config_json=json.dumps(method_data['configuration'])
            )
            db.session.add(method)
            created += 1
            print(f"   ✅ Created: {method_data['name']}")
        else:
            print(f"   ⏭️  Exists: {method_data['name']}")
    
    db.session.commit()
    print(f"✅ Payment Methods: {created} created, {len(methods) - created} already exist")
    return PaymentMethod.query.all()


def setup_fee_structures():
    """Create fee structures for current academic year"""
    print("\n💰 Setting up Fee Structures...")
    
    # Current academic year and term
    academic_year = "2025-2026"
    term = "Term 1"
    
    # Fee structures - Customize these amounts for your school!
    fee_structures = [
        {
            'fee_type_name': 'Tuition Fee',
            'description': 'Core academic instruction and curriculum delivery',
            'amount': Decimal('15000.00'),
            'academic_year': academic_year,
            'term': term,
            'allocation_priority': 1,  # Highest priority
            'allow_partial_payment': True,
            'is_mandatory': True,
            'grade_id': None  # Applies to all grades
        },
        {
            'fee_type_name': 'Transport Fee',
            'description': 'School bus transportation service',
            'amount': Decimal('5000.00'),
            'academic_year': academic_year,
            'term': term,
            'allocation_priority': 2,
            'allow_partial_payment': True,
            'is_mandatory': False,
            'grade_id': None
        },
        {
            'fee_type_name': 'Lunch Program',
            'description': 'Daily nutritious lunch meals',
            'amount': Decimal('3000.00'),
            'academic_year': academic_year,
            'term': term,
            'allocation_priority': 3,
            'allow_partial_payment': True,
            'is_mandatory': False,
            'grade_id': None
        },
        {
            'fee_type_name': 'Activity Fee',
            'description': 'Sports, clubs, and extracurricular activities',
            'amount': Decimal('2000.00'),
            'academic_year': academic_year,
            'term': term,
            'allocation_priority': 4,
            'allow_partial_payment': True,
            'is_mandatory': False,
            'grade_id': None
        },
        {
            'fee_type_name': 'ICT/Computer Lab Fee',
            'description': 'Computer lab access and technology resources',
            'amount': Decimal('1500.00'),
            'academic_year': academic_year,
            'term': term,
            'allocation_priority': 5,
            'allow_partial_payment': False,
            'is_mandatory': False,
            'grade_id': None
        }
    ]
    
    created = 0
    for fee_data in fee_structures:
        # Check if this fee structure already exists
        existing = FeeStructure.query.filter_by(
            fee_type_name=fee_data['fee_type_name'],
            academic_year=academic_year,
            term=term
        ).first()
        
        if not existing:
            fee = FeeStructure(**fee_data)
            db.session.add(fee)
            created += 1
            print(f"   ✅ Created: {fee_data['fee_type_name']} - KES {fee_data['amount']:,.2f} (Priority {fee_data['allocation_priority']})")
        else:
            print(f"   ⏭️  Exists: {fee_data['fee_type_name']} - KES {existing.amount:,.2f}")
    
    db.session.commit()
    print(f"✅ Fee Structures: {created} created, {len(fee_structures) - created} already exist")
    return FeeStructure.query.filter_by(academic_year=academic_year, term=term).all()


def assign_fees_to_students(fee_structures):
    """Assign fee structures to all active students"""
    print("\n👥 Assigning Fees to Students...")
    
    # Get all active students
    students = Student.query.all()
    
    if not students:
        print("   ⚠️  No students found in the database!")
        return 0
    
    print(f"   Found {len(students)} students")
    
    assigned_count = 0
    skipped_count = 0
    
    for student in students:
        for fee_structure in fee_structures:
            # Check if this student already has this fee assigned
            existing = StudentFeeAccount.query.filter_by(
                student_id=student.id,
                fee_structure_id=fee_structure.id
            ).first()
            
            if not existing:
                # Create fee account for this student
                account = StudentFeeAccount(
                    student_id=student.id,
                    fee_structure_id=fee_structure.id,
                    total_amount=fee_structure.amount,
                    amount_paid=Decimal('0.00'),
                    balance=fee_structure.amount,
                    status='pending',
                    academic_year=fee_structure.academic_year,
                    term=fee_structure.term,
                )
                db.session.add(account)
                assigned_count += 1
            else:
                skipped_count += 1
    
    db.session.commit()
    
    print(f"✅ Fee Assignment: {assigned_count} new accounts created, {skipped_count} already exist")
    print(f"   📊 Total fee accounts: {StudentFeeAccount.query.count()}")
    return assigned_count


def calculate_summary_statistics():
    """Calculate and display summary statistics"""
    print("\n📊 Summary Statistics:")
    print("=" * 70)
    
    # Total fees charged
    total_charged = db.session.query(
        db.func.sum(StudentFeeAccount.total_amount)
    ).scalar() or Decimal('0.00')
    
    # Total collected
    total_collected = db.session.query(
        db.func.sum(StudentFeeAccount.amount_paid)
    ).scalar() or Decimal('0.00')
    
    # Outstanding balance
    outstanding = total_charged - total_collected
    
    # Number of students with fees
    students_with_fees = db.session.query(
        db.func.count(db.func.distinct(StudentFeeAccount.student_id))
    ).scalar() or 0
    
    # Fee structures
    fee_count = FeeStructure.query.filter_by(is_active=True).count()
    
    # Payment methods
    payment_method_count = PaymentMethod.query.filter_by(is_active=True).count()
    
    print(f"Students with Fee Accounts: {students_with_fees}")
    print(f"Active Fee Structures: {fee_count}")
    print(f"Active Payment Methods: {payment_method_count}")
    print(f"\n💰 Financial Summary:")
    print(f"   Total Fees Charged:    KES {total_charged:>15,.2f}")
    print(f"   Amount Collected:      KES {total_collected:>15,.2f}")
    print(f"   Outstanding Balance:   KES {outstanding:>15,.2f}")
    print(f"   Collection Rate:       {(total_collected / total_charged * 100) if total_charged > 0 else 0:>15.1f}%")
    print("=" * 70)


def main():
    """Main setup function"""
    print("\n" + "=" * 70)
    print("🏫 HILLVIEW SCHOOL - FEE MANAGEMENT SYSTEM")
    print("   Production Data Setup")
    print("=" * 70)
    
    # Create Flask app context
    app = create_app('development')
    
    with app.app_context():
        try:
            # Step 1: Setup payment methods
            payment_methods = setup_payment_methods()
            
            # Step 2: Setup fee structures
            fee_structures = setup_fee_structures()
            
            # Step 3: Assign fees to all students
            assigned = assign_fees_to_students(fee_structures)
            
            # Step 4: Display summary
            calculate_summary_statistics()
            
            print("\n✅ SETUP COMPLETE!")
            print("\n🎯 Next Steps:")
            print("   1. Login to the system at: http://127.0.0.1:8080")
            print("   2. Navigate to: http://127.0.0.1:8080/fees/")
            print("   3. Start recording payments!")
            print("   4. View balance report: http://127.0.0.1:8080/fees/reports/balances")
            print("\n" + "=" * 70)
            
        except Exception as e:
            print(f"\n❌ ERROR: {str(e)}")
            import traceback
            traceback.print_exc()
            db.session.rollback()
            return 1
    
    return 0


if __name__ == '__main__':
    exit(main())
