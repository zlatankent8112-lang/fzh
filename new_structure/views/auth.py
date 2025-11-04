"""
Authentication views for the Hillview School Management System.
"""
import os
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, abort, send_from_directory, get_flashed_messages, current_app
from ..extensions import csrf, limiter
try:
    from ..services import authenticate_teacher, logout
except ImportError:
    try:
        from services import authenticate_teacher, logout
    except ImportError:
        # Mock functions for testing
        def authenticate_teacher(username, password, role):
            return None
        def logout(session):
            session.clear()

# Simple security decorators for now
def sql_injection_protection(f):
    return f

def auth_rate_limit(f):
    return f

class SQLInjectionProtection:
    @staticmethod
    def validate_input(value, field):
        return True

class RCEProtection:
    @staticmethod
    def detect_code_injection(value):
        return False

# Create a blueprint for authentication routes
auth_bp = Blueprint('auth', __name__)

LOCKOUT_THRESHOLD = 5           # failed attempts
LOCKOUT_WINDOW = timedelta(minutes=30)

def rotate_session():
    """Rotate session identifier to mitigate fixation after successful login."""
    # Flask's signed cookie session does not expose session id; emulate rotation by clearing & re-setting needed keys.
    preserved = {}
    # Keep minimal flash storage only (Flask stores flashes in session['_flashes'])
    flashes = session.get('_flashes')
    if flashes:
        preserved['_flashes'] = flashes
    session.clear()
    for k, v in preserved.items():
        session[k] = v

def register_successful_login(teacher_obj, username, role):
    """Finalize a successful login by initializing a secure, valid session.

    Important: Our max-security middleware enforces both an activity
    timestamp (last_activity) and IP binding (ip_address). If these are not
    initialized at login time, the first request after login will consider
    the session expired or invalid and clear it, leading to an immediate 401.
    """
    rotate_session()
    
    # Flask-Login integration
    from flask_login import login_user
    login_user(teacher_obj, remember=True)
    
    session['teacher_id'] = teacher_obj.id
    session['username'] = username
    session['role'] = role
    session['login_at'] = datetime.utcnow().isoformat()
    # Initialize required security fields so the first GET after login passes
    try:
        import time
        from flask import request
        session['last_activity'] = time.time()
        session['ip_address'] = request.remote_addr
    except Exception:
        # Best-effort; middleware will set them if missing on subsequent requests
        pass
    session.permanent = True
    # Update teacher security metadata
    try:
        from ..extensions import db
        teacher_obj.failed_login_attempts = 0
        teacher_obj.locked_until = None
        teacher_obj.last_login = datetime.utcnow()
        db.session.commit()
    except Exception as e:
        print(f"⚠️ Failed to persist login metadata: {e}")

def register_failed_login(teacher_obj):
    # Increment failed attempts & lock if threshold reached
    try:
        from ..extensions import db
        if teacher_obj:
            teacher_obj.failed_login_attempts = (teacher_obj.failed_login_attempts or 0) + 1
            if teacher_obj.failed_login_attempts >= LOCKOUT_THRESHOLD:
                teacher_obj.locked_until = datetime.utcnow() + LOCKOUT_WINDOW
            db.session.commit()
    except Exception as e:
        print(f"⚠️ Failed to persist failed login metadata: {e}")

def is_locked(teacher_obj):
    if not teacher_obj:
        return False
    if teacher_obj.locked_until and teacher_obj.locked_until > datetime.utcnow():
        return True
    return False

@auth_bp.route('/')
def index():
    """Route for the main index/login page."""
    return render_template('login.html')

@auth_bp.route('/premium')
def premium_login():
    """Premium login page with Tailwind CSS and glassmorphism design"""
    school_info = {
        'school_name': 'Hillview School',
        'school_motto': 'Excellence Through Knowledge and Character',
        'logo_url': None
    }
    return render_template('login_premium.html', school_info=school_info)

@auth_bp.route('/polished')
def polished_login():
    """Ultra-polished dark theme login page matching your design reference"""
    school_info = {
        'school_name': 'Hillview School',
        'school_motto': 'Excellence Through Knowledge and Character',
        'logo_url': None
    }
    return render_template('login_polished.html', school_info=school_info)

