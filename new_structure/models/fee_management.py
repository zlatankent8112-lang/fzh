"""
Fee Management models for the Hillview School Management System.
Handles fee structures, payments, invoices, and related financial data.
"""
from new_structure.extensions import db
from datetime import datetime


class FeeStructure(db.Model):
    """
    Define fee types and amounts per grade/stream/term.
    Examples: Tuition, Transport, Lunch, Boarding, Activity fees, etc.
    """
    __tablename__ = 'fee_structure'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Fee Type Information
    fee_type_name = db.Column(db.String(100), nullable=False)  # e.g., "Tuition Fee", "Transport Fee"
    description = db.Column(db.Text, nullable=True)  # Detailed description
    amount = db.Column(db.Numeric(10, 2), nullable=False)  # Fee amount in KES
    
    # Academic Context
    academic_year = db.Column(db.String(10), nullable=False)  # e.g., "2024", "2025"
    term = db.Column(db.String(20), nullable=True)  # "Term 1", "Term 2", "Term 3", "Annual"
    term_id = db.Column(db.Integer, db.ForeignKey('term.id'), nullable=True)  # Link to Term table
    
    # Grade/Stream Linkage (Optional - can be grade-specific or school-wide)
    grade_id = db.Column(db.Integer, db.ForeignKey('grade.id'), nullable=True)  # Specific grade
    education_level = db.Column(db.String(50), nullable=True)  # 'lower_primary', 'upper_primary', 'junior_secondary'
    stream_id = db.Column(db.Integer, db.ForeignKey('stream.id'), nullable=True)  # Specific stream (optional)
    
    # Fee Characteristics
    is_mandatory = db.Column(db.Boolean, default=True)  # Must be paid by all students
    is_boarding = db.Column(db.Boolean, default=False)  # Boarding school fee vs Day school
    frequency = db.Column(db.String(20), default='per_term')  # 'per_term', 'annual', 'one_time', 'monthly'
    category = db.Column(db.String(50), nullable=True)  # 'tuition', 'meals', 'transport', 'remedial', 'admission', 'caution', 'admin', 'activities'
    is_refundable = db.Column(db.Boolean, default=False)  # For caution fees, deposits, etc.
    applies_to_grades = db.Column(db.Text, nullable=True)  # JSON array of grade IDs: "[1,2,3]" for conditional application
    
    # Payment Allocation Settings (HYBRID APPROACH)
    allocation_priority = db.Column(db.Integer, default=1)  # 1 = highest priority (paid first)
    allow_partial_payment = db.Column(db.Boolean, default=True)  # Can this fee be partially paid?
    
    # Status
    is_active = db.Column(db.Boolean, default=True)  # Can be deactivated without deleting
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('teacher.id'), nullable=True)  # Who created this fee
    
    # Relationships
    grade = db.relationship('Grade', backref='fee_structures', lazy=True)
    stream = db.relationship('Stream', backref='fee_structures', lazy=True)
    term_obj = db.relationship('Term', foreign_keys=[term_id], backref='fee_structures', lazy=True)
    creator = db.relationship('Teacher', backref='created_fee_structures', lazy=True)
    
    def __repr__(self):
        return f'<FeeStructure {self.fee_type_name} - KES {self.amount} ({self.term}/{self.academic_year})>'
    
    def to_dict(self):
        """Convert to dictionary for JSON responses"""
        return {
            'id': self.id,
            'fee_type_name': self.fee_type_name,
            'description': self.description,
            'amount': float(self.amount) if self.amount else 0,
            'academic_year': self.academic_year,
            'term': self.term,
            'grade_id': self.grade_id,
            'grade_name': self.grade.name if self.grade else None,
            'education_level': self.education_level,
            'stream_id': self.stream_id,
            'stream_name': self.stream.name if self.stream else None,
            'is_mandatory': self.is_mandatory,
            'is_boarding': self.is_boarding,
            'frequency': self.frequency,
            'allocation_priority': self.allocation_priority,
            'allow_partial_payment': self.allow_partial_payment,
            'is_active': self.is_active,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else None
        }
    
    @classmethod
    def get_active_fees_for_grade(cls, grade_id, term, academic_year):
        """Get all active fees for a specific grade and term"""
        from new_structure.models.academic import Grade
        
        # Get the grade to determine education level
        grade = Grade.query.get(grade_id)
        if not grade:
            return []
        
        # Map grade name to education level
        # Education levels: pre_primary, lower_primary, upper_primary, junior_secondary, senior_secondary
        grade_name = grade.name.upper()
        if 'PP' in grade_name or 'PRE' in grade_name:
            education_level = 'pre_primary'
        elif any(num in grade_name for num in ['1', '2', '3']) and 'GRADE' in grade_name:
            education_level = 'lower_primary'
        elif any(num in grade_name for num in ['4', '5', '6']) and 'GRADE' in grade_name:
            education_level = 'upper_primary'
        elif any(num in grade_name for num in ['7', '8', '9']) and 'GRADE' in grade_name:
            education_level = 'junior_secondary'
        elif any(num in grade_name for num in ['10', '11', '12']) and 'GRADE' in grade_name:
            education_level = 'senior_secondary'
        else:
            education_level = None
        
        # Get fees by education level (since grade_id is NULL in fee_structure)
        if education_level:
            # Match academic year pattern (e.g., '2025' matches '2025-2026')
            # Also match fees with term=NULL (All Terms) or specific term
            from sqlalchemy import or_
            fees = cls.query.filter(
                cls.education_level == education_level,
                cls.is_active == True,
                or_(cls.term == term, cls.term == None),  # Match specific term or "All Terms"
                cls.academic_year.like(f'{academic_year}%')  # '2025' matches '2025-2026'
            ).all()
            return fees
        else:
            # Fallback to grade_id matching (for legacy/specific grade fees)
            from sqlalchemy import or_
            fees = cls.query.filter(
                cls.grade_id == grade_id,
                cls.is_active == True,
                or_(cls.term == term, cls.term == None),
                cls.academic_year.like(f'{academic_year}%')
            ).all()
            return fees
    
    @classmethod
    def get_school_wide_fees(cls, term, academic_year):
        """Get fees that apply to all students (no specific grade)"""
        from sqlalchemy import or_
        return cls.query.filter(
            cls.grade_id == None,
            cls.education_level == None,
            cls.is_active == True,
            or_(cls.term == term, cls.term == None),
            cls.academic_year.like(f'{academic_year}%')
        ).all()
    
    @classmethod
    def calculate_total_fees_for_student(cls, student, term, academic_year):
        """Calculate total fees for a student for a given term"""
        from decimal import Decimal
        
        # Get grade-specific fees
        grade_fees = cls.get_active_fees_for_grade(
            student.grade_id, 
            term, 
            academic_year
        )
        
        # Get school-wide fees (not grade-specific)
        school_fees = cls.get_school_wide_fees(term, academic_year)
        
        # Combine both
        all_fees = grade_fees + school_fees
        
        # Calculate total
        total = Decimal('0.00')
        for fee in all_fees:
            if fee.is_mandatory:
                total += fee.amount
        
        return total
    
    @classmethod
    def get_fees_by_priority(cls, grade_id, term, academic_year):
        """Get active fees sorted by allocation priority (for auto-allocation)"""
        # Use the updated get_active_fees_for_grade which handles education_level mapping
        grade_fees = cls.get_active_fees_for_grade(grade_id, term, academic_year)
        
        # Get school-wide fees
        school_fees = cls.query.filter_by(
            grade_id=None,
            term=term,
            academic_year=academic_year,
            is_active=True
        ).order_by(cls.allocation_priority.asc()).all()
        
        # Combine and sort by priority
        all_fees = grade_fees + school_fees
        return sorted(all_fees, key=lambda x: x.allocation_priority)


