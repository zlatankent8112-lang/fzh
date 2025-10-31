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
    frequency = db.Column(db.String(20), default='termly')  # 'termly', 'annual', 'monthly'
    
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
        return cls.query.filter_by(
            grade_id=grade_id,
            term=term,
            academic_year=academic_year,
            is_active=True
        ).all()
    
    @classmethod
    def get_school_wide_fees(cls, term, academic_year):
        """Get fees that apply to all students (no specific grade)"""
        return cls.query.filter_by(
            grade_id=None,
            term=term,
            academic_year=academic_year,
            is_active=True
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
        # Get grade-specific fees
        grade_fees = cls.query.filter_by(
            grade_id=grade_id,
            term=term,
            academic_year=academic_year,
            is_active=True
        ).order_by(cls.allocation_priority.asc()).all()
        
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