@auth_bp.route('/admin_login', methods=['GET', 'POST'])
@limiter.limit("10/minute;3/5second", override_defaults=False)
@sql_injection_protection
def admin_login():
    """Route for headteacher login."""
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        # Input validation
        if not username or not password:
            return render_template('admin_login.html', error='Username and password required')

        # Length validation
        if len(username) > 100 or len(password) > 128:
            return render_template('admin_login.html', error='Invalid credentials')

        # SQL injection protection
        if not SQLInjectionProtection.validate_input(username, "username"):
            return render_template('admin_login.html', error='Invalid credentials')

        # Command injection protection
        if (RCEProtection.detect_code_injection(username) or
            RCEProtection.detect_code_injection(password)):
            return render_template('admin_login.html', error='Invalid credentials')

        # Secure logging - don't expose usernames
        client_ip = request.environ.get('REMOTE_ADDR', 'unknown')
        print(f"🔍 Admin login attempt from IP: {client_ip}")

        try:
            teacher = authenticate_teacher(username, password, 'headteacher')
            if teacher and is_locked(teacher):
                flash('Account temporarily locked. Try again later.', 'error')
                return render_template('admin_login.html', error='Invalid credentials')
            print(f"🔍 Authentication result: {teacher}")

            if teacher:
                print(f"🔍 Setting session for teacher ID: {teacher.id}")
                register_successful_login(teacher, username, 'headteacher')
                print(f"🔍 Redirecting to admin dashboard...")
                return redirect(url_for('admin.dashboard'))
            else:
                print(f"🔍 Authentication failed for: {username}")
                # Attempt to find teacher by username irrespective of role for failed attempt tracking
                try:
                    from ..models.user import Teacher
                    from ..extensions import db
                    candidate = Teacher.query.filter_by(username=username).first()
                except Exception:
                    candidate = None
                register_failed_login(candidate)
                flash('Invalid credentials', 'error')
                return render_template('admin_login.html', error='Invalid credentials')
        except Exception as e:
            print(f"🚨 Authentication error: {str(e)}")
            flash('An error occurred during authentication. Please try again.', 'error')
            return render_template('admin_login.html', error=f'Authentication error: {str(e)}')

    return render_template('admin_login.html')

@auth_bp.route('/teacher_login', methods=['GET', 'POST'])
@limiter.limit("15/minute;5/10second", override_defaults=False)
@sql_injection_protection
def teacher_login():
    """Route for teacher login."""
    # Clear any existing flash messages when accessing login page
    if request.method == 'GET':
        # Clear any stale session data that might cause errors
        session.pop('teacher_id', None)
        session.pop('username', None)
        session.pop('role', None)
        # Clear any flash messages
        get_flashed_messages()

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        # Input validation
        if not username or not password:
            return render_template('teacher_login.html', error='Username and password required')

        # Length validation
        if len(username) > 100 or len(password) > 128:
            return render_template('teacher_login.html', error='Invalid credentials')

        # SQL injection protection
        if not SQLInjectionProtection.validate_input(username, "username"):
            return render_template('teacher_login.html', error='Invalid credentials')

        # Command injection protection
        if (RCEProtection.detect_code_injection(username) or
            RCEProtection.detect_code_injection(password)):
            return render_template('teacher_login.html', error='Invalid credentials')

        # Secure logging - don't expose usernames
        client_ip = request.environ.get('REMOTE_ADDR', 'unknown')
        print(f"🔍 Subject teacher login attempt from IP: {client_ip}")

        try:
            teacher = authenticate_teacher(username, password, 'teacher')
            if teacher and is_locked(teacher):
                flash('Account temporarily locked. Try again later.', 'error')
                return render_template('teacher_login.html', error='Invalid credentials')

            if teacher:
                register_successful_login(teacher, username, 'teacher')
                return redirect(url_for('teacher.dashboard'))
            else:
                try:
                    from ..models.user import Teacher
                    candidate = Teacher.query.filter_by(username=username).first()
                except Exception:
                    candidate = None
                register_failed_login(candidate)
                flash('Invalid credentials', 'error')
                return render_template('teacher_login.html', error='Invalid credentials')
        except Exception as e:
            print(f"🚨 Authentication error: {str(e)}")
            flash('An error occurred during authentication. Please try again.', 'error')
            return render_template('teacher_login.html', error='Invalid credentials')

    return render_template('teacher_login.html')