class StudentFeeAccount(db.Model):
    """
    Individual student fee obligations. Links students to specific fees.
    Tracks balance, payments, due dates, and status for each fee per student.
    """
    __tablename__ = 'student_fee_account'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Student & Fee Linkage
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    fee_structure_id = db.Column(db.Integer, db.ForeignKey('fee_structure.id'), nullable=False)
    
    # Academic Context
    academic_year = db.Column(db.String(10), nullable=False)
    term = db.Column(db.String(20), nullable=False)
    term_id = db.Column(db.Integer, db.ForeignKey('term.id'), nullable=True)
    
    # Financial Tracking
    total_amount = db.Column(db.Numeric(10, 2), nullable=False)  # Total fee amount owed
    amount_paid = db.Column(db.Numeric(10, 2), default=0.00)  # Total amount paid so far
    balance = db.Column(db.Numeric(10, 2), nullable=False)  # Remaining balance (total - paid)
    
    # Discount/Waiver (if applicable)
    discount_amount = db.Column(db.Numeric(10, 2), default=0.00)  # Scholarship/discount applied
    discount_reason = db.Column(db.Text, nullable=True)  # Reason for discount
    
    # Due Date & Status
    due_date = db.Column(db.Date, nullable=True)  # When fee is due
    status = db.Column(db.String(20), default='pending')  # 'pending', 'partial', 'paid', 'overdue', 'waived'
    is_overdue = db.Column(db.Boolean, default=False)  # Calculated field
    
    # Payment Plan (if student is on installment)
    has_payment_plan = db.Column(db.Boolean, default=False)
    payment_plan_id = db.Column(db.Integer, nullable=True)  # Link to payment plan (future)
    
    # Notes
    notes = db.Column(db.Text, nullable=True)  # Special notes about this fee for this student
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_payment_date = db.Column(db.DateTime, nullable=True)  # When last payment was received
    
    # Relationships
    student = db.relationship('Student', backref='fee_accounts', lazy=True)
    fee_structure = db.relationship('FeeStructure', backref='student_accounts', lazy=True)
    term_obj = db.relationship('Term', foreign_keys=[term_id], backref='student_fee_accounts', lazy=True)
    
    def __repr__(self):
        return f'<StudentFeeAccount Student:{self.student_id} Fee:{self.fee_structure.fee_type_name if self.fee_structure else "N/A"} Balance:KES{self.balance}>'
    
    def to_dict(self):
        """Convert to dictionary for JSON responses"""
        return {
            'id': self.id,
            'student_id': self.student_id,
            'student_name': self.student.name if self.student else None,
            'fee_structure_id': self.fee_structure_id,
            'fee_type_name': self.fee_structure.fee_type_name if self.fee_structure else None,
            'academic_year': self.academic_year,
            'term': self.term,
            'total_amount': float(self.total_amount) if self.total_amount else 0,
            'amount_paid': float(self.amount_paid) if self.amount_paid else 0,
            'balance': float(self.balance) if self.balance else 0,
            'discount_amount': float(self.discount_amount) if self.discount_amount else 0,
            'discount_reason': self.discount_reason,
            'due_date': self.due_date.strftime('%Y-%m-%d') if self.due_date else None,
            'status': self.status,
            'is_overdue': self.is_overdue,
            'has_payment_plan': self.has_payment_plan,
            'notes': self.notes,
            'last_payment_date': self.last_payment_date.strftime('%Y-%m-%d %H:%M:%S') if self.last_payment_date else None
        }
    
    def update_balance(self):
        """Recalculate balance after payment/discount changes"""
        from decimal import Decimal
        self.balance = Decimal(str(self.total_amount)) - Decimal(str(self.amount_paid)) - Decimal(str(self.discount_amount))
        
        # Update status based on balance
        if self.balance <= 0:
            self.status = 'paid'
        elif self.amount_paid > 0:
            self.status = 'partial'
        else:
            self.status = 'pending'
        
        db.session.commit()
    
    def check_overdue_status(self):
        """Check if fee is overdue"""
        from datetime import date
        if self.due_date and self.balance > 0:
            self.is_overdue = date.today() > self.due_date
            db.session.commit()
    
    @classmethod
    def get_student_fees_summary(cls, student_id, term, academic_year):
        """Get all fees for a student in a given term"""
        return cls.query.filter_by(
            student_id=student_id,
            term=term,
            academic_year=academic_year
        ).all()
    
    @classmethod
    def calculate_total_balance(cls, student_id, term, academic_year):
        """Calculate total balance for a student in a given term"""
        from decimal import Decimal
        accounts = cls.get_student_fees_summary(student_id, term, academic_year)
        total_balance = Decimal('0.00')
        for account in accounts:
            total_balance += account.balance
        return total_balance
    
    @classmethod
    def get_overdue_accounts(cls, student_id=None):
        """Get all overdue fee accounts (optionally filtered by student)"""
        from datetime import date
        query = cls.query.filter(
            cls.due_date < date.today(),
            cls.balance > 0
        )
        if student_id:
            query = query.filter_by(student_id=student_id)
        return query.all()
    
    @classmethod
    def create_accounts_for_student(cls, student, term, academic_year, due_date=None):
        """
        Auto-create fee accounts when a student enrolls for a term.
        Links student to all applicable fees based on their grade.
        """
        from decimal import Decimal
        
        # Get all applicable fees for this student's grade
        applicable_fees = FeeStructure.get_active_fees_for_grade(
            student.grade_id,
            term,
            academic_year
        )
        
        # Add school-wide fees
        school_fees = FeeStructure.get_school_wide_fees(term, academic_year)
        applicable_fees.extend(school_fees)
        
        created_accounts = []
        for fee in applicable_fees:
            # Check if account already exists
            existing = cls.query.filter_by(
                student_id=student.id,
                fee_structure_id=fee.id,
                term=term,
                academic_year=academic_year
            ).first()
            
            if not existing:
                account = cls(
                    student_id=student.id,
                    fee_structure_id=fee.id,
                    academic_year=academic_year,
                    term=term,
                    total_amount=fee.amount,
                    amount_paid=Decimal('0.00'),
                    balance=fee.amount,
                    due_date=due_date,
                    status='pending'
                )
                db.session.add(account)
                created_accounts.append(account)
        
        db.session.commit()
        return created_accounts


