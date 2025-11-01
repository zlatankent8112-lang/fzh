"""
Fee Management Blueprint - Routes for managing fees, payments, and student accounts.
Phase 1: Basic CRUD operations for testing frontend integration.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session
from functools import wraps
from datetime import datetime, date
from decimal import Decimal

from new_structure.extensions import db
from new_structure.models.fee_management import (
    FeeStructure, StudentFeeAccount, PaymentMethod, Payment, PaymentAllocation, StudentCreditBalance
)
from new_structure.models.academic import Student, Grade, Term
from new_structure.services import is_authenticated, get_role

fee_bp = Blueprint('fees', __name__, url_prefix='/fees')


# Authentication decorator for fee management routes
def fee_access_required(f):
    """Decorator to require authenticated user (any role: headteacher, classteacher, teacher)"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_authenticated(session):
            flash('Please log in to access fee management.', 'warning')
            return redirect(url_for('auth.classteacher_login'))
        
        role = get_role(session)
        # Allow all teacher roles to access fee management
        if role not in ('headteacher', 'classteacher', 'teacher'):
            flash('Access denied. Fee management requires staff privileges.', 'error')
            return redirect(url_for('auth.classteacher_login'))
        
        return f(*args, **kwargs)
    return decorated_function


@fee_bp.route('/')
@fee_access_required
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
@fee_access_required
def fee_structures():
    """List all fee structures"""
    fees = FeeStructure.query.filter_by(is_active=True).order_by(
        FeeStructure.academic_year.desc(),
        FeeStructure.term,
        FeeStructure.allocation_priority
    ).all()
    return render_template('fees/structures.html', fees=fees)


@fee_bp.route('/structures/create', methods=['GET', 'POST'])
@fee_access_required
def create_fee_structure():
    """Create a new fee structure"""
    if request.method == 'POST':
        try:
            teacher_id = session.get('teacher_id')
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
                created_by=teacher_id
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
@fee_access_required
def student_fees(student_id):
    """View all fees for a specific student"""
    student = Student.query.get_or_404(student_id)
    
    # Get current term (you can make this dynamic)
    current_term = "Term 1"
    current_year = "2025-2026"
    
    # Get student's fee accounts
    accounts = StudentFeeAccount.query.filter_by(
        student_id=student_id,
        term=current_term,
        academic_year=current_year
    ).join(FeeStructure).order_by(FeeStructure.allocation_priority).all()
    
    total_fees = sum(acc.total_amount for acc in accounts)
    total_paid = sum(acc.amount_paid for acc in accounts)
    balance = sum(acc.balance for acc in accounts)
    
    # Get available credit balances
    from new_structure.models.fee_management import StudentCreditBalance
    credit_balances = StudentCreditBalance.query.filter_by(
        student_id=student_id,
        status='available'
    ).all()
    total_credit = sum(credit.remaining_credit for credit in credit_balances)
    
    # Get siblings (same parent contact - if parent_contact matches)
    siblings = []
    if hasattr(student, 'parent_contact') and student.parent_contact:
        siblings = Student.query.filter(
            Student.id != student_id,
            Student.parent_contact == student.parent_contact
        ).all()
    
    return render_template('fees/student_fees.html',
                         student=student,
                         accounts=accounts,
                         total_fees=total_fees,
                         total_paid=total_paid,
                         balance=balance,
                         credit_balances=credit_balances,
                         total_credit=total_credit,
                         siblings=siblings,
                         current_term=current_term,
                         current_year=current_year)