@auth_bp.route('/classteacher_login', methods=['GET', 'POST'])
@limiter.limit("15/minute;5/10second", override_defaults=False)
@sql_injection_protection
def classteacher_login():
    """Route for class teacher login. Also allows subject teachers with class assignments."""
    print(f"🔍 CLASSTEACHER LOGIN ROUTE HIT! Method: {request.method}")
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        print(f"🔍 Login attempt - Username: {username}, Password length: {len(password)}")

        # Input validation
        if not username or not password:
            return render_template('classteacher_login.html', error='Username and password required')

        # Length validation
        if len(username) > 100 or len(password) > 128:
            return render_template('classteacher_login.html', error='Invalid credentials')

        # SQL injection protection
        if not SQLInjectionProtection.validate_input(username, "username"):
            return render_template('classteacher_login.html', error='Invalid credentials')

        # Command injection protection
        if (RCEProtection.detect_code_injection(username) or
            RCEProtection.detect_code_injection(password)):
            return render_template('classteacher_login.html', error='Invalid credentials')

        # Secure logging - don't expose usernames
        client_ip = request.environ.get('REMOTE_ADDR', 'unknown')
        print(f"🔍 Class teacher login attempt from IP: {client_ip}")

        try:
            # First try to authenticate as classteacher
            teacher = authenticate_teacher(username, password, 'classteacher')
            if teacher and is_locked(teacher):
                flash('Account temporarily locked. Try again later.', 'error')
                return render_template('classteacher_login.html', error='Invalid credentials')

            if teacher:
                register_successful_login(teacher, username, 'classteacher')
                return redirect(url_for('classteacher.dashboard'))

            # If classteacher auth failed, try as subject teacher with class assignments
            teacher = authenticate_teacher(username, password, 'teacher')

            if teacher:
                # Check if this subject teacher has class assignments
                from ..services.flexible_marks_service import FlexibleMarksService
                can_access = FlexibleMarksService.can_teacher_access_classteacher_portal(teacher.id)

                if can_access:
                    register_successful_login(teacher, username, 'teacher')  # Keep original role but allow access
                    return redirect(url_for('classteacher.dashboard'))
                else:
                    flash('You don\'t have any class assignments. Please use the subject teacher portal.', 'error')
                    return render_template('classteacher_login.html',
                                         error='You don\'t have any class assignments. Please use the subject teacher portal.')
            
            try:
                from ..models.user import Teacher
                candidate = Teacher.query.filter_by(username=username).first()
            except Exception:
                candidate = None
            register_failed_login(candidate)
            flash('Invalid credentials', 'error')
            return render_template('classteacher_login.html', error='Invalid credentials')

        except Exception as e:
            print(f"🚨 Authentication error: {str(e)}")
            flash('An error occurred during authentication. Please try again.', 'error')
            return render_template('classteacher_login.html', error='An error occurred during authentication. Please try again.')

    return render_template('classteacher_login.html')

@auth_bp.route('/logout')
def logout_route():
    """Route for logging out."""
    logout(session)
    return redirect(url_for('auth.index'))

# Guard debug/simple login route if present in create_app main __init__ (we do not redefine here)
@auth_bp.before_app_request
def protect_debug_login():
    if request.endpoint and 'debug_simple_login' in request.endpoint:
        if not current_app.debug and not current_app.config.get('ENABLE_DEBUG_LOGIN', False):
            return abort(404)

@auth_bp.route('/mobile-test')
def mobile_test():
    """Mobile responsive test page."""
    return render_template('mobile_test.html')