class PaymentMethod(db.Model):
    """
    Payment methods configuration (Cash, M-PESA, Bank, Cheque, etc.).
    """
    __tablename__ = 'payment_method'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)  # e.g., Cash, M-PESA, Bank
    description = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    # Optional JSON configuration for gateways (kept text for MySQL 8 compatibility)
    config_json = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<PaymentMethod {self.name} active={self.is_active}>'


class Payment(db.Model):
    """
    Payment records made by or on behalf of a student.
    allocation_mode: 'auto' pays highest-priority outstanding fees first; 'manual' uses PaymentAllocation rows provided.
    """
    __tablename__ = 'payment'

    id = db.Column(db.Integer, primary_key=True)

    # Who/when/how
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    method_id = db.Column(db.Integer, db.ForeignKey('payment_method.id'), nullable=True)
    payment_date = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    currency = db.Column(db.String(10), default='KES')
    reference = db.Column(db.String(100), nullable=True)  # MPESA code, bank slip, receipt no., etc.
    allocation_mode = db.Column(db.String(10), default='auto')  # 'auto' | 'manual'
    notes = db.Column(db.Text, nullable=True)
    recorded_by = db.Column(db.Integer, db.ForeignKey('teacher.id'), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    student = db.relationship('Student', backref='payments', lazy=True)
    method = db.relationship('PaymentMethod', backref='payments', lazy=True)
    recorder = db.relationship('Teacher', backref='recorded_payments', lazy=True)

    def __repr__(self):
        return f'<Payment id={self.id} student={self.student_id} amount=KES{self.amount} mode={self.allocation_mode}>'


class PaymentAllocation(db.Model):
    """
    Allocation of a payment amount to specific student fee accounts.
    """
    __tablename__ = 'payment_allocation'

    id = db.Column(db.Integer, primary_key=True)
    payment_id = db.Column(db.Integer, db.ForeignKey('payment.id'), nullable=False)
    student_fee_account_id = db.Column(db.Integer, db.ForeignKey('student_fee_account.id'), nullable=False)
    amount_allocated = db.Column(db.Numeric(10, 2), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    payment = db.relationship('Payment', backref='allocations', lazy=True)
    student_fee_account = db.relationship('StudentFeeAccount', backref='allocations', lazy=True)

    def __repr__(self):
        return f'<PaymentAllocation payment={self.payment_id} account={self.student_fee_account_id} amount=KES{self.amount_allocated}>'


class FeeInvoice(db.Model):
    """Invoices generated for students (per term/year)."""
    __tablename__ = 'fee_invoice'

    id = db.Column(db.Integer, primary_key=True)
    invoice_number = db.Column(db.String(50), unique=True, nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    academic_year = db.Column(db.String(10), nullable=False)
    term = db.Column(db.String(20), nullable=False)
    issue_date = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    due_date = db.Column(db.Date, nullable=True)
    total_amount = db.Column(db.Numeric(10, 2), nullable=False)
    status = db.Column(db.String(20), default='issued')  # issued, partially_paid, paid, canceled
    generated_by = db.Column(db.Integer, db.ForeignKey('teacher.id'), nullable=True)
    notes = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    student = db.relationship('Student', backref='fee_invoices', lazy=True)
    generator = db.relationship('Teacher', backref='generated_invoices', lazy=True)


class FeeWaiver(db.Model):
    """Scholarships/discounts granted to a student or a specific fee account."""
    __tablename__ = 'fee_waiver'

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    student_fee_account_id = db.Column(db.Integer, db.ForeignKey('student_fee_account.id'), nullable=True)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    reason = db.Column(db.Text, nullable=True)
    approved_by = db.Column(db.Integer, db.ForeignKey('teacher.id'), nullable=True)
    approved_at = db.Column(db.DateTime, default=datetime.utcnow)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    student = db.relationship('Student', backref='fee_waivers', lazy=True)
    approver = db.relationship('Teacher', backref='approved_waivers', lazy=True)
    account = db.relationship('StudentFeeAccount', backref='waivers', lazy=True)


class FeeReminder(db.Model):
    """Log of reminders sent (SMS/Email)."""
    __tablename__ = 'fee_reminder'

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    channel = db.Column(db.String(20), nullable=False)  # sms, email
    message = db.Column(db.Text, nullable=False)
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default='sent')  # sent, failed, queued
    delivery_ref = db.Column(db.String(100), nullable=True)

    student = db.relationship('Student', backref='fee_reminders', lazy=True)


class Receipt(db.Model):
    """Official receipt for a Payment (printable)."""
    __tablename__ = 'receipt'

    id = db.Column(db.Integer, primary_key=True)
    receipt_number = db.Column(db.String(50), unique=True, nullable=False)
    payment_id = db.Column(db.Integer, db.ForeignKey('payment.id'), nullable=False)
    issued_at = db.Column(db.DateTime, default=datetime.utcnow)
    issued_by = db.Column(db.Integer, db.ForeignKey('teacher.id'), nullable=True)
    notes = db.Column(db.Text, nullable=True)

    payment = db.relationship('Payment', backref='receipt', uselist=False, lazy=True)
    issuer = db.relationship('Teacher', backref='issued_receipts', lazy=True)


class MpesaTransaction(db.Model):
    """M-PESA transaction log (manual/automated import)."""
    __tablename__ = 'mpesa_transaction'

    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.String(50), unique=True, nullable=False)  # e.g., LNB7QW123
    phone_number = db.Column(db.String(20), nullable=True)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    trans_time = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default='received')  # received, matched, applied, failed
    result_code = db.Column(db.String(10), nullable=True)
    raw_payload = db.Column(db.Text, nullable=True)
    payment_id = db.Column(db.Integer, db.ForeignKey('payment.id'), nullable=True)

    payment = db.relationship('Payment', backref='mpesa_transactions', lazy=True)





