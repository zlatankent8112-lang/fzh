"""Fee management service: record payments and auto-allocate to student fee accounts."""
from __future__ import annotations
from decimal import Decimal
from typing import List, Dict, Optional
from datetime import datetime

from new_structure.extensions import db
from new_structure.models import (
    Student,
    Term,
    FeeStructure,
    StudentFeeAccount,
    PaymentMethod,
    Payment,
    PaymentAllocation,
)


def _get_or_create_method(name: str = 'Cash') -> PaymentMethod:
    pm = PaymentMethod.query.filter_by(name=name).first()
    if not pm:
        pm = PaymentMethod(name=name, description=f"{name} payment")
        db.session.add(pm)
        db.session.commit()
    return pm


def ensure_fee_accounts_for_student(student: Student, term: str, academic_year: str, due_date=None) -> List[StudentFeeAccount]:
    """Ensure StudentFeeAccount rows exist for all applicable FeeStructure rows for this student."""
    existing = StudentFeeAccount.query.filter_by(student_id=student.id, term=term, academic_year=academic_year).all()
    if existing:
        return existing
    return StudentFeeAccount.create_accounts_for_student(student, term, academic_year, due_date)


def record_payment_auto_allocate(
    *,
    student_id: int,
    term: str,
    academic_year: str,
    amount: Decimal | float | str,
    method_name: str = 'Cash',
    reference: Optional[str] = None,
    recorded_by: Optional[int] = None,
) -> Dict:
    """
    Record a payment and auto-allocate across outstanding StudentFeeAccount rows according to
    FeeStructure.allocation_priority (ascending).

    Returns a dict with payment_id and detailed allocations.
    """
    # Normalize amount to Decimal
    remaining = Decimal(str(amount))
    if remaining <= 0:
        raise ValueError("Payment amount must be positive")

    student = Student.query.get(student_id)
    if not student:
        raise ValueError(f"Student {student_id} not found")

    # Ensure accounts exist
    ensure_fee_accounts_for_student(student, term, academic_year)

    # Payment method
    method = _get_or_create_method(method_name)

    # Create Payment shell
    payment = Payment(
        student_id=student.id,
        method_id=method.id,
        amount=remaining,
        reference=reference,
        allocation_mode='auto',
        recorded_by=recorded_by,
        payment_date=datetime.utcnow(),
    )
    db.session.add(payment)
    db.session.flush()  # get id

    # Fetch accounts with outstanding balance ordered by fee priority
    # Join with FeeStructure to order by allocation_priority
    q = (
        db.session.query(StudentFeeAccount)
        .join(FeeStructure, StudentFeeAccount.fee_structure_id == FeeStructure.id)
        .filter(
            StudentFeeAccount.student_id == student.id,
            StudentFeeAccount.term == term,
            StudentFeeAccount.academic_year == academic_year,
            StudentFeeAccount.balance > 0,
            FeeStructure.is_active == True,
        )
        .order_by(FeeStructure.allocation_priority.asc())
    )

    allocations: List[Dict] = []
    for account in q.all():
        if remaining <= 0:
            break
        alloc = min(remaining, Decimal(str(account.balance)))
        if alloc <= 0:
            continue
        # Create allocation row
        pa = PaymentAllocation(
            payment_id=payment.id,
            student_fee_account_id=account.id,
            amount_allocated=alloc,
            created_at=datetime.utcnow(),
        )
        db.session.add(pa)
        # Update account paid/balance
        account.amount_paid = Decimal(str(account.amount_paid or 0)) + alloc
        account.update_balance()  # also flips status
        account.last_payment_date = datetime.utcnow()
        db.session.flush()

        allocations.append({
            'account_id': account.id,
            'fee_type': account.fee_structure.fee_type_name if account.fee_structure else None,
            'allocated': float(alloc),
            'new_balance': float(account.balance),
        })
        remaining -= alloc

    db.session.commit()

    return {
        'payment_id': payment.id,
        'student_id': student.id,
        'term': term,
        'academic_year': academic_year,
        'amount': float(payment.amount),
        'unallocated': float(remaining) if remaining > 0 else 0.0,
        'allocations': allocations,
    }