@fee_bp.route('/payment/record', methods=['GET', 'POST'])
@fee_access_required
def record_payment():
    """Record a new payment"""
    if request.method == 'POST':
        try:
            teacher_id = session.get('teacher_id')
            student_id = int(request.form['student_id'])
            amount = Decimal(request.form['amount'])
            method_id = int(request.form['method_id'])
            reference = request.form.get('reference', '')
            allocation_mode = request.form.get('allocation_mode', 'auto')
            
            # Create payment record first
            payment = Payment(
                student_id=student_id,
                method_id=method_id,
                amount=amount,
                reference=reference,
                allocation_mode=allocation_mode,
                payment_date=datetime.utcnow(),
                recorded_by=teacher_id
            )
            db.session.add(payment)
            db.session.flush()  # Get payment ID
            
            # Handle allocation based on mode
            if allocation_mode == 'auto':
                # Get student's outstanding fees by priority
                accounts = StudentFeeAccount.query.filter(
                    StudentFeeAccount.student_id == student_id,
                    StudentFeeAccount.balance > 0
                ).join(FeeStructure).order_by(FeeStructure.allocation_priority).all()
                
                remaining = amount
                allocated_total = Decimal('0')
                
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
                    allocated_total += allocated
                
                # Handle unallocated amount (overpayment/credit)
                if remaining > 0:
                    # Create credit balance record
                    credit = StudentCreditBalance(
                        student_id=student_id,
                        payment_id=payment.id,
                        credit_amount=remaining,
                        remaining_credit=remaining,
                        status='available',
                        notes=f'Credit from payment {payment.reference or payment.id} - overpayment of KES {remaining}'
                    )
                    db.session.add(credit)
                    flash(f'✅ Payment of KES {amount} recorded. KES {allocated_total} allocated, KES {remaining} kept as credit for future fees.', 'success')
                else:
                    flash(f'✅ Payment of KES {amount} recorded and fully allocated!', 'success')
            
            elif allocation_mode == 'manual':
                # Manual mode - just record payment, admin will allocate later
                flash(f'✅ Payment of KES {amount} recorded. Please allocate manually to specific fees.', 'warning')
            
            # Auto-generate receipt for this payment
            from new_structure.models.fee_management import Receipt
            receipt_number = f"RCP-{datetime.utcnow().year}-{payment.id:05d}"
            receipt = Receipt(
                receipt_number=receipt_number,
                payment_id=payment.id,
                issued_by=teacher_id,
                issued_at=datetime.utcnow()
            )
            db.session.add(receipt)
            
            db.session.commit()
            
            # Show receipt link in flash message
            flash(f'📄 Receipt #{receipt_number} generated. <a href="/fees/receipt/{receipt.id}" style="color: white; text-decoration: underline;">View/Print Receipt</a>', 'info')
            
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
@fee_access_required
def balance_report():
    """Show fee balances for all students"""
    from sqlalchemy import func
    
    # Get filter parameters
    grade_id = request.args.get('grade_id', type=int)
    status = request.args.get('status', '')
    
    # Base query - get all students with fee accounts
    query = db.session.query(
        Student,
        func.sum(StudentFeeAccount.total_amount).label('total_fees'),
        func.sum(StudentFeeAccount.amount_paid).label('total_paid'),
        func.sum(StudentFeeAccount.balance).label('balance')
    ).join(StudentFeeAccount)
    
    # Apply filters
    if grade_id:
        query = query.filter(Student.grade_id == grade_id)
    
    if status:
        if status == 'paid':
            query = query.having(func.sum(StudentFeeAccount.balance) == 0)
        elif status == 'partial':
            query = query.having(
                func.sum(StudentFeeAccount.amount_paid) > 0,
                func.sum(StudentFeeAccount.balance) > 0
            )
        elif status == 'pending':
            query = query.having(func.sum(StudentFeeAccount.amount_paid) == 0)
        elif status == 'overdue':
            # For now, consider any unpaid balance as overdue
            query = query.having(func.sum(StudentFeeAccount.balance) > 0)
    
    # Group by student and order by balance descending
    query = query.group_by(Student.id).order_by(func.sum(StudentFeeAccount.balance).desc())
    
    results = query.all()
    
    # Format results with balance info
    students_with_balances = []
    total_charged = 0
    total_collected = 0
    
    for student, total_fees, total_paid, balance in results:
        balance_info = type('obj', (object,), {
            'total_fees': total_fees or 0,
            'total_paid': total_paid or 0,
            'balance': balance or 0,
            'is_overdue': (balance or 0) > 0  # Simple overdue logic for now
        })()
        students_with_balances.append((student, balance_info))
        total_charged += balance_info.total_fees
        total_collected += balance_info.total_paid
    
    total_outstanding = total_charged - total_collected
    
    # Get all grades for filter
    grades = Grade.query.order_by(Grade.name).all()
    
    return render_template('fees/balance_report.html',
                         students_with_balances=students_with_balances,
                         total_charged=total_charged,
                         total_collected=total_collected,
                         total_outstanding=total_outstanding,
                         grades=grades,
                         selected_grade=request.args.get('grade_id', ''),
                         selected_status=status)


@fee_bp.route('/api/student/<int:student_id>/balance')
@fee_access_required
def api_student_balance(student_id):
    """API endpoint to get student's current balance"""
    accounts = StudentFeeAccount.query.filter_by(student_id=student_id).all()
    
    total_balance = sum(acc.balance for acc in accounts)
    
    return jsonify({
        'student_id': student_id,
        'total_balance': float(total_balance),
        'accounts': [acc.to_dict() for acc in accounts]
    })