class FeeWaiver(db.Model):
    """Scholarships/discounts record applied to a specific StudentFeeAccount."""
    __tablename__ = 'fee_waiver'

    id = db.Column(db.Integer, primary_key=True)
    student_fee_account_id = db.Column(db.Integer, db.ForeignKey('student_fee_account.id'), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    reason = db.Column(db.Text, nullable=True)
    approved_by = db.Column(db.Integer, db.ForeignKey('teacher.id'), nullable=True)
    approved_at = db.Column(db.DateTime, default=datetime.utcnow)

    account = db.relationship('StudentFeeAccount', backref='waivers', lazy=True)
    approver = db.relationship('Teacher', backref='approved_waivers', lazy=True)

    def __repr__(self):
        return f'<FeeWaiver account={self.student_fee_account_id} amount=KES{self.amount}>'


class FeeReminder(db.Model):
    """SMS/Email reminder logs for fee follow-up."""
    __tablename__ = 'fee_reminder'

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    channel = db.Column(db.String(10), default='sms')  # sms | email
    message = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default='sent')  # sent | failed
    invoice_id = db.Column(db.Integer, db.ForeignKey('fee_invoice.id'), nullable=True)
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)

    student = db.relationship('Student', backref='fee_reminders', lazy=True)
    invoice = db.relationship('FeeInvoice', backref='reminders', lazy=True)

    def __repr__(self):
        return f'<FeeReminder student={self.student_id} channel={self.channel} status={self.status}>'


