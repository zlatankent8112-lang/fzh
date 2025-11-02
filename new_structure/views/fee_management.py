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
from new_structure.models.academic import Student, Grade, Term, Stream
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
    # Get educational level filter
    current_level = request.args.get('education_level', '')
    
    query = FeeStructure.query
    if current_level:
        query = query.filter_by(education_level=current_level)
    
    fees = query.order_by(
        FeeStructure.academic_year.desc(),
        FeeStructure.term,
        FeeStructure.allocation_priority
    ).all()
    
    # Get unique education levels for stats
    education_levels = db.session.query(FeeStructure.education_level)\
        .distinct()\
        .filter(FeeStructure.education_level.isnot(None))\
        .all()
    education_levels = [level[0] for level in education_levels]
    
    return render_template('fees/structures.html', 
                         fees=fees, 
                         current_level=current_level,
                         education_levels=education_levels)


@fee_bp.route('/structures/create', methods=['GET', 'POST'])
@fee_access_required
def create_fee_structure():
    """Create a new fee structure"""
    if request.method == 'POST':
        try:
            teacher_id = session.get('teacher_id')
            
            # Check for duplicates
            fee_name = request.form['fee_type_name'].strip()
            academic_year = request.form['academic_year']
            term = request.form.get('term') or None
            education_level = request.form.get('education_level')
            frequency = request.form.get('frequency', 'per_term')
            grade_id = request.form.get('grade_id') or None
            
            existing = FeeStructure.query.filter_by(
                fee_type_name=fee_name,
                academic_year=academic_year,
                term=term,
                education_level=education_level,
                frequency=frequency,
                grade_id=grade_id
            ).first()
            
            if existing:
                flash(f'⚠️ A fee structure with this name, academic year, term, frequency, and education level already exists!', 'warning')
                grades = Grade.query.order_by(Grade.name).all()
                return render_template('fees/create_structure.html', grades=grades, fee=None, form_data=request.form)
            
            # Parse applies_to_grades if provided
            applies_to_grades = request.form.get('applies_to_grades', '').strip()
            if applies_to_grades:
                # Convert comma-separated string to JSON array
                import json
                grade_list = [int(g.strip()) for g in applies_to_grades.split(',') if g.strip().isdigit()]
                applies_to_grades_json = json.dumps(grade_list) if grade_list else None
            else:
                applies_to_grades_json = None
            
            fee = FeeStructure(
                fee_type_name=request.form['fee_type_name'],
                description=request.form.get('description'),
                amount=Decimal(request.form['amount']),
                academic_year=request.form['academic_year'],
                term=request.form.get('term') or None,
                grade_id=request.form.get('grade_id') or None,
                education_level=request.form.get('education_level'),
                category=request.form.get('category'),
                frequency=request.form.get('frequency', 'per_term'),
                applies_to_grades=applies_to_grades_json,
                allocation_priority=int(request.form.get('allocation_priority', 1)),
                allow_partial_payment=request.form.get('allow_partial_payment') == 'on',
                is_mandatory=request.form.get('is_mandatory') == 'on',
                is_refundable=request.form.get('is_refundable') == 'on',
                is_active=request.form.get('is_active') == 'on',
                created_by=teacher_id
            )
            db.session.add(fee)
            db.session.commit()
            flash(f'✅ Fee structure "{fee.fee_type_name}" created successfully!', 'success')
            return redirect(url_for('fees.fee_structures'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating fee structure: {str(e)}', 'danger')
    
    # Pre-fill education level from query parameter if provided
    education_level_param = request.args.get('education_level')
    
    grades = Grade.query.order_by(Grade.name).all()
    return render_template('fees/create_structure.html', grades=grades, fee=None, 
                          prefill_education_level=education_level_param)


@fee_bp.route('/structures/bulk-create', methods=['GET', 'POST'])
@fee_access_required
def bulk_create_fee_structure():
    """Bulk create multiple fee structures at once"""
    if request.method == 'POST':
        try:
            import json
            teacher_id = session.get('teacher_id')
            
            # Get common settings
            academic_year = request.form['academic_year']
            term = request.form.get('term') or None
            frequency = request.form.get('frequency', 'per_term')
            education_level = request.form.get('education_level')
            grade_id = request.form.get('grade_id') or None
            applies_to_grades_str = request.form.get('applies_to_grades', '').strip()
            
            # Parse applies_to_grades if provided
            applies_to_grades_json = None
            if applies_to_grades_str:
                grade_list = [int(g.strip()) for g in applies_to_grades_str.split(',') if g.strip().isdigit()]
                applies_to_grades_json = json.dumps(grade_list) if grade_list else None
            
            # Get all fee entries from form
            fees_created = 0
            fee_names = []
            
            # Parse the fees array from form data
            fee_data = {}
            for key in request.form.keys():
                if key.startswith('fees['):
                    # Extract fee index and field name: fees[1][name] -> index=1, field=name
                    import re
                    match = re.match(r'fees\[(\d+)\]\[(\w+)\]', key)
                    if match:
                        index = match.group(1)
                        field = match.group(2)
                        if index not in fee_data:
                            fee_data[index] = {}
                        fee_data[index][field] = request.form[key]
            
            # Also check for checkbox fields (they won't appear if unchecked)
            for index in fee_data.keys():
                fee_data[index]['mandatory'] = f'fees[{index}][mandatory]' in request.form
                fee_data[index]['refundable'] = f'fees[{index}][refundable]' in request.form
                fee_data[index]['optional'] = f'fees[{index}][optional]' in request.form
            
            # Create each fee structure
            duplicates_found = []
            for index, fee_info in fee_data.items():
                if 'name' in fee_info and 'amount' in fee_info:
                    # Use individual fee settings or fall back to common settings
                    fee_term = fee_info.get('term') if fee_info.get('term') else term
                    fee_frequency = fee_info.get('frequency') if fee_info.get('frequency') else frequency
                    
                    # Check for duplicates
                    fee_name = fee_info['name'].strip()
                    existing = FeeStructure.query.filter_by(
                        fee_type_name=fee_name,
                        academic_year=academic_year,
                        term=fee_term,
                        education_level=education_level,
                        frequency=fee_frequency,
                        grade_id=grade_id
                    ).first()
                    
                    if existing:
                        duplicates_found.append(fee_name)
                        continue  # Skip this duplicate
                    
                    # If optional is checked, it's not mandatory
                    is_optional = fee_info.get('optional', False)
                    is_mandatory = fee_info.get('mandatory', True) and not is_optional
                    
                    fee = FeeStructure(
                        fee_type_name=fee_info['name'],
                        description=fee_info.get('description'),
                        amount=Decimal(fee_info['amount']),
                        academic_year=academic_year,
                        term=fee_term,  # Use individual or common
                        grade_id=grade_id,
                        education_level=education_level,
                        category=fee_info.get('category', 'tuition'),
                        frequency=fee_frequency,  # Use individual or common
                        applies_to_grades=applies_to_grades_json,
                        allocation_priority=int(fee_info.get('priority', index)),
                        allow_partial_payment=True,  # Default to true
                        is_mandatory=is_mandatory,  # False if optional
                        is_refundable=fee_info.get('refundable', False),
                        is_active=True,
                        created_by=teacher_id
                    )
                    db.session.add(fee)
                    fees_created += 1
                    fee_names.append(fee_info['name'])
            
            db.session.commit()
            
            # Create success message
            if duplicates_found:
                flash(f'⚠️ Skipped {len(duplicates_found)} duplicate(s): {", ".join(duplicates_found)}', 'warning')
            
            if fees_created == 1:
                flash(f'✅ Created {fees_created} fee structure: {fee_names[0]}', 'success')
            elif fees_created > 1:
                flash(f'✅ Successfully created {fees_created} fee structures: {", ".join(fee_names)}', 'success')
            elif not duplicates_found:
                flash('❌ No fee structures were created', 'danger')
            
            return redirect(url_for('fees.fee_structures'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating fee structures: {str(e)}', 'danger')
    
    # Pre-fill education level from query parameter if provided
    education_level_param = request.args.get('education_level')
    
    grades = Grade.query.order_by(Grade.name).all()
    return render_template('fees/bulk_create_structure.html', grades=grades, 
                          prefill_education_level=education_level_param)


@fee_bp.route('/structures/<int:fee_id>/edit', methods=['GET', 'POST'])
@fee_access_required
def edit_fee_structure(fee_id):
    """Edit an existing fee structure"""
    import json
    fee = FeeStructure.query.get_or_404(fee_id)
    
    # Parse applies_to_grades JSON to comma-separated string for editing
    applies_to_grades_str = ''
    if fee.applies_to_grades:
        try:
            grade_list = json.loads(fee.applies_to_grades)
            applies_to_grades_str = ','.join(map(str, grade_list))
        except:
            applies_to_grades_str = ''
    
    if request.method == 'POST':
        try:
            # Parse applies_to_grades if provided
            applies_to_grades = request.form.get('applies_to_grades', '').strip()
            if applies_to_grades:
                import json
                grade_list = [int(g.strip()) for g in applies_to_grades.split(',') if g.strip().isdigit()]
                fee.applies_to_grades = json.dumps(grade_list) if grade_list else None
            else:
                fee.applies_to_grades = None
            
            fee.fee_type_name = request.form['fee_type_name']
            fee.description = request.form.get('description')
            fee.amount = Decimal(request.form['amount'])
            fee.academic_year = request.form['academic_year']
            fee.term = request.form.get('term') or None
            fee.grade_id = request.form.get('grade_id') or None
            fee.education_level = request.form.get('education_level')
            fee.category = request.form.get('category')
            fee.frequency = request.form.get('frequency', 'per_term')
            fee.allocation_priority = int(request.form.get('allocation_priority', 1))
            fee.allow_partial_payment = request.form.get('allow_partial_payment') == 'on'
            fee.is_mandatory = request.form.get('is_mandatory') == 'on'
            fee.is_refundable = request.form.get('is_refundable') == 'on'
            fee.is_active = request.form.get('is_active') == 'on'
            
            db.session.commit()
            flash(f'✅ Fee structure "{fee.fee_type_name}" updated successfully!', 'success')
            return redirect(url_for('fees.fee_structures'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating fee structure: {str(e)}', 'danger')
    
    grades = Grade.query.order_by(Grade.name).all()
    return render_template('fees/create_structure.html', grades=grades, fee=fee, 
                          applies_to_grades_str=applies_to_grades_str)


@fee_bp.route('/structures/<int:fee_id>/delete', methods=['POST'])
@fee_access_required
def delete_fee_structure(fee_id):
    """Delete a fee structure"""
    fee = FeeStructure.query.get_or_404(fee_id)
    fee_name = fee.fee_type_name
    
    try:
        # Check if any student accounts use this fee
        accounts_count = StudentFeeAccount.query.filter_by(fee_structure_id=fee_id).count()
        
        if accounts_count > 0:
            flash(f'⚠️ Cannot delete "{fee_name}". It is used by {accounts_count} student fee account(s). Deactivate it instead.', 'warning')
        else:
            db.session.delete(fee)
            db.session.commit()
            flash(f'✅ Fee structure "{fee_name}" deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting fee structure: {str(e)}', 'danger')
    
    return redirect(url_for('fees.fee_structures'))


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
    
    # Get receipt history for this student
    from new_structure.models.fee_management import Receipt
    receipts = Receipt.query.join(Payment).filter(
        Payment.student_id == student_id
    ).order_by(Receipt.issued_at.desc()).all()
    
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
                         current_year=current_year,
                         receipts=receipts)


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
                # Manual mode - redirect to manual allocation page
                db.session.commit()  # Commit payment first
                flash(f'✅ Payment of KES {amount} recorded. Now allocate to specific fees.', 'info')
                return redirect(url_for('fees.allocate_payment_manual', payment_id=payment.id))
            
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
    
    # Get term and academic year from first fee structure
    academic_year = None
    term = None
    if all_accounts:
        first_fee_structure = FeeStructure.query.get(all_accounts[0].fee_structure_id)
        if first_fee_structure:
            academic_year = first_fee_structure.academic_year
            term = first_fee_structure.term
    
    return render_template('fees/receipt.html',
                         receipt=receipt,
                         payment=payment,
                         student=student,
                         payment_method=payment_method,
                         accountant=accountant,
                         allocations=allocation_details,
                         total_fees=total_fees,
                         total_paid=total_paid,
                         total_balance=total_balance,
                         academic_year=academic_year,
                         term=term)


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
    
    # Get term and academic year from first fee structure
    academic_year = None
    term = None
    if all_accounts:
        first_fee_structure = FeeStructure.query.get(all_accounts[0].fee_structure_id)
        if first_fee_structure:
            academic_year = first_fee_structure.academic_year
            term = first_fee_structure.term
    
    return render_template('fees/receipt_print.html',
                         receipt=receipt,
                         payment=payment,
                         student=student,
                         payment_method=payment_method,
                         accountant=accountant,
                         allocations=allocation_details,
                         total_fees=total_fees,
                         total_balance=total_balance,
                         academic_year=academic_year,
                         term=term)


@fee_bp.route('/receipt/<int:receipt_id>/qr')
def receipt_qr(receipt_id):
    """Generate QR code for receipt verification"""
    import qrcode
    from io import BytesIO
    from flask import send_file
    
    from new_structure.models.fee_management import Receipt
    
    receipt = Receipt.query.get_or_404(receipt_id)
    
    # Create verification URL
    verification_url = request.url_root.rstrip('/') + url_for('fees.verify_receipt', receipt_number=receipt.receipt_number)
    
    # Generate QR code
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=2,
    )
    qr.add_data(verification_url)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    
    # Save to BytesIO object
    img_io = BytesIO()
    img.save(img_io, 'PNG')
    img_io.seek(0)
    
    return send_file(img_io, mimetype='image/png')


@fee_bp.route('/receipt/verify/<receipt_number>')
def verify_receipt(receipt_number):
    """Verify receipt authenticity"""
    from new_structure.models.fee_management import Receipt
    
    receipt = Receipt.query.filter_by(receipt_number=receipt_number).first()
    
    if not receipt:
        return render_template('fees/receipt_verify.html', 
                             valid=False, 
                             message="Invalid receipt number. This receipt does not exist in our system.")
    
    payment = receipt.payment
    student = Student.query.get(payment.student_id)
    payment_method = PaymentMethod.query.get(payment.method_id)
    
    return render_template('fees/receipt_verify.html',
                         valid=True,
                         receipt=receipt,
                         payment=payment,
                         student=student,
                         payment_method=payment_method)


# ============================================================================
# INVOICE MANAGEMENT ROUTES
# ============================================================================

@fee_bp.route('/invoices')
@fee_access_required
def invoice_list():
    """View all invoices with filters"""
    from new_structure.models.fee_management import FeeInvoice
    from sqlalchemy.orm import joinedload
    
    # Get filter parameters
    status = request.args.get('status', '')
    grade_id = request.args.get('grade_id', type=int)
    stream_id = request.args.get('stream_id', type=int)
    term = request.args.get('term', '')
    academic_year = request.args.get('academic_year', '')
    
    # Base query with eager loading of student, grade, and stream
    query = FeeInvoice.query.join(Student).options(
        joinedload(FeeInvoice.student).joinedload(Student.grade),
        joinedload(FeeInvoice.student).joinedload(Student.stream)
    )
    
    # Apply filters
    if status:
        query = query.filter(FeeInvoice.status == status)
    if grade_id:
        query = query.filter(Student.grade_id == grade_id)
    if stream_id:
        query = query.filter(Student.stream_id == stream_id)
    if term:
        query = query.filter(FeeInvoice.term == term)
    if academic_year:
        query = query.filter(FeeInvoice.academic_year == academic_year)
    
    # Order by most recent
    invoices = query.order_by(FeeInvoice.issue_date.desc()).all()
    
    # Get all grades and streams for filter dropdown
    grades = Grade.query.order_by(Grade.name).all()
    streams = Stream.query.order_by(Stream.name).all()
    
    # Get unique terms and academic years from invoices OR fee structures
    terms_from_invoices = db.session.query(FeeInvoice.term).distinct().all()
    academic_years_from_invoices = db.session.query(FeeInvoice.academic_year).distinct().all()
    
    # If no invoices exist, get from fee structures
    if not terms_from_invoices:
        terms_from_invoices = db.session.query(FeeStructure.term).distinct().all()
    if not academic_years_from_invoices:
        academic_years_from_invoices = db.session.query(FeeStructure.academic_year).distinct().all()
    
    return render_template('fees/invoice_list.html',
                         invoices=invoices,
                         grades=grades,
                         streams=streams,
                         terms=[t[0] for t in terms_from_invoices if t[0]],
                         academic_years=[ay[0] for ay in academic_years_from_invoices if ay[0]],
                         current_status=status,
                         current_grade_id=grade_id,
                         current_stream_id=stream_id,
                         current_term=term,
                         current_academic_year=academic_year)


@fee_bp.route('/invoice/<int:invoice_id>')
@fee_access_required
def view_invoice(invoice_id):
    """View individual invoice"""
    from new_structure.models.fee_management import FeeInvoice
    from sqlalchemy.orm import joinedload
    
    invoice = FeeInvoice.query.get_or_404(invoice_id)
    student = Student.query.options(joinedload(Student.grade), joinedload(Student.stream)).get(invoice.student_id)
    
    # Get all fee accounts for this student, term, and academic year
    accounts = StudentFeeAccount.query.filter_by(
        student_id=student.id,
        term=invoice.term,
        academic_year=invoice.academic_year
    ).join(FeeStructure).order_by(FeeStructure.allocation_priority).all()
    
    # Calculate totals
    total_fees = sum(acc.total_amount for acc in accounts)
    total_paid = sum(acc.amount_paid for acc in accounts)
    balance = sum(acc.balance for acc in accounts)
    
    # Get payment history for this invoice period
    payments = Payment.query.filter_by(student_id=student.id).order_by(Payment.payment_date.desc()).all()
    
    return render_template('fees/invoice_view.html',
                         invoice=invoice,
                         student=student,
                         accounts=accounts,
                         total_fees=total_fees,
                         total_paid=total_paid,
                         balance=balance,
                         payments=payments)


@fee_bp.route('/invoice/generate', methods=['GET', 'POST'])
@fee_access_required
def generate_invoices():
    """Generate invoices for students"""
    from new_structure.models.fee_management import FeeInvoice
    from new_structure.models.user import Teacher
    
    if request.method == 'POST':
        grade_id = request.form.get('grade_id', type=int)
        term = request.form.get('term')
        academic_year = request.form.get('academic_year')
        due_date_str = request.form.get('due_date')
        
        if not all([term, academic_year, due_date_str]):
            flash('Please provide all required fields.', 'warning')
            return redirect(url_for('fees.generate_invoices'))
        
        due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
        
        # Get students to generate invoices for
        students_query = Student.query
        if grade_id:
            students_query = students_query.filter_by(grade_id=grade_id)
        
        students = students_query.all()
        
        if not students:
            flash('No students found for the selected criteria.', 'warning')
            return redirect(url_for('fees.generate_invoices'))
        
        generated_count = 0
        skipped_count = 0
        skipped_existing_count = 0
        skipped_no_accounts_count = 0
        total_students_considered = 0
        
        # Get the starting invoice number ONCE before the loop
        year = datetime.now().year
        last_invoice = FeeInvoice.query.filter(
            FeeInvoice.invoice_number.like(f'INV-{year}-%')
        ).order_by(FeeInvoice.id.desc()).first()
        
        if last_invoice:
            next_invoice_num = int(last_invoice.invoice_number.split('-')[-1]) + 1
        else:
            next_invoice_num = 1
        
        for student in students:
            total_students_considered += 1
            # Check if invoice already exists
            existing = FeeInvoice.query.filter_by(
                student_id=student.id,
                term=term,
                academic_year=academic_year
            ).first()
            
            if existing:
                skipped_count += 1
                skipped_existing_count += 1
                continue
            
            # Get student's fee accounts for this term
            accounts = StudentFeeAccount.query.filter_by(
                student_id=student.id,
                term=term,
                academic_year=academic_year
            ).all()

            # If no prebuilt accounts exist, auto-create them from the fee structure for this student
            if not accounts:
                try:
                    created_accounts = StudentFeeAccount.create_accounts_for_student(
                        student, term, academic_year, due_date
                    )
                    accounts = created_accounts or []
                except Exception:
                    accounts = []

            if not accounts:
                skipped_count += 1
                skipped_no_accounts_count += 1
                continue
            
            total_amount = sum(acc.total_amount for acc in accounts)
            
            # Generate invoice number with incremented counter
            invoice_number = f'INV-{year}-{next_invoice_num:05d}'
            next_invoice_num += 1
            
            # Create invoice
            invoice = FeeInvoice(
                invoice_number=invoice_number,
                student_id=student.id,
                academic_year=academic_year,
                term=term,
                issue_date=date.today(),
                due_date=due_date,
                total_amount=total_amount,
                status='issued',
                generated_by=session.get('teacher_id')
            )
            
            db.session.add(invoice)
            generated_count += 1
        
        try:
            db.session.commit()
            # Provide richer diagnostics in the flash to help troubleshoot
            flash(
                f'Successfully generated {generated_count} invoice(s). '
                f'Skipped {skipped_count} (existing: {skipped_existing_count}, no accounts: {skipped_no_accounts_count}). '
                f'Considered {total_students_considered} student(s).',
                'success'
            )
        except Exception as e:
            db.session.rollback()
            flash(f'Error generating invoices: {str(e)}', 'danger')
        
        return redirect(url_for('fees.invoice_list'))
    
    # GET request - show form
    grades = Grade.query.order_by(Grade.name).all()
    
    # Get unique terms and academic years from fee structures
    terms = db.session.query(FeeStructure.term).distinct().all()
    academic_years = db.session.query(FeeStructure.academic_year).distinct().all()
    
    return render_template('fees/invoice_generate.html',
                         grades=grades,
                         terms=[t[0] for t in terms],
                         academic_years=[ay[0] for ay in academic_years])


@fee_bp.route('/invoice/<int:invoice_id>/delete', methods=['POST'])
@fee_access_required
def delete_invoice(invoice_id):
    """Delete a single invoice"""
    from new_structure.models.fee_management import FeeInvoice
    
    invoice = FeeInvoice.query.get_or_404(invoice_id)
    invoice_number = invoice.invoice_number
    
    try:
        db.session.delete(invoice)
        db.session.commit()
        flash(f'Invoice {invoice_number} deleted successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting invoice: {str(e)}', 'danger')
    
    return redirect(url_for('fees.invoice_list'))


@fee_bp.route('/invoices/delete-bulk', methods=['POST'])
@fee_access_required
def delete_invoices_bulk():
    """Delete multiple invoices"""
    from new_structure.models.fee_management import FeeInvoice
    
    # Get filter parameters for bulk delete
    term = request.form.get('term')
    academic_year = request.form.get('academic_year')
    grade_id = request.form.get('grade_id', type=int)
    
    if not term or not academic_year:
        flash('Please specify term and academic year for bulk delete.', 'warning')
        return redirect(url_for('fees.invoice_list'))
    
    # Build query
    query = FeeInvoice.query.filter_by(term=term, academic_year=academic_year)
    
    if grade_id:
        query = query.join(Student).filter(Student.grade_id == grade_id)
    
    # Get count before deleting
    count = query.count()
    
    if count == 0:
        flash('No invoices found matching the criteria.', 'warning')
        return redirect(url_for('fees.invoice_list'))
    
    try:
        query.delete(synchronize_session=False)
        db.session.commit()
        flash(f'Successfully deleted {count} invoice(s) for {term} {academic_year}.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting invoices: {str(e)}', 'danger')
    
    return redirect(url_for('fees.invoice_list'))


@fee_bp.route('/payment/<int:payment_id>/allocate', methods=['GET', 'POST'])
@fee_access_required
def allocate_payment_manual(payment_id):
    """Manually allocate payment to specific fee accounts"""
    payment = Payment.query.get_or_404(payment_id)
    student = Student.query.get(payment.student_id)
    
    # Get all fee accounts with outstanding balance
    accounts = StudentFeeAccount.query.filter_by(
        student_id=student.id
    ).join(FeeStructure).order_by(FeeStructure.allocation_priority).all()
    
    # Calculate already allocated amount
    already_allocated = sum(alloc.amount_allocated for alloc in payment.allocations)
    remaining_amount = payment.amount - already_allocated
    
    if request.method == 'POST':
        try:
            total_allocated = Decimal('0')
            
            # Process each allocation input
            for account in accounts:
                amount_key = f'amount_{account.id}'
                amount_str = request.form.get(amount_key, '0').strip()
                
                if amount_str and float(amount_str) > 0:
                    allocated_amount = Decimal(amount_str)
                    
                    # Validate: can't allocate more than balance
                    if allocated_amount > account.balance:
                        flash(f'Cannot allocate KES {allocated_amount} to {account.fee_structure.fee_type_name} - balance is only KES {account.balance}', 'danger')
                        return redirect(url_for('fees.allocate_payment_manual', payment_id=payment_id))
                    
                    # Validate: can't exceed remaining payment amount
                    if total_allocated + allocated_amount > remaining_amount:
                        flash(f'Total allocation exceeds remaining payment amount (KES {remaining_amount})', 'danger')
                        return redirect(url_for('fees.allocate_payment_manual', payment_id=payment_id))
                    
                    # Create allocation
                    allocation = PaymentAllocation(
                        payment_id=payment.id,
                        student_fee_account_id=account.id,
                        amount_allocated=allocated_amount
                    )
                    db.session.add(allocation)
                    
                    # Update account
                    account.amount_paid += allocated_amount
                    account.balance -= allocated_amount
                    if account.balance <= 0:
                        account.status = 'paid'
                    elif account.amount_paid > 0:
                        account.status = 'partial'
                    account.last_payment_date = datetime.utcnow()
                    
                    total_allocated += allocated_amount
            
            # Handle any remaining unallocated amount as credit
            final_remaining = remaining_amount - total_allocated
            if final_remaining > 0:
                credit = StudentCreditBalance(
                    student_id=student.id,
                    payment_id=payment.id,
                    credit_amount=final_remaining,
                    remaining_credit=final_remaining,
                    status='available',
                    notes=f'Unallocated amount from payment {payment.reference or payment.id}'
                )
                db.session.add(credit)
                flash(f'✅ Allocated KES {total_allocated}. KES {final_remaining} kept as credit.', 'success')
            else:
                flash(f'✅ Payment fully allocated: KES {total_allocated}', 'success')
            
            # Generate receipt
            from new_structure.models.fee_management import Receipt
            if not payment.receipt:
                receipt_number = f"RCP-{datetime.utcnow().year}-{payment.id:05d}"
                receipt = Receipt(
                    receipt_number=receipt_number,
                    payment_id=payment.id,
                    issued_by=session.get('teacher_id'),
                    issued_at=datetime.utcnow()
                )
                db.session.add(receipt)
            
            db.session.commit()
            return redirect(url_for('fees.student_fees', student_id=student.id))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error allocating payment: {str(e)}', 'danger')
    
    # GET request
    payment_method = PaymentMethod.query.get(payment.method_id)
    
    return render_template('fees/allocate_payment.html',
                         payment=payment,
                         student=student,
                         accounts=accounts,
                         payment_method=payment_method,
                         remaining_amount=remaining_amount)