@fee_bp.route('/receipt/<int:receipt_id>')
@fee_access_required
def view_receipt(receipt_id):
    """View receipt details"""
    from new_structure.models.fee_management import Receipt
    from new_structure.models.user import Teacher
    
    receipt = Receipt.query.get_or_404(receipt_id)
    payment = receipt.payment
    student = Student.query.get(payment.student_id)
    payment_method = PaymentMethod.query.get(payment.method_id)
    
    # Get accountant/teacher who recorded payment
    accountant = Teacher.query.get(payment.recorded_by) if payment.recorded_by else None
    
    # Get ALL student fee accounts to show complete breakdown
    all_accounts = StudentFeeAccount.query.filter_by(student_id=student.id).all()
    
    # Get payment allocations for THIS payment only
    payment_allocations = PaymentAllocation.query.filter_by(payment_id=payment.id).all()
    payment_alloc_map = {alloc.student_fee_account_id: alloc.amount_allocated for alloc in payment_allocations}
    
    allocation_details = []
    total_fees = Decimal('0')
    total_paid = Decimal('0')
    total_balance = Decimal('0')
    
    for account in all_accounts:
        fee_structure = FeeStructure.query.get(account.fee_structure_id)
        
        # Get amount allocated in THIS payment (0 if not allocated)
        amount_allocated_in_this_payment = payment_alloc_map.get(account.id, Decimal('0'))
        
        allocation_details.append({
            'fee_name': fee_structure.fee_type_name,
            'priority': fee_structure.allocation_priority,
            'total_fee': account.total_amount,
            'amount_allocated': amount_allocated_in_this_payment,
            'balance': account.balance
        })
        
        total_fees += account.total_amount
        total_paid += account.amount_paid
        total_balance += account.balance
    
    # Sort by priority (lower number = higher priority)
    allocation_details.sort(key=lambda x: x['priority'])
    
    return render_template('fees/receipt.html',
                         receipt=receipt,
                         payment=payment,
                         student=student,
                         payment_method=payment_method,
                         accountant=accountant,
                         allocations=allocation_details,
                         total_fees=total_fees,
                         total_paid=total_paid,
                         total_balance=total_balance)


@fee_bp.route('/receipt/<int:receipt_id>/print')
@fee_access_required
def print_receipt(receipt_id):
    """Print-friendly receipt view"""
    from new_structure.models.fee_management import Receipt
    from new_structure.models.user import Teacher
    
    receipt = Receipt.query.get_or_404(receipt_id)
    payment = receipt.payment
    student = Student.query.get(payment.student_id)
    payment_method = PaymentMethod.query.get(payment.method_id)
    
    # Get accountant/teacher who recorded payment
    accountant = Teacher.query.get(payment.recorded_by) if payment.recorded_by else None
    
    # Get ALL student fee accounts to show complete breakdown
    all_accounts = StudentFeeAccount.query.filter_by(student_id=student.id).all()
    
    # Get payment allocations for THIS payment only
    payment_allocations = PaymentAllocation.query.filter_by(payment_id=payment.id).all()
    payment_alloc_map = {alloc.student_fee_account_id: alloc.amount_allocated for alloc in payment_allocations}
    
    allocation_details = []
    total_fees = Decimal('0')
    total_paid = Decimal('0')
    total_balance = Decimal('0')
    
    for account in all_accounts:
        fee_structure = FeeStructure.query.get(account.fee_structure_id)
        
        # Get amount allocated in THIS payment (0 if not allocated)
        amount_allocated_in_this_payment = payment_alloc_map.get(account.id, Decimal('0'))
        
        allocation_details.append({
            'fee_name': fee_structure.fee_type_name,
            'priority': fee_structure.allocation_priority,
            'total_fee': account.total_amount,
            'amount_allocated': amount_allocated_in_this_payment,
            'balance': account.balance
        })
        
        total_fees += account.total_amount
        total_paid += account.amount_paid
        total_balance += account.balance
    
    # Sort by priority (lower number = higher priority)
    allocation_details.sort(key=lambda x: x['priority'])
    
    return render_template('fees/receipt_print.html',
                         receipt=receipt,
                         payment=payment,
                         student=student,
                         payment_method=payment_method,
                         accountant=accountant,
                         allocations=allocation_details,
                         total_fees=total_fees,
                         total_balance=total_balance)