class Receipt(db.Model):
    """Official receipt linked to a Payment."""
    __tablename__ = 'receipt'

    id = db.Column(db.Integer, primary_key=True)
    receipt_number = db.Column(db.String(50), unique=True, nullable=False)
    payment_id = db.Column(db.Integer, db.ForeignKey('payment.id'), nullable=False)
    issued_at = db.Column(db.DateTime, default=datetime.utcnow)
    issued_by = db.Column(db.Integer, db.ForeignKey('teacher.id'), nullable=True)

    payment = db.relationship('Payment', backref='receipt', lazy=True, uselist=False)
    issuer = db.relationship('Teacher', backref='issued_receipts', lazy=True)

    def __repr__(self):
        return f'<Receipt {self.receipt_number} payment={self.payment_id}>'


class MpesaTransaction(db.Model):
    """M-PESA transactions captured for later reconciliation to Payments."""
    __tablename__ = 'mpesa_transaction'

    id = db.Column(db.Integer, primary_key=True)
    mpesa_code = db.Column(db.String(30), unique=True, nullable=False)
    phone = db.Column(db.String(20), nullable=True)
    payer_name = db.Column(db.String(100), nullable=True)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    received_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default='received')  # received | applied | failed
    raw_payload = db.Column(db.Text, nullable=True)
    payment_id = db.Column(db.Integer, db.ForeignKey('payment.id'), nullable=True)

    payment = db.relationship('Payment', backref='mpesa_transactions', lazy=True)

    def __repr__(self):
        return f'<MpesaTransaction {self.mpesa_code} amount=KES{self.amount} status={self.status}>'


