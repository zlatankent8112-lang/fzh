"""
Fee Management Blueprint - Routes for managing fees, payments, and student accounts.
Phase 1: Basic CRUD operations for testing frontend integration.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from datetime import datetime, date
from decimal import Decimal

from new_structure.extensions import db
from new_structure.models.fee_management import (
    FeeStructure, StudentFeeAccount, PaymentMethod, Payment, PaymentAllocation
)
from new_structure.models.academic import Student, Grade, Term

fee_bp = Blueprint('fees', __name__, url_prefix='/fees')


@fee_bp.route('/')
@login_required
def index():
    """Dashboard showing fee management overview"""
    total_fees = FeeStructure.query.filter_by(is_active=True).count()
    total_students = Student.query.count()
    total_payments = Payment.query.count()
    
    # Get recent payments
    recent_payments = Payment.query.order_by(Payment.payment_date.desc()).limit(5).all()
    
    return render_template('fees/index.html',
                         total_fees=total_fees,
                         total_students=total_students,
                         total_payments=total_payments,
                         recent_payments=recent_payments)


@fee_bp.route('/structures')
@login_required
def fee_structures():
    """List all fee structures"""
    fees = FeeStructure.query.filter_by(is_active=True).order_by(
        FeeStructure.academic_year.desc(),
        FeeStructure.term,
        FeeStructure.allocation_priority
    ).all()
    return render_template('fees/structures.html', fees=fees)


@fee_bp.route('/structures/create', methods=['GET', 'POST'])
@login_required
def create_fee_structure():
    """Create a new fee structure"""
    if request.method == 'POST':
        try:
            fee = FeeStructure(
                fee_type_name=request.form['fee_type_name'],
                description=request.form.get('description'),
                amount=Decimal(request.form['amount']),
                academic_year=request.form['academic_year'],
                term=request.form['term'],
                grade_id=request.form.get('grade_id') or None,
                allocation_priority=int(request.form.get('allocation_priority', 1)),
                allow_partial_payment=request.form.get('allow_partial_payment') == 'on',
                is_mandatory=request.form.get('is_mandatory') == 'on',
                created_by=current_user.id if hasattr(current_user, 'id') else None
            )
            db.session.add(fee)
            db.session.commit()
            flash(f'Fee structure "{fee.fee_type_name}" created successfully!', 'success')
            return redirect(url_for('fees.fee_structures'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating fee structure: {str(e)}', 'error')
    
    grades = Grade.query.all()
    return render_template('fees/create_structure.html', grades=grades)


@fee_bp.route('/student/<int:student_id>')
@login_required
def student_fees(student_id):
    """View all fees for a specific student"""
    student = Student.query.get_or_404(student_id)
    
    # Get current term (you can make this dynamic)
    current_term = "Term 1"
    current_year = "2025"
    
    # Get student's fee accounts
    accounts = StudentFeeAccount.query.filter_by(
        student_id=student_id,
        term=current_term,
        academic_year=current_year
    ).join(FeeStructure).order_by(FeeStructure.allocation_priority).all()
    
    total_fees = sum(acc.total_amount for acc in accounts)
    total_paid = sum(acc.amount_paid for acc in accounts)
    balance = sum(acc.balance for acc in accounts)
    
    return render_template('fees/student_fees.html',
                         student=student,
                         accounts=accounts,
                         total_fees=total_fees,
                         total_paid=total_paid,
                         balance=balance,
                         current_term=current_term,
                         current_year=current_year)


@fee_bp.route('/payment/record', methods=['GET', 'POST'])
@login_required
def record_payment():
    """Record a new payment"""
    if request.method == 'POST':
        try:
            student_id = int(request.form['student_id'])
            amount = Decimal(request.form['amount'])
            method_id = int(request.form['method_id'])
            reference = request.form.get('reference', '')
            allocation_mode = request.form.get('allocation_mode', 'auto')
            
            # Create payment
            payment = Payment(
                student_id=student_id,
                method_id=method_id,
                amount=amount,
                reference=reference,
                allocation_mode=allocation_mode,
                payment_date=datetime.utcnow(),
                recorded_by=current_user.id if hasattr(current_user, 'id') else None
            )
            db.session.add(payment)
            db.session.flush()  # Get payment ID
            
            # Auto-allocate if mode is auto
            if allocation_mode == 'auto':
                # Get student's outstanding fees by priority
                accounts = StudentFeeAccount.query.filter(
                    StudentFeeAccount.student_id == student_id,
                    StudentFeeAccount.balance > 0
                ).join(FeeStructure).order_by(FeeStructure.allocation_priority).all()
                
                remaining = amount
                for acc in accounts:
                    if remaining <= 0:
                        break
                    
                    allocated = min(remaining, acc.balance)
                    
                    # Create allocation
                    allocation = PaymentAllocation(
                        payment_id=payment.id,
                        student_fee_account_id=acc.id,
                        amount_allocated=allocated
                    )
                    db.session.add(allocation)
                    
                    # Update account
                    acc.amount_paid += allocated
                    acc.balance -= allocated
                    if acc.balance <= 0:
                        acc.status = 'paid'
                    elif acc.amount_paid > 0:
                        acc.status = 'partial'
                    acc.last_payment_date = datetime.utcnow()
                    
                    remaining -= allocated
            
            db.session.commit()
            flash(f'Payment of KES {amount} recorded successfully!', 'success')
            return redirect(url_for('fees.student_fees', student_id=student_id))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error recording payment: {str(e)}', 'error')
    
    # GET request - show form
    students = Student.query.order_by(Student.name).all()
    payment_methods = PaymentMethod.query.filter_by(is_active=True).all()
    
    return render_template('fees/record_payment.html',
                         students=students,
                         payment_methods=payment_methods)


@fee_bp.route('/reports/balances')
@login_required
def balance_report():
    """Show fee balances for all students"""
    # Get all students with outstanding balances
    students_with_balances = db.session.query(
        Student,
        db.func.sum(StudentFeeAccount.balance).label('total_balance')
    ).join(StudentFeeAccount).filter(
        StudentFeeAccount.balance > 0
    ).group_by(Student.id).all()
    
    return render_template('fees/balance_report.html',
                         students_with_balances=students_with_balances)


@fee_bp.route('/api/student/<int:student_id>/balance')
@login_required
def api_student_balance(student_id):
    """API endpoint to get student's current balance"""
    accounts = StudentFeeAccount.query.filter_by(student_id=student_id).all()
    
    total_balance = sum(acc.balance for acc in accounts)
    
    return jsonify({
        'student_id': student_id,
        'total_balance': float(total_balance),
        'accounts': [acc.to_dict() for acc in accounts]
    })
