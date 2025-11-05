"""
User-related models for the Hillview School Management System.
"""
import hmac
from new_structure.extensions import db
from .associations import teacher_subjects
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin

# Association table imported from models.associations to avoid circular imports

class Teacher(UserMixin, db.Model):
    """Teacher model representing school staff members.

    The canonical hashed password is stored in the 'password' column.
    Legacy password_hash alias has been removed after migration cleanup.
    """
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    # Canonical password column
    password = db.Column(db.String(255), nullable=False)
    # Legacy column that still exists in database (keep for backward compatibility)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), nullable=False)  # e.g., 'headteacher', 'teacher', 'classteacher'
    stream_id = db.Column(db.Integer, db.ForeignKey('stream.id'), nullable=True)

    # Personal Information
    first_name = db.Column(db.String(100), nullable=True)
    last_name = db.Column(db.String(100), nullable=True)
    email = db.Column(db.String(100), nullable=True)
    phone = db.Column(db.String(20), nullable=True)

    # Professional Information
    employee_id = db.Column(db.String(50), nullable=True, unique=True)
    qualification = db.Column(db.String(50), nullable=True)  # P1, DIPLOMA, DEGREE, MASTERS, PHD
    specialization = db.Column(db.String(100), nullable=True)  # Subject specialization
    date_joined = db.Column(db.Date, nullable=True)
    is_active = db.Column(db.Boolean, default=True)

    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

    # Authentication Security Fields (A2 hardening)
    failed_login_attempts = db.Column(db.Integer, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)
    last_login = db.Column(db.DateTime, nullable=True)
    # NOTE: If using an existing production database, create Alembic migration to add:
    #   failed_login_attempts INT DEFAULT 0
    #   locked_until DATETIME NULL
    #   last_login DATETIME NULL
    # These are additive, backwards-compatible columns.

    # Relationships (use callables to avoid ambiguous string lookups across re-imports)
    stream = db.relationship(
        lambda: __import__('new_structure.models.academic', fromlist=['Stream']).Stream,
        backref=db.backref('teachers', lazy=True)
    )
    subjects = db.relationship(
        lambda: __import__('new_structure.models.academic', fromlist=['Subject']).Subject,
        secondary=teacher_subjects,
        back_populates='teachers'
    )

    def set_password(self, password):
        """Set (and hash) the user's password using secure hashing only.

        Plain-text storage in the legacy `password` column is deprecated. We now
        mirror the hashed value into `password` simply to satisfy the NOT NULL
        constraint until the column is dropped in a future migration.
        """
        if not password:
            return

        # Basic password quality (non-breaking) guidance – only warn for legacy weak passwords.
        # Future enforcement can raise ValueError, but we keep compatibility with existing short test passwords.
        pwd = str(password)
        if len(pwd) < 6:
            # Too short for modern policy, but allow for backward compatibility.
            pass
        # Hash (Werkzeug defaults to PBKDF2-HMAC-SHA256)
        hashed = generate_password_hash(pwd)
        self.password = hashed
        # Also set password_hash for backward compatibility with existing database schema
        self.password_hash = hashed

    def check_password(self, password):
        """Validate password using the canonical hashed 'password' column."""
        if not password or not isinstance(password, str) or len(password) > 128:
            return False
        # Minimal injection style validation (defense in depth)
        try:
            from new_structure.security.sql_injection_protection import SQLInjectionProtection
            if not SQLInjectionProtection.validate_input(password, "password"):
                return False
        except ImportError:
            pass
        if not self.password:
            return False
        if self.password.startswith(('scrypt:', 'pbkdf2:')):
            return check_password_hash(self.password, password)
        return hmac.compare_digest(self.password, password)

    def is_password_hashed(self):
        """Return True if canonical password column stores a modern hash."""
        return bool(self.password and self.password.startswith(('scrypt:', 'pbkdf2:')))

    @property
    def full_name(self):
        """Get the full name of the teacher."""
        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name}"
        if self.first_name:
            return self.first_name
        if self.last_name:
            return self.last_name
        return self.username