@auth_bp.route('/dashboard-mobile-test')
def dashboard_mobile_test():
    """Comprehensive Headteacher Dashboard - Main dashboard for school management."""
    # Check authentication
    if not session.get('teacher_id') or session.get('role') != 'headteacher':
        flash('Access denied. Please login as headteacher.', 'error')
        return redirect(url_for('auth.admin_login'))

    # Comprehensive dashboard data
    mock_data = {
        'school_info': {
            'school_name': 'Hillview School',
            'school_motto': 'Excellence Through Knowledge and Character',
            'logo_url': '/static/images/default_logo.png'
        },
        'current_academic_year': '2025/2026',
        'current_term': 'Term 1',
        'total_students': 450,
        'total_teachers': 28,
        'total_classes': 18,
        'total_subjects': 12,
        'avg_performance': 78.5,
        'recent_activities': [
            {'type': 'info', 'message': 'New student enrollment completed', 'time': '2 hours ago'},
            {'type': 'success', 'message': 'Monthly reports generated', 'time': '1 day ago'},
            {'type': 'warning', 'message': 'Grade 5 Math assessment pending', 'time': '2 days ago'}
        ],
        'performance_alerts': [
            {'grade': 'Grade 3', 'subject': 'Mathematics', 'status': 'needs_attention', 'percentage': 65},
            {'grade': 'Grade 6', 'subject': 'English', 'status': 'excellent', 'percentage': 92}
        ],
        'system_alerts': [
            {'type': 'info', 'message': 'System backup completed successfully', 'time': '1 hour ago'},
            {'type': 'warning', 'message': 'Server maintenance scheduled for weekend', 'time': '3 hours ago'}
        ],
        'quick_stats': {
            'attendance_rate': 94.2,
            'teacher_satisfaction': 87.5,
            'parent_engagement': 76.8,
            'academic_progress': 82.3
        },
        'learners_per_grade': {
            'Grade 1': 65,
            'Grade 2': 58,
            'Grade 3': 62,
            'Grade 4': 55,
            'Grade 5': 60,
            'Grade 6': 57,
            'Grade 7': 53,
            'Grade 8': 48,
            'Grade 9': 42
        },
        'upcoming_assessments': [
            {'name': 'Mathematics Mid-Term', 'date': '2025-08-15', 'grade': 'Grade 5', 'type': 'exam'},
            {'name': 'English Literature Test', 'date': '2025-08-18', 'grade': 'Grade 6', 'type': 'test'},
            {'name': 'Science Project Review', 'date': '2025-08-22', 'grade': 'Grade 4', 'type': 'project'}
        ],
        'performance_data': [
            {
                'grade': 'Grade 1', 'stream': 'A', 'term': 'Term 1', 'assessment_type': 'Mid-Term',
                'total_students': 32, 'class_average': 78.5,
                'performance_counts': {'EE1': 8, 'EE2': 6, 'ME1': 10, 'ME2': 5, 'AE1': 2, 'AE2': 1, 'BE1': 0, 'BE2': 0}
            },
            {
                'grade': 'Grade 2', 'stream': 'A', 'term': 'Term 1', 'assessment_type': 'End-Term',
                'total_students': 29, 'class_average': 82.3,
                'performance_counts': {'EE1': 9, 'EE2': 7, 'ME1': 8, 'ME2': 3, 'AE1': 2, 'AE2': 0, 'BE1': 0, 'BE2': 0}
            }
        ],
        'gender_per_grade': {
            'Grade 1': {'Male': 35, 'Female': 30},
            'Grade 2': {'Male': 30, 'Female': 28},
            'Grade 3': {'Male': 32, 'Female': 30},
            'Grade 4': {'Male': 28, 'Female': 27},
            'Grade 5': {'Male': 31, 'Female': 29},
            'Grade 6': {'Male': 29, 'Female': 28},
            'Grade 7': {'Male': 27, 'Female': 26},
            'Grade 8': {'Male': 25, 'Female': 23},
            'Grade 9': {'Male': 22, 'Female': 20}
        },
        'total_assessments': 24,
        'top_class': 'Grade 2A',
        'top_class_score': 85.2,
        'least_performing_grade': 'Grade 4',
        'least_grade_score': 72.1,
        'performance_distribution': {
            'E.E': 45,
            'M.E': 32,
            'A.E': 18,
            'B.E': 5
        },
        'streams_per_grade': {
            'Grade 1': {
                'A': {'total': 32, 'Male': 18, 'Female': 14},
                'B': {'total': 33, 'Male': 17, 'Female': 16}
            },
            'Grade 2': {
                'A': {'total': 29, 'Male': 15, 'Female': 14},
                'B': {'total': 29, 'Male': 15, 'Female': 14}
            },
            'Grade 3': {
                'A': {'total': 31, 'Male': 16, 'Female': 15},
                'B': {'total': 31, 'Male': 16, 'Female': 15}
            },
            'Grade 4': {
                'A': {'total': 28, 'Male': 14, 'Female': 14},
                'B': {'total': 27, 'Male': 14, 'Female': 13}
            },
            'Grade 5': {
                'A': {'total': 30, 'Male': 16, 'Female': 14},
                'B': {'total': 30, 'Male': 15, 'Female': 15}
            },
            'Grade 6': {
                'A': {'total': 29, 'Male': 15, 'Female': 14},
                'B': {'total': 28, 'Male': 14, 'Female': 14}
            },
            'Grade 7': {
                'A': {'total': 27, 'Male': 14, 'Female': 13},
                'B': {'total': 26, 'Male': 13, 'Female': 13}
            },
            'Grade 8': {
                'A': {'total': 25, 'Male': 13, 'Female': 12},
                'B': {'total': 23, 'Male': 12, 'Female': 11}
            },
            'Grade 9': {
                'A': {'total': 22, 'Male': 11, 'Female': 11},
                'B': {'total': 20, 'Male': 11, 'Female': 9}
            }
        }
    }
    return render_template('headteacher.html', **mock_data)