class StudentCreditBalance(db.Model):
    """
    Track credit balances (overpayments) for students.
    These credits can be applied to future term fees or refunded.
    """
    __tablename__ = 'student_credit_balance'
    
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    payment_id = db.Column(db.Integer, db.ForeignKey('payment.id'), nullable=True)  # Source payment
    
    # Credit amount
    credit_amount = db.Column(db.Numeric(10, 2), nullable=False)
    remaining_credit = db.Column(db.Numeric(10, 2), nullable=False)
    
    # Status tracking
    status = db.Column(db.String(20), default='available')  # available | applied | refunded | expired
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    applied_at = db.Column(db.DateTime, nullable=True)
    refunded_at = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    
    # Relationships
    student = db.relationship('Student', backref='credit_balances', lazy=True)
    payment = db.relationship('Payment', backref='credit_balances', lazy=True)
    
    def __repr__(self):
        return f'<StudentCreditBalance student_id={self.student_id} remaining=KES{self.remaining_credit} status={self.status}>'


class CreditTransfer(db.Model):
    """
    Track credit transfers between siblings.
    Requires parent authorization and admin approval.
    """
    __tablename__ = 'credit_transfer'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Transfer details
    from_student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    to_student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    credit_balance_id = db.Column(db.Integer, db.ForeignKey('student_credit_balance.id'), nullable=False)
    
    # Amount transferred
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    
    # Authorization and approval
    authorized_by = db.Column(db.String(100), nullable=True)  # Parent name/email
    authorization_date = db.Column(db.DateTime, nullable=True)
    approved_by = db.Column(db.Integer, db.ForeignKey('teacher.id'), nullable=True)  # Admin/headteacher
    approved_at = db.Column(db.DateTime, nullable=True)
    
    # Status
    status = db.Column(db.String(20), default='pending')  # pending | approved | rejected | completed
    
    # Metadata
    reason = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)
    
    # Relationships
    from_student = db.relationship('Student', foreign_keys=[from_student_id], backref='credit_transfers_out', lazy=True)
    to_student = db.relationship('Student', foreign_keys=[to_student_id], backref='credit_transfers_in', lazy=True)
    credit_balance = db.relationship('StudentCreditBalance', backref='transfers', lazy=True)
    approver = db.relationship('Teacher', backref='approved_credit_transfers', lazy=True)
    
    def __repr__(self):
        return f'<CreditTransfer from={self.from_student_id} to={self.to_student_id} amount=KES{self.amount} status={self.status}>'
