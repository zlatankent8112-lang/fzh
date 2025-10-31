"""
Models package for the Hillview School Management System.
This file imports and exposes all models for easy access.
"""
# Import database instance
try:
    from new_structure.extensions import db
except ImportError:
    from extensions import db
from .user import Teacher, teacher_subjects
from .academic import (
    SchoolConfiguration, Subject, Grade, Stream, Term,
    AssessmentType, Student, Mark, StudentPromotionHistory
)
from .assignment import TeacherSubjectAssignment
from .report_config import ReportConfiguration, ClassReportConfiguration, ReportTemplate
from .school_setup import SchoolSetup, SchoolBranding, SchoolCustomization
from .permission import ClassTeacherPermission, PermissionRequest
from .function_permission import FunctionPermission, DefaultFunctionPermissions
from .access_audit import AccessAudit
from .assessment_config import AssessmentWeightsConfig, MissingPolicyConfig
from .rounding_config import RoundingModeConfig

# Import parent portal models (with error handling for backward compatibility)
try:
    from .parent import Parent, ParentStudent, ParentEmailLog, EmailTemplate
except ImportError:
    # Parent models not available yet
    Parent = None
    ParentStudent = None
    ParentEmailLog = None
    EmailTemplate = None

# Import fee management models (with error handling for backward compatibility)
try:
    from .fee_management import FeeStructure
except ImportError:
    # Fee management models not available yet
    FeeStructure = None