@auth_bp.route('/dashboard-mobile-demo')
def dashboard_mobile_demo():
    """Comprehensive dashboard mobile responsive demo page."""
    return render_template('dashboard_mobile_test.html')

@auth_bp.route('/classteacher-mobile-test')
def classteacher_mobile_test():
    """Class teacher mobile responsive test page."""
    # Mock data for testing
    mock_data = {
        'school_info': {
            'school_name': 'Hillview School',
            'logo_url': '/static/images/default_logo.png'
        },
        'grade': '8',
        'stream': 'A',
        'subjects': ['Mathematics', 'English', 'Science'],
        'students': []
    }
    return render_template('classteacher.html', **mock_data)

@auth_bp.route('/teacher-mobile-test')
def teacher_mobile_test():
    """Teacher mobile responsive test page."""
    # Mock data for testing
    mock_data = {
        'school_info': {
            'school_name': 'Hillview School',
            'logo_url': '/static/images/default_logo.png'
        },
        'teacher_name': 'John Doe',
        'subjects': ['Mathematics', 'Physics'],
        'classes': []
    }
    return render_template('teacher.html', **mock_data)

@auth_bp.route('/forms-mobile-demo')
def forms_mobile_demo():
    """Forms and file upload mobile responsive demo page."""
    return render_template('forms_mobile_demo.html')

@auth_bp.route('/charts-analytics-mobile-demo')
def charts_analytics_mobile_demo():
    """Charts and analytics mobile responsive demo page."""
    return render_template('charts_analytics_mobile_demo.html')

@auth_bp.route('/student-portal-mobile-demo')
def student_portal_mobile_demo():
    """Student portal mobile responsive demo page."""
    return render_template('student_portal_mobile_demo.html')

@auth_bp.route('/mobile-performance-demo')
def mobile_performance_demo():
    """Mobile performance optimization demo page."""
    return render_template('mobile_performance_dashboard.html')

@auth_bp.route('/offline')
def offline():
    """Offline page for PWA."""
    return render_template('offline.html')

@auth_bp.route('/manifest.json')
def manifest():
    """Web App Manifest for PWA."""
    return send_from_directory('static', 'manifest.json', mimetype='application/manifest+json')

@auth_bp.route('/pwa-demo')
def pwa_demo():
    """Progressive Web App demo page."""
    return render_template('pwa_demo.html')
