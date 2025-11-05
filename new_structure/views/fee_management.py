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
    FeeStructure, StudentFeeAccount, PaymentMethod, Payment, PaymentAllocation, StudentCreditBalance, FeeInvoice
)
from new_structure.models.academic import Student, Grade, Term, Stream
from new_structure.services import is_authenticated, get_role

fee_bp = Blueprint('fees', __name__, url_prefix='/fees')


# Authentication decorator for fee management routes
def fee_access_required(f):
    """Decorator to require authenticated user with fee management access"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_authenticated(session):
            flash('Please log in to access fee management.', 'warning')
            return redirect(url_for('auth.fee_staff_login'))
        
        role = get_role(session)
        # Allow headteacher, secretary, and accountant to access fee management
        # Note: 'classteacher' and 'teacher' roles removed for security - fee management is financial
        if role not in ('headteacher', 'secretary', 'accountant'):
            flash('Access denied. Fee management requires administrative or finance staff privileges.', 'error')
            return redirect(url_for('auth.fee_staff_login'))
        
        return f(*args, **kwargs)
    return decorated_function


@fee_bp.route('/')
@fee_access_required
def index():
    """Dashboard showing fee management overview with filters"""
    from sqlalchemy import func
    from datetime import date
    
    total_fees = FeeStructure.query.filter_by(is_active=True).count()
    total_students = Student.query.count()
    total_payments_count = Payment.query.count()
    total_invoices = FeeInvoice.query.count()
    
    # Calculate Total Revenue (all payments)
    total_revenue = db.session.query(func.sum(Payment.amount)).scalar() or Decimal('0')
    
    # Calculate Outstanding Balance (total balance across all student fee accounts)
    total_outstanding = db.session.query(func.sum(StudentFeeAccount.balance)).scalar() or Decimal('0')
    
    # Calculate Today's Collections
    today = date.today()
    today_collections = db.session.query(func.sum(Payment.amount)).filter(
        func.date(Payment.payment_date) == today
    ).scalar() or Decimal('0')
    today_payments_count = Payment.query.filter(
        func.date(Payment.payment_date) == today
    ).count()
    
    # Get filters from query parameters
    term_filter = request.args.get('term', '')
    grade_filter = request.args.get('grade', '')
    stream_filter = request.args.get('stream', '')
    method_filter = request.args.get('method', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    limit = int(request.args.get('limit', 20))
    
    # Build query for payments
    query = Payment.query.join(Student)
    
    if term_filter:
        query = query.filter(Payment.term == term_filter)
    if grade_filter:
        query = query.join(Student.grade).filter(Grade.id == int(grade_filter))
    if stream_filter:
        query = query.join(Student.stream).filter(Stream.id == int(stream_filter))
    if method_filter:
        query = query.filter(Payment.method == method_filter)
    if date_from:
        from datetime import datetime
        query = query.filter(Payment.payment_date >= datetime.strptime(date_from, '%Y-%m-%d'))
    if date_to:
        from datetime import datetime
        query = query.filter(Payment.payment_date <= datetime.strptime(date_to, '%Y-%m-%d'))
    
    payments = query.order_by(Payment.payment_date.desc()).limit(limit).all()
    
    # Get filter options
    grades = Grade.query.order_by(Grade.name).all()
    streams = Stream.query.order_by(Stream.name).all()
    
    # Calculate total amount from filtered payments
    total_amount = sum(p.amount for p in payments)
    
    return render_template('fees/index.html',
                         total_fees=total_fees,
                         total_students=total_students,
                         total_payments=total_payments_count,
                         total_invoices=total_invoices,
                         recent_payments=payments,
                         grades=grades,
                         streams=streams,
                         term_filter=term_filter,
                         grade_filter=grade_filter,
                         stream_filter=stream_filter,
                         method_filter=method_filter,
                         date_from=date_from,
                         date_to=date_to,
                         limit=limit,
                         total_amount=total_amount,
                         total_revenue=total_revenue,
                         total_outstanding=total_outstanding,
                         today_collections=today_collections,
                         today_payments_count=today_payments_count)


@fee_bp.route('/structures')
@fee_access_required
def fee_structures():
    """List all fee structures with usage counts"""
    # Get educational level filter
    current_level = request.args.get('education_level', '')
    current_term = request.args.get('term', '')
    
    query = FeeStructure.query
    if current_level:
        query = query.filter_by(education_level=current_level)
    if current_term:
        query = query.filter_by(term=current_term)
    
    fees = query.order_by(
        FeeStructure.academic_year.desc(),
        FeeStructure.term,
        FeeStructure.allocation_priority
    ).all()
    
    # Calculate usage counts for each fee structure
    fee_usage = {}
    for fee in fees:
        accounts = StudentFeeAccount.query.filter_by(fee_structure_id=fee.id).all()
        students_count = len(accounts)
        students_with_payments = sum(1 for acc in accounts if acc.amount_paid > 0)
        total_collected = sum(acc.amount_paid for acc in accounts)
        
        fee_usage[fee.id] = {
            'students_count': students_count,
            'students_with_payments': students_with_payments,
            'total_collected': total_collected,
            'can_delete': students_count == 0
        }
    
    # Get unique education levels for stats
    education_levels = db.session.query(FeeStructure.education_level)\
        .distinct()\
        .filter(FeeStructure.education_level.isnot(None))\
        .all()
    education_levels = [level[0] for level in education_levels]
    
    # Group fees by term for card layout
    fees_by_term = {}
    if current_level:
        for term in ['Term 1', 'Term 2', 'Term 3']:
            term_fees = [f for f in fees if f.term == term and f.is_active]
            if term_fees:
                fees_by_term[term] = {
                    'fees': term_fees,
                    'total': sum(f.amount for f in term_fees),
                    'count': len(term_fees)
                }
    
    return render_template('fees/structures.html', 
                         fees=fees,
                         fee_usage=fee_usage,
                         current_level=current_level,
                         current_term=current_term,
                         education_levels=education_levels,
                         fees_by_term=fees_by_term)


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
            
            # Check if "Create for all terms" checkbox is checked
            create_for_all_terms = request.form.get('create_for_all_terms') == 'true'
            terms_to_create = ['Term 1', 'Term 2', 'Term 3'] if create_for_all_terms else [term]
            
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
                    # If individual fee has specific term, use that (override "create for all terms")
                    has_specific_term = fee_info.get('term') and fee_info.get('term').strip()
                    
                    if has_specific_term:
                        # This fee has a specific term override, create only for that term
                        terms_for_this_fee = [fee_info.get('term')]
                    else:
                        # Use the common term settings (either single term or all 3 terms)
                        terms_for_this_fee = terms_to_create
                    
                    fee_frequency = fee_info.get('frequency') if fee_info.get('frequency') else frequency
                    fee_name = fee_info['name'].strip()
                    
                    # Create fee for each term
                    for fee_term in terms_for_this_fee:
                        # Check for duplicates
                        existing = FeeStructure.query.filter_by(
                            fee_type_name=fee_name,
                            academic_year=academic_year,
                            term=fee_term,
                            education_level=education_level,
                            frequency=fee_frequency,
                            grade_id=grade_id
                        ).first()
                        
                        if existing:
                            dup_name = f"{fee_name} ({fee_term})" if create_for_all_terms else fee_name
                            duplicates_found.append(dup_name)
                            continue  # Skip this duplicate
                        
                        # If optional is checked, it's not mandatory
                        is_optional = fee_info.get('optional', False)
                        is_mandatory = fee_info.get('mandatory', True) and not is_optional
                        
                        fee = FeeStructure(
                            fee_type_name=fee_info['name'],
                            description=fee_info.get('description'),
                            amount=Decimal(fee_info['amount']),
                            academic_year=academic_year,
                            term=fee_term,
                            grade_id=grade_id,
                            education_level=education_level,
                            category=fee_info.get('category', 'tuition'),
                            frequency=fee_frequency,
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
                        
                        # Add to fee_names (show term if creating for all terms)
                        if create_for_all_terms and not has_specific_term:
                            fee_names.append(f"{fee_info['name']} ({fee_term})")
                        else:
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
            
            # Handle optional checkbox (overrides mandatory)
            is_optional = request.form.get('optional') == 'on'
            is_mandatory_checked = request.form.get('is_mandatory') == 'on'
            
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
            fee.is_mandatory = is_mandatory_checked and not is_optional  # Optional overrides mandatory
            fee.is_refundable = request.form.get('is_refundable') == 'on'
            fee.is_active = request.form.get('is_active') == 'on'
            
            db.session.commit()
            flash(f'✅ Fee structure "{fee.fee_type_name}" updated successfully!', 'success')
            return redirect(url_for('fees.fee_structures'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating fee structure: {str(e)}', 'danger')
    
    grades = Grade.query.order_by(Grade.name).all()
    return render_template('fees/edit_structure.html', grades=grades, fee=fee, 
                          applies_to_grades_str=applies_to_grades_str)


@fee_bp.route('/structures/<int:fee_id>/delete', methods=['POST'])
@fee_access_required
def delete_fee_structure(fee_id):
    """Delete a fee structure with enhanced safety checks"""
    fee = FeeStructure.query.get_or_404(fee_id)
    fee_name = fee.fee_type_name
    
    try:
        # Enhanced safety checks
        accounts = StudentFeeAccount.query.filter_by(fee_structure_id=fee_id).all()
        accounts_count = len(accounts)
        
        if accounts_count > 0:
            # Check if any of these accounts have payments
            accounts_with_payments = 0
            total_payments = Decimal('0')
            
            for account in accounts:
                if account.amount_paid > 0:
                    accounts_with_payments += 1
                    total_payments += account.amount_paid
            
            if accounts_with_payments > 0:
                flash(f'🚫 Cannot delete "{fee_name}"!\n\n'
                      f'• Used by {accounts_count} student(s)\n'
                      f'• {accounts_with_payments} student(s) have made payments\n'
                      f'• Total payments: KES {total_payments:,.2f}\n\n'
                      f'You must first reverse all payments or deactivate this fee instead.', 
                      'danger')
            else:
                flash(f'⚠️ Cannot delete "{fee_name}". It is assigned to {accounts_count} student(s). '
                      f'You can deactivate it instead to prevent new usage.', 'warning')
        else:
            db.session.delete(fee)
            db.session.commit()
            flash(f'✅ Fee structure "{fee_name}" deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting fee structure: {str(e)}', 'danger')
    
    return redirect(url_for('fees.fee_structures'))


@fee_bp.route('/structures/bulk-delete', methods=['POST'])
@fee_access_required
def bulk_delete_fee_structures():
    """Delete multiple fee structures at once"""
    try:
        data = request.get_json()
        fee_ids = data.get('fee_ids', [])
        
        if not fee_ids:
            return jsonify({'success': False, 'message': 'No fees selected'}), 400
        
        deleted_count = 0
        skipped_count = 0
        skipped_names = []
        
        for fee_id in fee_ids:
            fee = FeeStructure.query.get(fee_id)
            if not fee:
                continue
            
            # Check if any student accounts use this fee
            accounts_count = StudentFeeAccount.query.filter_by(fee_structure_id=fee_id).count()
            
            if accounts_count > 0:
                skipped_count += 1
                skipped_names.append(fee.fee_type_name)
            else:
                db.session.delete(fee)
                deleted_count += 1
        
        db.session.commit()
        
        # Set flash message for next page load
        if deleted_count > 0 and skipped_count == 0:
            flash(f'✅ Successfully deleted {deleted_count} fee structure(s)!', 'success')
        elif deleted_count > 0 and skipped_count > 0:
            flash(f'✅ Deleted {deleted_count} fee(s). ⚠️ Skipped {skipped_count} fee(s) in use: {", ".join(skipped_names)}', 'warning')
        elif skipped_count > 0:
            flash(f'⚠️ Cannot delete {skipped_count} fee(s) - they are in use: {", ".join(skipped_names)}', 'warning')
        
        return jsonify({'success': True, 'deleted': deleted_count, 'skipped': skipped_count})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


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
            # Debug logging
            print("=== PAYMENT RECORDING DEBUG ===")
            print(f"Form data: {dict(request.form)}")
            
            teacher_id = session.get('teacher_id')
            student_id = int(request.form['student_id'])
            amount = Decimal(request.form['amount'])
            method_id = int(request.form['method_id'])
            reference = request.form.get('reference', '')
            allocation_mode = request.form.get('allocation_mode', 'auto')
            
            print(f"Parsed values - student_id: {student_id}, amount: {amount}, method_id: {method_id}")
            
            # Get student - their grade is already in the database
            student = Student.query.get_or_404(student_id)
            
            if not student.grade:
                flash(f'⚠️ Student {student.name} has no grade assigned. Please assign a grade first.', 'warning')
                return redirect(url_for('fees.record_payment'))
            
            # Auto-create fee accounts if they don't exist (for current term)
            # This allows recording payments even before invoices are generated
            # Uses the student's grade from the database to find applicable fee structures
            current_term = request.form.get('term', 'Term 1')
            year = datetime.utcnow().year
            current_year = request.form.get('academic_year', f"{year}-{year + 1}")
            
            accounts = StudentFeeAccount.query.filter_by(
                student_id=student_id,
                term=current_term,
                academic_year=current_year
            ).all()
            
            if not accounts:
                # Auto-create accounts from applicable fee structures
                accounts = StudentFeeAccount.create_accounts_for_student(
                    student, 
                    current_term, 
                    current_year,
                    due_date=None
                )
                if not accounts:
                    flash(f'⚠️ No fee structures found for {student.grade.name if student.grade else "this student"}. Please create fee structures first.', 'warning')
                    return redirect(url_for('fees.record_payment'))
                flash(f'📋 Created fee accounts for {student.name} - {current_term} {current_year}', 'info')
            
            # Create payment record
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
                    StudentFeeAccount.balance > 0,
                    StudentFeeAccount.term == current_term,
                    StudentFeeAccount.academic_year == current_year
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
            import traceback
            db.session.rollback()
            error_trace = traceback.format_exc()
            print(f"=== ERROR RECORDING PAYMENT ===")
            print(error_trace)
            flash(f'Error recording payment: {str(e)}', 'danger')
            return redirect(url_for('fees.record_payment'))
    
    # GET request - show form
    # Fetch ALL students using the proper relationships (like manage_students does)
    from new_structure.models.academic import Grade, Stream
    
    students_raw = Student.query.order_by(Student.name).all()
    
    payment_methods = PaymentMethod.query.filter_by(is_active=True).all()
    
    # Convert students to serializable format with proper grade and stream info
    # Use the Student model's relationships to access grade and stream directly
    students = []
    for s in students_raw:
        # Use the Student model's relationship properties directly
        grade_name = 'No Grade'
        stream_name = ''
        
        # Access through the relationship (Student.grade relationship)
        if s.grade:
            grade_name = s.grade.name
        
        # Access through backref from Stream (Student.stream relationship via Stream.students backref)
        if s.stream_id:
            stream = Stream.query.get(s.stream_id)
            if stream:
                stream_name = stream.name
        
        # Debug logging for first 3 students
        if len(students) < 3:
            print(f"DEBUG Student: {s.name} - grade_id={s.grade_id}, stream_id={s.stream_id}, grade={grade_name}, stream={stream_name}")
        
        students.append({
            'id': s.id,
            'name': s.name,
            'admission_number': s.admission_number,
            'grade': grade_name,
            'stream': stream_name
        })
    
    # Get current year for default selection in format "2025-2026"
    year = datetime.utcnow().year
    current_year = f"{year}-{year + 1}"
    
    return render_template('fees/record_payment.html',
                         students=students,
                         payment_methods=payment_methods,
                         current_year=current_year)


@fee_bp.route('/student-balance/<int:student_id>')
@fee_access_required
def get_student_balance(student_id):
    """Get student's outstanding balance and recent payments for record payment UX"""
    try:
        student = Student.query.get_or_404(student_id)
        
        # Get current term/year
        year = datetime.utcnow().year
        current_year = f"{year}-{year + 1}"
        current_term = request.args.get('term', 'Term 1')
        
        # Get all fee accounts for this student
        accounts = StudentFeeAccount.query.filter_by(
            student_id=student_id,
            term=current_term,
            academic_year=current_year
        ).join(FeeStructure).order_by(FeeStructure.allocation_priority).all()
        
        # Calculate totals
        total_fees = sum(acc.total_amount for acc in accounts)
        total_paid = sum(acc.amount_paid for acc in accounts)
        total_balance = sum(acc.balance for acc in accounts)
        
        # Get fee breakdown
        fee_breakdown = [{
            'fee_name': acc.fee_structure.fee_type_name,
            'total': float(acc.total_amount),
            'paid': float(acc.amount_paid),
            'balance': float(acc.balance),
            'status': acc.status,
            'priority': acc.fee_structure.allocation_priority
        } for acc in accounts]
        
        # Get recent payments (last 10)
        recent_payments = Payment.query.filter_by(student_id=student_id)\
            .order_by(Payment.payment_date.desc())\
            .limit(10).all()
        
        payments_list = [{
            'id': p.id,
            'date': p.payment_date.strftime('%d %b %Y %I:%M %p'),
            'amount': float(p.amount),
            'method': p.method.name if p.method else 'N/A',
            'reference': p.reference or '',
            'recorded_by': p.recorder.full_name if p.recorder else 'System'
        } for p in recent_payments]
        
        return jsonify({
            'success': True,
            'student_name': student.name,
            'admission_number': student.admission_number,
            'grade': student.grade.name if student.grade else 'N/A',
            'stream': student.stream.name if student.stream else '',
            'total_fees': float(total_fees),
            'total_paid': float(total_paid),
            'total_balance': float(total_balance),
            'fee_breakdown': fee_breakdown,
            'recent_payments': payments_list,
            'has_accounts': len(accounts) > 0
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


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


@fee_bp.route('/analytics')
@fee_access_required
def analytics_dashboard():
    """Fee collection analytics and reports dashboard"""
    from sqlalchemy import func, case, and_
    from datetime import date, timedelta, datetime
    
    # Get filter parameters
    term_filter = request.args.get('term', 'Term 1')
    academic_year = request.args.get('academic_year', '2025')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    grade_filter = request.args.get('grade_id')
    stream_filter = request.args.get('stream_id')
    payment_status = request.args.get('payment_status')
    
    # Build base query filters
    query_filters = []
    payment_filters = []
    
    # Date range filter for payments
    if start_date:
        try:
            start_dt = datetime.strptime(start_date, '%Y-%m-%d').date()
            payment_filters.append(Payment.payment_date >= start_dt)
        except ValueError:
            pass
    
    if end_date:
        try:
            end_dt = datetime.strptime(end_date, '%Y-%m-%d').date()
            payment_filters.append(Payment.payment_date <= end_dt)
        except ValueError:
            pass
    
    # Grade filter
    if grade_filter:
        query_filters.append(Grade.name == grade_filter)
    
    # Stream filter
    if stream_filter:
        query_filters.append(Stream.name == stream_filter)
    
    # =========================
    # 1. SUMMARY STATISTICS
    # =========================
    summary_query = db.session.query(
        func.sum(StudentFeeAccount.total_amount).label('total_expected'),
        func.sum(StudentFeeAccount.amount_paid).label('total_collected'),
        func.sum(StudentFeeAccount.balance).label('total_outstanding')
    ).join(Student)
    
    if query_filters:
        summary_query = summary_query.join(Grade).join(Stream, isouter=True).filter(and_(*query_filters))
    
    summary = summary_query.first()
    total_expected = summary.total_expected or Decimal('0')
    total_collected = summary.total_collected or Decimal('0')
    total_outstanding = summary.total_outstanding or Decimal('0')
    collection_rate = (float(total_collected) / float(total_expected) * 100) if total_expected > 0 else 0
    
    # Count students by payment status
    status_base = db.session.query(Student.id, 
                                    func.sum(StudentFeeAccount.balance).label('balance'),
                                    func.sum(StudentFeeAccount.amount_paid).label('paid'))\
        .join(StudentFeeAccount)
    
    if query_filters:
        status_base = status_base.join(Grade).join(Stream, isouter=True).filter(and_(*query_filters))
    
    status_base = status_base.group_by(Student.id).subquery()
    
    fully_paid_count = db.session.query(func.count(status_base.c.id)).filter(status_base.c.balance == 0).scalar() or 0
    partially_paid_count = db.session.query(func.count(status_base.c.id)).filter(
        and_(status_base.c.balance > 0, status_base.c.paid > 0)
    ).scalar() or 0
    not_paid_count = db.session.query(func.count(status_base.c.id)).filter(status_base.c.paid == 0).scalar() or 0
    
    # Apply payment status filter if specified
    if payment_status:
        if payment_status == 'paid':
            query_filters.append(StudentFeeAccount.balance == 0)
        elif payment_status == 'partial':
            query_filters.append(and_(StudentFeeAccount.balance > 0, StudentFeeAccount.amount_paid > 0))
        elif payment_status == 'unpaid':
            query_filters.append(StudentFeeAccount.amount_paid == 0)
    
    # =========================
    # 2. COLLECTION BY GRADE
    # =========================
    grade_query = db.session.query(
        Grade.name,
        func.count(func.distinct(Student.id)).label('student_count'),
        func.sum(StudentFeeAccount.total_amount).label('expected'),
        func.sum(StudentFeeAccount.amount_paid).label('collected'),
        func.sum(StudentFeeAccount.balance).label('outstanding')
    ).join(Student, Grade.id == Student.grade_id)\
     .join(StudentFeeAccount, Student.id == StudentFeeAccount.student_id)
    
    if stream_filter:
        grade_query = grade_query.join(Stream).filter(Stream.name == stream_filter)
    
    grade_stats = grade_query.group_by(Grade.name).order_by(Grade.name).all()
    
    # =========================
    # 3. COLLECTION BY STREAM
    # =========================
    stream_query = db.session.query(
        Stream.name,
        Grade.name.label('grade_name'),
        func.count(func.distinct(Student.id)).label('student_count'),
        func.sum(StudentFeeAccount.total_amount).label('expected'),
        func.sum(StudentFeeAccount.amount_paid).label('collected'),
        func.sum(StudentFeeAccount.balance).label('outstanding')
    ).join(Student, Stream.id == Student.stream_id)\
     .join(Grade, Stream.grade_id == Grade.id)\
     .join(StudentFeeAccount, Student.id == StudentFeeAccount.student_id)
    
    if grade_filter:
        stream_query = stream_query.filter(Grade.name == grade_filter)
    
    stream_stats = stream_query.group_by(Stream.name, Grade.name).order_by(Grade.name, Stream.name).all()
    
    # =========================
    # 4. PAYMENT METHOD BREAKDOWN
    # =========================
    payment_method_query = db.session.query(
        PaymentMethod.name,
        func.count(Payment.id).label('transaction_count'),
        func.sum(Payment.amount).label('total_amount')
    ).join(Payment, PaymentMethod.id == Payment.method_id)
    
    if payment_filters:
        payment_method_query = payment_method_query.filter(and_(*payment_filters))
    
    if query_filters:
        payment_method_query = payment_method_query.join(
            StudentFeeAccount, Payment.student_fee_account_id == StudentFeeAccount.id
        ).join(Student).join(Grade).join(Stream, isouter=True).filter(and_(*query_filters))
    
    payment_method_stats = payment_method_query.group_by(PaymentMethod.name).all()
    
    # =========================
    # 5. DAILY COLLECTION TRENDS (Last 30 days)
    # =========================
    thirty_days_ago = date.today() - timedelta(days=30)
    trend_filters = [Payment.payment_date >= thirty_days_ago]
    
    if payment_filters:
        trend_filters.extend(payment_filters)
    
    daily_query = db.session.query(
        func.date(Payment.payment_date).label('date'),
        func.count(Payment.id).label('count'),
        func.sum(Payment.amount).label('amount')
    ).filter(and_(*trend_filters))
    
    if query_filters:
        daily_query = daily_query.join(
            StudentFeeAccount, Payment.student_fee_account_id == StudentFeeAccount.id
        ).join(Student).join(Grade).join(Stream, isouter=True).filter(and_(*query_filters))
    
    daily_collections = daily_query.group_by(func.date(Payment.payment_date))\
                                   .order_by(func.date(Payment.payment_date))\
                                   .all()
    
    # =========================
    # 6. DEFAULTERS LIST (Top 20)
    # =========================
    defaulters_query = db.session.query(
        Student,
        func.sum(StudentFeeAccount.total_amount).label('total_fees'),
        func.sum(StudentFeeAccount.amount_paid).label('total_paid'),
        func.sum(StudentFeeAccount.balance).label('balance')
    ).join(StudentFeeAccount)
    
    if query_filters:
        defaulters_query = defaulters_query.join(Grade).join(Stream, isouter=True).filter(and_(*query_filters))
    
    defaulters = defaulters_query.group_by(Student.id)\
                                 .having(func.sum(StudentFeeAccount.balance) > 0)\
                                 .order_by(func.sum(StudentFeeAccount.balance).desc())\
                                 .limit(20)\
                                 .all()
    
    return render_template('fees/analytics_dashboard.html',
                         # Summary stats
                         total_expected=total_expected,
                         total_collected=total_collected,
                         total_outstanding=total_outstanding,
                         collection_rate=collection_rate,
                         fully_paid_count=fully_paid_count,
                         partially_paid_count=partially_paid_count,
                         not_paid_count=not_paid_count,
                         # Breakdowns
                         grade_stats=grade_stats,
                         stream_stats=stream_stats,
                         payment_method_stats=payment_method_stats,
                         daily_collections=daily_collections,
                         defaulters=defaulters,
                         # Filters
                         term_filter=term_filter,
                         academic_year=academic_year)


@fee_bp.route('/analytics/export/excel')
@fee_access_required
def export_analytics_excel():
    """Export analytics data to Excel"""
    from flask import send_file
    from services.fee_export_service import FeeExportService
    from sqlalchemy import func
    from datetime import date, timedelta
    
    # Get filter parameters
    term_filter = request.args.get('term', 'Term 1')
    academic_year = request.args.get('academic_year', '2025')
    
    # Collect same analytics data as dashboard
    total_expected = db.session.query(func.sum(StudentFeeAccount.total_amount)).scalar() or Decimal('0')
    total_collected = db.session.query(func.sum(StudentFeeAccount.amount_paid)).scalar() or Decimal('0')
    total_outstanding = db.session.query(func.sum(StudentFeeAccount.balance)).scalar() or Decimal('0')
    collection_rate = (float(total_collected) / float(total_expected) * 100) if total_expected > 0 else 0
    
    fully_paid_count = db.session.query(func.count(func.distinct(Student.id)))\
        .join(StudentFeeAccount)\
        .group_by(Student.id)\
        .having(func.sum(StudentFeeAccount.balance) == 0)\
        .count()
    
    partially_paid_count = db.session.query(func.count(func.distinct(Student.id)))\
        .join(StudentFeeAccount)\
        .group_by(Student.id)\
        .having(func.sum(StudentFeeAccount.balance) > 0, func.sum(StudentFeeAccount.amount_paid) > 0)\
        .count()
    
    not_paid_count = db.session.query(func.count(func.distinct(Student.id)))\
        .join(StudentFeeAccount)\
        .group_by(Student.id)\
        .having(func.sum(StudentFeeAccount.amount_paid) == 0)\
        .count()
    
    grade_stats = db.session.query(
        Grade.name,
        func.count(func.distinct(Student.id)).label('student_count'),
        func.sum(StudentFeeAccount.total_amount).label('expected'),
        func.sum(StudentFeeAccount.amount_paid).label('collected'),
        func.sum(StudentFeeAccount.balance).label('outstanding')
    ).join(Student, Grade.id == Student.grade_id)\
     .join(StudentFeeAccount, Student.id == StudentFeeAccount.student_id)\
     .group_by(Grade.name)\
     .order_by(Grade.name)\
     .all()
    
    stream_stats = db.session.query(
        Stream.name,
        Grade.name.label('grade_name'),
        func.count(func.distinct(Student.id)).label('student_count'),
        func.sum(StudentFeeAccount.total_amount).label('expected'),
        func.sum(StudentFeeAccount.amount_paid).label('collected'),
        func.sum(StudentFeeAccount.balance).label('outstanding')
    ).join(Student, Stream.id == Student.stream_id)\
     .join(Grade, Stream.grade_id == Grade.id)\
     .join(StudentFeeAccount, Student.id == StudentFeeAccount.student_id)\
     .group_by(Stream.name, Grade.name)\
     .order_by(Grade.name, Stream.name)\
     .all()
    
    payment_method_stats = db.session.query(
        PaymentMethod.name,
        func.count(Payment.id).label('transaction_count'),
        func.sum(Payment.amount).label('total_amount')
    ).join(Payment, PaymentMethod.id == Payment.method_id)\
     .group_by(PaymentMethod.name)\
     .all()
    
    defaulters = db.session.query(
        Student,
        func.sum(StudentFeeAccount.total_amount).label('total_fees'),
        func.sum(StudentFeeAccount.amount_paid).label('total_paid'),
        func.sum(StudentFeeAccount.balance).label('balance')
    ).join(StudentFeeAccount)\
     .group_by(Student.id)\
     .having(func.sum(StudentFeeAccount.balance) > 0)\
     .order_by(func.sum(StudentFeeAccount.balance).desc())\
     .limit(100)\
     .all()
    
    analytics_data = {
        'total_expected': total_expected,
        'total_collected': total_collected,
        'total_outstanding': total_outstanding,
        'collection_rate': collection_rate,
        'fully_paid_count': fully_paid_count,
        'partially_paid_count': partially_paid_count,
        'not_paid_count': not_paid_count,
        'grade_stats': grade_stats,
        'stream_stats': stream_stats,
        'payment_method_stats': payment_method_stats,
        'defaulters': defaulters
    }
    
    excel_file = FeeExportService.export_analytics_to_excel(analytics_data)
    
    filename = f'Fee_Analytics_{term_filter}_{academic_year}_{datetime.now().strftime("%Y%m%d")}.xlsx'
    
    return send_file(
        excel_file,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=filename
    )


@fee_bp.route('/analytics/export/pdf')
@fee_access_required
def export_defaulters_pdf():
    """Export defaulters list to PDF"""
    from flask import send_file
    from services.fee_export_service import FeeExportService
    from sqlalchemy import func
    
    # Get defaulters
    defaulters = db.session.query(
        Student,
        func.sum(StudentFeeAccount.total_amount).label('total_fees'),
        func.sum(StudentFeeAccount.amount_paid).label('total_paid'),
        func.sum(StudentFeeAccount.balance).label('balance')
    ).join(StudentFeeAccount)\
     .group_by(Student.id)\
     .having(func.sum(StudentFeeAccount.balance) > 0)\
     .order_by(func.sum(StudentFeeAccount.balance).desc())\
     .all()
    
    pdf_file = FeeExportService.export_defaulters_to_pdf(defaulters)
    
    filename = f'Defaulters_List_{datetime.now().strftime("%Y%m%d")}.pdf'
    
    return send_file(
        pdf_file,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=filename
    )


@fee_bp.route('/balance-report/export/excel')
@fee_access_required
def export_balance_report_excel():
    """Export balance report to Excel"""
    from flask import send_file
    from services.fee_export_service import FeeExportService
    from sqlalchemy import func
    
    # Get all students with their balances
    students_data = db.session.query(
        Student,
        func.sum(StudentFeeAccount.total_amount).label('total_fees'),
        func.sum(StudentFeeAccount.amount_paid).label('total_paid'),
        func.sum(StudentFeeAccount.balance).label('balance')
    ).join(StudentFeeAccount)\
     .group_by(Student.id)\
     .order_by(Student.name)\
     .all()
    
    students_with_balances = []
    for student, total_fees, total_paid, balance in students_data:
        status = 'Paid' if balance == 0 else 'Partial' if total_paid > 0 else 'Not Paid'
        students_with_balances.append({
            'name': student.name,
            'admission_number': student.admission_number,
            'grade': student.grade.name if student.grade else 'N/A',
            'stream': student.stream.name if student.stream else 'N/A',
            'total_fees': total_fees,
            'total_paid': total_paid,
            'balance': balance,
            'status': status
        })
    
    excel_file = FeeExportService.export_balance_report_to_excel(students_with_balances)
    
    filename = f'Balance_Report_{datetime.now().strftime("%Y%m%d")}.xlsx'
    
    return send_file(
        excel_file,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=filename
    )


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
    
    # Create JSON-serializable version for autocomplete
    invoices_json = [{
        'id': inv.id,
        'invoice_number': inv.invoice_number,
        'student': {
            'name': inv.student.name if inv.student else 'N/A',
            'admission_number': inv.student.admission_number if inv.student else 'N/A'
        },
        'total_amount': float(inv.total_amount),
        'status': inv.status
    } for inv in invoices]
    
    return render_template('fees/invoice_list.html',
                         invoices=invoices,
                         invoices_json=invoices_json,
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


@fee_bp.route('/invoice/<int:invoice_id>/send', methods=['POST'])
@fee_access_required
def send_invoice(invoice_id):
    """Send invoice via SMS or Email"""
    from new_structure.models.fee_management import FeeInvoice
    from new_structure.models.parent import Parent, ParentStudent
    
    try:
        data = request.get_json()
        method = data.get('method', 'email')  # 'sms' or 'email'
        
        invoice = FeeInvoice.query.get_or_404(invoice_id)
        student = Student.query.get(invoice.student_id)
        
        if not student:
            return jsonify({'success': False, 'message': 'Student not found'}), 404
        
        # Get parent information
        parent_student = ParentStudent.query.filter_by(student_id=student.id, relationship='parent').first()
        if not parent_student:
            return jsonify({'success': False, 'message': 'No parent linked to this student'}), 404
        
        parent = Parent.query.get(parent_student.parent_id)
        if not parent:
            return jsonify({'success': False, 'message': 'Parent information not found'}), 404
        
        # Prepare invoice message
        message = f"""
Dear {parent.first_name} {parent.last_name},

Fee Invoice for {student.name}
Invoice No: {invoice.invoice_number}
Term: {invoice.term} ({invoice.academic_year})
Amount: KES {invoice.total_amount:,.2f}
Due Date: {invoice.due_date.strftime('%d %b %Y') if invoice.due_date else 'N/A'}

Please visit the school or parent portal to make payment.

Hillview School
        """.strip()
        
        if method == 'sms':
            # Check if parent has phone number
            if not parent.phone:
                return jsonify({'success': False, 'message': 'Parent phone number not available'}), 400
            
            # TODO: Integrate with actual SMS API (Africa's Talking, Twilio, etc.)
            # For now, we'll just log the message
            print(f"[SMS] To: {parent.phone}")
            print(f"[SMS] Message: {message}")
            
            return jsonify({
                'success': True,
                'message': f'Invoice sent via SMS to {parent.phone}'
            })
        
        elif method == 'email':
            # Check if parent has email
            if not parent.email:
                return jsonify({'success': False, 'message': 'Parent email not available'}), 400
            
            # TODO: Integrate with actual Email service (SendGrid, Mailgun, etc.)
            # For now, we'll just log the message
            print(f"[EMAIL] To: {parent.email}")
            print(f"[EMAIL] Subject: Fee Invoice #{invoice.invoice_number} - {student.name}")
            print(f"[EMAIL] Body: {message}")
            
            return jsonify({
                'success': True,
                'message': f'Invoice sent via Email to {parent.email}'
            })
        
        else:
            return jsonify({'success': False, 'message': 'Invalid method. Use "sms" or "email"'}), 400
    
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


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
    
    # Get all fee accounts - prioritize current term but show all available
    accounts = StudentFeeAccount.query.filter_by(
        student_id=student.id
    ).join(FeeStructure).order_by(
        StudentFeeAccount.academic_year.desc(),
        StudentFeeAccount.term.desc(),
        FeeStructure.allocation_priority
    ).all()
    
    # If no accounts exist at all, show helpful message
    if not accounts:
        flash(f'⚠️ No fee accounts found for {student.name}. Fee accounts are created when you generate invoices or will be auto-created when recording payments for a specific term.', 'warning')
        return redirect(url_for('fees.index'))
    
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


@fee_bp.route('/payment/<int:payment_id>/edit', methods=['GET', 'POST'])
@fee_access_required
def edit_payment(payment_id):
    """Edit an existing payment"""
    payment = Payment.query.get_or_404(payment_id)
    student = Student.query.get(payment.student_id)
    
    if request.method == 'POST':
        try:
            # Get old amount before updating
            old_amount = payment.amount
            new_amount = Decimal(request.form['amount'])
            amount_difference = new_amount - old_amount
            allocation_mode = request.form.get('allocation_mode', 'auto')
            
            # Update payment details (only fields that exist in Payment model)
            payment.amount = new_amount
            payment.method_id = int(request.form['method_id'])
            payment.reference = request.form.get('reference', '')
            payment.notes = request.form.get('notes', payment.notes)
            payment.allocation_mode = allocation_mode
            
            # If amount changed, need to re-allocate
            if amount_difference != 0:
                # Remove existing allocations
                for allocation in payment.allocations:
                    account = allocation.student_fee_account
                    account.amount_paid -= allocation.amount_allocated
                    account.balance += allocation.amount_allocated
                    account.status = 'unpaid' if account.amount_paid == 0 else 'partial'
                    db.session.delete(allocation)
                
                # Commit payment changes before re-allocation
                db.session.commit()
                
                # Handle allocation mode
                if allocation_mode == 'manual':
                    # Redirect to manual allocation page
                    flash(f'✅ Payment updated to KES {new_amount}. Now allocate to specific fees.', 'info')
                    return redirect(url_for('fees.allocate_payment_manual', payment_id=payment.id))
                
                # Auto-allocate new amount using same logic as record_payment
                # Get student's outstanding fees by priority
                current_term = "Term 1"  # You can make this dynamic
                current_year = "2025-2026"
                
                accounts = StudentFeeAccount.query.filter(
                    StudentFeeAccount.student_id == student.id,
                    StudentFeeAccount.balance > 0,
                    StudentFeeAccount.term == current_term,
                    StudentFeeAccount.academic_year == current_year
                ).join(FeeStructure).order_by(FeeStructure.allocation_priority).all()
                
                remaining = new_amount
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
                    credit = StudentCreditBalance(
                        student_id=student.id,
                        payment_id=payment.id,
                        credit_amount=remaining,
                        remaining_credit=remaining,
                        status='available',
                        notes=f'Credit from edited payment {payment.reference or payment.id}'
                    )
                    db.session.add(credit)
                
                db.session.commit()
                flash(f'✅ Payment updated successfully', 'success')
                return redirect(url_for('fees.index'))
            else:
                # Amount didn't change, just commit the other updates
                db.session.commit()
                flash(f'✅ Payment details updated', 'success')
                return redirect(url_for('fees.index'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating payment: {str(e)}', 'danger')
    
    # GET request
    payment_methods = PaymentMethod.query.all()
    return render_template('fees/edit_payment.html',
                         payment=payment,
                         student=student,
                         payment_methods=payment_methods)


@fee_bp.route('/payment/<int:payment_id>/delete', methods=['POST'])
@fee_access_required
def delete_payment(payment_id):
    """Delete a payment and reverse its allocations"""
    try:
        payment = Payment.query.get_or_404(payment_id)
        student_name = payment.student.name if payment.student else 'Unknown'
        
        # Reverse all allocations
        for allocation in payment.allocations:
            account = allocation.fee_account
            account.amount_paid -= allocation.amount_allocated
            account.balance += allocation.amount_allocated
            account.status = 'unpaid' if account.amount_paid == 0 else 'partial'
            db.session.delete(allocation)
        
        # Delete associated receipt if exists
        if payment.receipt:
            db.session.delete(payment.receipt)
        
        # Delete payment
        db.session.delete(payment)
        db.session.commit()
        
        flash(f'✅ Payment for {student_name} deleted successfully', 'success')
        return jsonify({'success': True, 'message': 'Payment deleted'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@fee_bp.route('/payments/bulk-delete', methods=['POST'])
@fee_access_required
def bulk_delete_payments():
    """Delete multiple payments at once"""
    try:
        data = request.get_json()
        payment_ids = data.get('payment_ids', [])
        
        if not payment_ids:
            return jsonify({'success': False, 'message': 'No payments selected'}), 400
        
        deleted_count = 0
        for payment_id in payment_ids:
            payment = Payment.query.get(payment_id)
            if payment:
                # Reverse allocations
                for allocation in payment.allocations:
                    account = allocation.fee_account
                    account.amount_paid -= allocation.amount_allocated
                    account.balance += allocation.amount_allocated
                    account.status = 'unpaid' if account.amount_paid == 0 else 'partial'
                    db.session.delete(allocation)
                
                # Delete receipt
                if payment.receipt:
                    db.session.delete(payment.receipt)
                
                # Delete payment
                db.session.delete(payment)
                deleted_count += 1
        
        db.session.commit()
        return jsonify({
            'success': True,
            'message': f'Successfully deleted {deleted_count} payment(s)',
            'deleted_count': deleted_count
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@fee_bp.route('/payments/export')
@fee_access_required
def export_payments():
    """Export payments to CSV"""
    import csv
    from io import StringIO
    from flask import make_response
    from datetime import datetime
    
    # Get same filters as index page
    term_filter = request.args.get('term', '')
    grade_filter = request.args.get('grade', '')
    stream_filter = request.args.get('stream', '')
    method_filter = request.args.get('method', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    
    # Build query with same filters
    query = Payment.query.join(Student)
    
    if grade_filter:
        query = query.join(Student.grade).filter(Grade.id == int(grade_filter))
    if stream_filter:
        query = query.join(Student.stream).filter(Stream.id == int(stream_filter))
    if method_filter:
        query = query.filter(Payment.method_id == int(method_filter))
    if date_from:
        query = query.filter(Payment.payment_date >= datetime.strptime(date_from, '%Y-%m-%d'))
    if date_to:
        query = query.filter(Payment.payment_date <= datetime.strptime(date_to, '%Y-%m-%d'))
    
    payments = query.order_by(Payment.payment_date.desc()).all()
    
    # Create CSV
    si = StringIO()
    writer = csv.writer(si)
    
    # Write headers
    writer.writerow([
        'Date', 'Time', 'Student Name', 'Admission No', 'Grade', 'Stream',
        'Amount (KES)', 'Method', 'Reference', 'Recorded By', 'Notes'
    ])
    
    # Write data
    for payment in payments:
        writer.writerow([
            payment.payment_date.strftime('%Y-%m-%d'),
            payment.payment_date.strftime('%H:%M:%S'),
            payment.student.name if payment.student else 'N/A',
            payment.student.admission_number if payment.student else 'N/A',
            payment.student.grade.name if payment.student and payment.student.grade else 'N/A',
            payment.student.stream.name if payment.student and payment.student.stream else 'N/A',
            float(payment.amount),
            payment.method.name if payment.method else 'N/A',
            payment.reference or '',
            payment.recorder.full_name if payment.recorder else 'System',
            payment.notes or ''
        ])
    
    # Create response
    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = f"attachment; filename=payments_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    output.headers["Content-type"] = "text/csv"
    
    return output

