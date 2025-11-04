"""
Views package for the Hillview School Management System.
This file imports and exposes view blueprints for registration with the Flask app.

Important: Use absolute imports (new_structure.views.*) so that this package
can be imported either as `new_structure.views` or as `views` (as some tests do)
without triggering relative import errors.
"""
from new_structure.views.auth import auth_bp
from new_structure.views.api_teacher import api_teacher_bp
from new_structure.views.teacher import teacher_bp
# Import classteacher with error handling due to security_helpers dependency
try:
    from new_structure.views.classteacher import classteacher_bp
    classteacher_available = True
except ImportError as e:
    print(f"WARNING: Classteacher blueprint not available: {e}")
    classteacher_bp = None
    classteacher_available = False
from new_structure.views.admin import admin_bp
from new_structure.views.bulk_assignments import bulk_assignments_bp
from new_structure.views.setup import setup_bp
from new_structure.views.staff_management import staff_bp
from new_structure.views.permission_management import permission_bp
from new_structure.views.headteacher_universal import universal_bp
from new_structure.views.analytics_api import analytics_api_bp
from new_structure.views.school_setup import school_setup_bp
from new_structure.views.subject_config_api import subject_config_api
from new_structure.views.missing_routes import missing_routes_bp
from new_structure.views.mobile_performance_api import mobile_performance_api

# Import parent portal blueprints with error handling
try:
    from new_structure.views.parent_simple import parent_simple_bp
    parent_bp_available = True
except ImportError:
    parent_simple_bp = None
    parent_bp_available = False

# Import parent management blueprint with error handling
try:
    from new_structure.views.parent_management import parent_management_bp
    parent_mgmt_bp_available = True
except ImportError:
    parent_management_bp = None
    parent_mgmt_bp_available = False

# Import email configuration blueprint with error handling - TEMPORARILY DISABLED
# try:
#     from .email_config import email_config_bp
#     email_config_bp_available = True
# except ImportError:
email_config_bp = None
email_config_bp_available = False

# Import fee management blueprint with error handling
try:
    from new_structure.views.fee_management import fee_bp
    fee_bp_available = True
    print("[OK] Fee management blueprint imported successfully")
except ImportError as e:
    print(f"[ERROR] Failed to import fee management blueprint: {e}")
    import traceback
    traceback.print_exc()
    fee_bp = None
    fee_bp_available = False

# Import M-PESA integration blueprint with error handling
try:
    from new_structure.views.mpesa import mpesa_bp
    mpesa_bp_available = True
    print("[OK] M-PESA blueprint imported successfully")
except ImportError as e:
    print(f"[ERROR] Failed to import M-PESA blueprint: {e}")
    import traceback
    traceback.print_exc()
    mpesa_bp = None
    mpesa_bp_available = False

# List of all blueprints to register with the app
blueprints = [
    auth_bp, teacher_bp, admin_bp,
    bulk_assignments_bp, setup_bp, staff_bp,
    permission_bp, universal_bp, analytics_api_bp,
    school_setup_bp, subject_config_api, missing_routes_bp,
    mobile_performance_api,
    api_teacher_bp
]

# Add classteacher blueprint if available
if classteacher_available and classteacher_bp:
    blueprints.append(classteacher_bp)

# Add parent blueprint if available
if parent_bp_available and parent_simple_bp:
    blueprints.append(parent_simple_bp)

# Add parent management blueprint if available
if parent_mgmt_bp_available and parent_management_bp:
    blueprints.append(parent_management_bp)

# Add email configuration blueprint if available
if email_config_bp_available and email_config_bp:
    blueprints.append(email_config_bp)

# Add fee management blueprint if available
if fee_bp_available and fee_bp:
    blueprints.append(fee_bp)
    print(f"[OK] Fee blueprint added to registration list (URL prefix: {fee_bp.url_prefix})")
else:
    print("[ERROR] Fee blueprint NOT added to registration list")

# Add M-PESA blueprint if available
if mpesa_bp_available and mpesa_bp:
    blueprints.append(mpesa_bp)
    print(f"[OK] M-PESA blueprint added to registration list (URL prefix: {mpesa_bp.url_prefix})")
else:
    print("[ERROR] M-PESA blueprint NOT added to registration list")