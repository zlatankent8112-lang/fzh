import pytest
from new_structure import create_app
import os

TEST_DB_URI = 'sqlite:///:memory:'

# --- Session-scoped App Lifecycle --------------------------------------------------
# We observed intermittent "app not registered with this SQLAlchemy instance" errors
# during large suite runs when creating *many* Flask app objects (one per test) while
# the pytest-flask plugin's autouse fixtures also accessed the 'app' fixture. Moving
# to a single session-scoped app with a function-scoped database reset eliminates
# engine re-binding races and keeps a stable registration in db._app_engines.

@pytest.fixture(scope='session')
def app():
    os.environ['TEST_SQLALCHEMY_DATABASE_URI'] = TEST_DB_URI
    flask_app = create_app('testing')
    # Provide deterministic test secret key (sufficient length to avoid upgrade loop)
    flask_app.config['SECRET_KEY'] = 'test-session-secret-key-1234567890'
    # Flag this as the central session-scoped test app so other fixtures can detect it
    flask_app.config['_SESSION_SCOPED_TEST_APP'] = True
    flask_app.config['TESTING'] = True
    # Security checks ENABLED by default - individual tests can bypass via fixtures
    flask_app.config['BYPASS_AUTH_FOR_TEST'] = False
    flask_app.config['BYPASS_IP_VALIDATION_FOR_TEST'] = False
    from new_structure.extensions import db
    with flask_app.app_context():
        # Ensure all models are registered before creating tables
        try:
            import importlib
            importlib.import_module('new_structure.models')
        except Exception:
            pass
        db.create_all()
    return flask_app

# Function-scoped DB reset for isolation. Runs BEFORE seeding/other autouse fixtures.
@pytest.fixture(autouse=True)
def _db_reset(app, request):
    if request.node.get_closest_marker('nodb'):
        # Skip DB work for nodb tests
        yield
        return
    # Only operate on our session-scoped test app; skip module-defined custom apps
    if not getattr(app, 'config', {}).get('_SESSION_SCOPED_TEST_APP', False):
        yield
        return
    from new_structure.extensions import db
    # If the provided app isn't registered with this SQLAlchemy instance, skip reset
    try:
        with app.app_context():
            # Ensure all model metadata is loaded before touching the engine
            try:
                import importlib
                # Import the aggregate models package which pulls in all model modules
                importlib.import_module('new_structure.models')
            except Exception as e:
                print(f"[conftest::_db_reset] Model import warning: {e}")
            # Accessing db.engine will raise if app not registered with this db instance
            _ = db.engine  # noqa: F841
            # Drop & recreate all tables to guarantee clean state per test
            try:
                db.drop_all()
                db.create_all()
            except Exception as e:
                # Emit diagnostics and retry once after re-importing concrete modules
                print(f"[conftest::_db_reset] create_all error: {e}; retrying after explicit module imports")
                try:
                    import importlib
                    for mod in (
                        'new_structure.models.academic',
                        'new_structure.models.user',
                        'new_structure.models.assignment',
                        'new_structure.models.permission',
                        'new_structure.models.function_permission',
                        'new_structure.models.report_config',
                        'new_structure.models.school_setup',
                        'new_structure.models.parent',
                        'new_structure.models.access_audit',
                    ):
                        try:
                            importlib.import_module(mod)
                        except Exception:
                            pass
                    db.drop_all()
                    db.create_all()
                except Exception as e2:
                    print(f"[conftest::_db_reset] Retry create_all failed: {e2}")
                    raise
    except Exception:
        # Non-DB tests or custom minimal apps without db.init_app(app)
        yield
        return
    yield
    # Post-test cleanup: ensure session rolled back/removed
    try:
        with app.app_context():
            db.session.rollback()
            db.session.remove()
    except Exception:
        pass

@pytest.fixture()
def client(app):
    """Return test client bound to session-scoped app."""
    return app.test_client()

# Retain no-op override (harmless) – still prevents unexpected plugin reconfiguration.
@pytest.fixture(autouse=True)
def _configure_application():  # type: ignore
    yield

# NOTE: Avoid pushing a global app context per test. Some tests intentionally
# create additional Flask apps (e.g., production-like config checks), and a
# globally pushed session app context can confuse Flask-SQLAlchemy's app
# binding. Tests that need a context should use the provided client, app, or
# explicitly push app.app_context() as they already do in this suite.

@pytest.fixture()
def db_session(app):
    """Provide active db.session for current (reset) database state."""
    from new_structure.extensions import db
    # Keep an active app context PUSHED for the entire test body
    # so that db.session.commit()/queries during the test see the
    # correct current_app and registered SQLAlchemy engine.
    ctx = app.app_context()
    ctx.push()
    try:
        yield db.session
    finally:
        try:
            db.session.rollback()
            db.session.remove()
        except Exception:
            pass
        # Pop the application context last
        try:
            ctx.pop()
        except Exception:
            pass

# Factories
@pytest.fixture()
def grade_factory(db_session):
    from new_structure.models.academic import Grade
    def _create(name, education_level='Primary'):
        g = Grade(name=name, education_level=education_level)
        db_session.add(g)
        db_session.commit()
        return g
    return _create

@pytest.fixture()
def stream_factory(db_session):
    from new_structure.models.academic import Stream
    def _create(name, grade_id):
        s = Stream(name=name, grade_id=grade_id)
        db_session.add(s)
        db_session.commit()
        return s
    return _create

@pytest.fixture()
def student_factory(db_session):
    from new_structure.models.academic import Student
    counter = {'n': 0}
    def _create(grade_id=None, stream_id=None, name=None, promotion_status='active', eligible=True):
        counter['n'] += 1
        student = Student(
            name=name or f"Student {counter['n']}",
            admission_number=f"ADM{1000+counter['n']}",
            grade_id=grade_id,
            stream_id=stream_id,
            is_eligible_for_promotion=eligible,
            promotion_status=promotion_status,
            academic_year='2024-2025'
        )
        db_session.add(student)
        db_session.commit()
        return student
    return _create

@pytest.fixture()
def teacher_factory(db_session):
    from new_structure.models.user import Teacher
    counter = {'n': 0}
    def _create(username=None, role='teacher'):
        counter['n'] += 1
        # Prefix with test_ to avoid collisions with seeded default users
        t = Teacher(
            username=username or f"test_teacher{counter['n']}",
            role=role,
            password='pbkdf2:dummy'  # hashed-like placeholder to satisfy NOT NULL
        )
        db_session.add(t)
        db_session.commit()
        return t
    return _create

# Ensure session cleared before each test to avoid leakage causing auth state differences.
# IMPORTANT: Do NOT depend directly on the client fixture so that nodb-marked tests avoid
# triggering app/client (and thus DB) creation. This prevents config fail-fast tests from
# unintentionally initializing the full application stack.
@pytest.fixture(autouse=True)
def clear_session(request):
    if request.node.get_closest_marker('nodb'):
        return
    client = request.getfixturevalue('client')
    with client.session_transaction() as sess:
        sess.clear()
    # Remove session cookie using public API (avoid private cookie_jar)
    try:
        app = getattr(client, 'application', None)
        cookie_name = None
        if app and getattr(app, 'config', None):
            cookie_name = app.config.get('SESSION_COOKIE_NAME', 'session')
        # Remove without specifying deprecated server_name parameter
        client.delete_cookie(key=cookie_name or 'session')
        if cookie_name and cookie_name != 'session':
            client.delete_cookie(key='session')
    except Exception:
        # Best-effort; a fresh client per test keeps cookies isolated anyway
        pass

# --- Baseline Domain Seeding ------------------------------------------------------
# Many tests assumed per-test app fixture seeded specific baseline entities (teachers, grade/stream, term, assessment type, subjects).
# Provide a unified baseline after each DB reset to eliminate repeated bespoke seeding fixtures and NoneType lookup failures.
@pytest.fixture(autouse=True)
def baseline_seed(_db_reset, app, request):
    if request.node.get_closest_marker('nobaseline') or request.node.get_closest_marker('nodb'):
        # Allow tests to opt-out (e.g., config validation, minimal schema tests)
        return
    # Only seed when using our central session-scoped test app to avoid interfering
    # with tests that build and seed their own minimal apps.
    if not getattr(app, 'config', {}).get('_SESSION_SCOPED_TEST_APP', False):
        return
    from new_structure.extensions import db
    from new_structure.models.academic import Grade, Stream, Term, AssessmentType, Subject
    from new_structure.models.user import Teacher
    from new_structure.models.assignment import TeacherSubjectAssignment
    # Skip seeding when the current app is not registered with this SQLAlchemy instance
    try:
        with app.app_context():
            _ = db.engine  # noqa: F841
    except Exception:
        return
    with app.app_context():
        # Grades & Streams
        grade4 = Grade.query.filter_by(name='Grade 4').first()
        if not grade4:
            grade4 = Grade(name='Grade 4', education_level='upper_primary')
            db.session.add(grade4)
            db.session.flush()
        streamA = Stream.query.filter_by(name='A', grade_id=grade4.id).first()
        if not streamA:
            streamA = Stream(name='A', grade_id=grade4.id)
            db.session.add(streamA)
            db.session.flush()
        # Term & Assessment Types
        term1 = Term.query.filter_by(name='Term 1').first()
        if not term1:
            term1 = Term(name='Term 1', is_current=True)
            db.session.add(term1)
        opener = AssessmentType.query.filter_by(name='Opener').first()
        if not opener:
            opener = AssessmentType(name='Opener', is_active=True)
            db.session.add(opener)
        # Subjects
        math = Subject.query.filter_by(name='Mathematics').first()
        if not math:
            math = Subject(name='Mathematics', education_level='upper_primary')
            db.session.add(math)
        english = Subject.query.filter_by(name='English').first()
        if not english:
            english = Subject(name='English', education_level='upper_primary')
            db.session.add(english)
        # Teachers (headteacher, classteachers ct1/ct2, generic teacher carol)
        head = Teacher.query.filter_by(username='head').first()
        if not head:
            head = Teacher(username='head', role='headteacher', password='pbkdf2:hash')
            db.session.add(head)
        ct1 = Teacher.query.filter_by(username='ct1').first()
        if not ct1:
            ct1 = Teacher(username='ct1', role='classteacher', password='pbkdf2:hash')
            db.session.add(ct1)
            db.session.flush()
        ct2 = Teacher.query.filter_by(username='ct2').first()
        if not ct2:
            ct2 = Teacher(username='ct2', role='classteacher', password='pbkdf2:hash')
            db.session.add(ct2)
        carol = Teacher.query.filter_by(username='carol').first()
        if not carol:
            carol = Teacher(username='carol', role='teacher', password='pbkdf2:hash')
            db.session.add(carol)
        db.session.flush()
        # Ensure ct1 is a class teacher for Grade 4 A (assignment) & subject teacher for math
        existing_assignment = TeacherSubjectAssignment.query.filter_by(teacher_id=ct1.id, subject_id=math.id, grade_id=grade4.id, stream_id=streamA.id).first()
        if not existing_assignment:
            db.session.add(TeacherSubjectAssignment(teacher_id=ct1.id, subject_id=math.id, grade_id=grade4.id, stream_id=streamA.id, is_class_teacher=True))
        # Provide second assignment for ct2 (non-class teacher) for scope tests when they reuse baseline
        existing_assignment_ct2 = TeacherSubjectAssignment.query.filter_by(teacher_id=ct2.id, subject_id=math.id, grade_id=grade4.id, stream_id=streamA.id).first()
        if not existing_assignment_ct2:
            db.session.add(TeacherSubjectAssignment(teacher_id=ct2.id, subject_id=math.id, grade_id=grade4.id, stream_id=streamA.id, is_class_teacher=False))
        db.session.commit()

# --- Fresh App Fixture for Dynamic Route Tests -------------------------------------
# Some tests (e.g., rate limit headers) dynamically add routes; with a session-scoped app
# after first request Flask disallows further route registration. Provide a function-scoped
# fresh_app that mirrors base create_app but isolated for those tests (mark with freshapp).
@pytest.fixture()
def fresh_app(request):
    if not request.node.get_closest_marker('freshapp'):
        # Only supply when explicitly requested to avoid inadvertent divergence
        pytest.skip('fresh_app fixture only available with @pytest.mark.freshapp')
    os.environ['TEST_SQLALCHEMY_DATABASE_URI'] = TEST_DB_URI
    fa = create_app('testing')
    from new_structure.extensions import db as _db
    with fa.app_context():
        _db.create_all()
    return fa

@pytest.fixture()
def fresh_client(fresh_app):
    return fresh_app.test_client()

# --- M-PESA Testing Fixtures --------------------------------------------------------
@pytest.fixture()
def sample_mpesa_transaction(db_session):
    """Create a sample M-PESA transaction for testing"""
    from new_structure.models.fee_management import MpesaTransaction
    transaction = MpesaTransaction(
        phone_number='254712345678',
        amount=1000.00,
        account_reference='TEST001',
        transaction_desc='Test payment',
        merchant_request_id='test-merchant-123',
        checkout_request_id='test-checkout-456',
        status='pending'
    )
    db_session.add(transaction)
    db_session.commit()
    return transaction

@pytest.fixture()
def sample_mpesa_transactions(db_session):
    """Create multiple sample M-PESA transactions"""
    from new_structure.models.fee_management import MpesaTransaction
    from datetime import datetime, timedelta
    
    transactions = []
    for i in range(5):
        txn = MpesaTransaction(
            phone_number=f'25471234567{i}',
            amount=1000.00 * (i + 1),
            account_reference=f'TEST00{i}',
            transaction_desc=f'Test payment {i}',
            merchant_request_id=f'test-merchant-{i}',
            checkout_request_id=f'test-checkout-{i}',
            status='success' if i % 2 == 0 else 'failed',
            created_at=datetime.now() - timedelta(days=i)
        )
        db_session.add(txn)
        transactions.append(txn)
    
    db_session.commit()
    return transactions

@pytest.fixture()
def auth_client(client, app, db_session):
    """Authenticated client for testing protected endpoints"""
    from new_structure.models.user import Teacher
    from flask_login import login_user
    
    # Create a test teacher if not exists
    teacher = Teacher.query.filter_by(username='testteacher').first()
    if not teacher:
        teacher = Teacher(
            username='testteacher',
            full_name='Test Teacher',
            email='test@school.com',
            role='teacher'
        )
        teacher.set_password('testpass123')
        db_session.add(teacher)
        db_session.commit()
    
    # Log in using Flask-Login
    with client:
        with client.session_transaction() as sess:
            sess['_user_id'] = str(teacher.id)
            sess['teacher_id'] = teacher.id
            sess['username'] = teacher.username
            sess['role'] = teacher.role
    return client

@pytest.fixture()
def sample_student(db_session):
    """Create a sample student for testing"""
    from new_structure.models.academic import Student
    student = Student(
        name='Test Student',
        admission_number='ADM001',
        grade_id=1,
        stream_id=1
    )
    db_session.add(student)
    db_session.commit()
    return student

@pytest.fixture()
def sample_students(db_session):
    """Create multiple sample students"""
    from new_structure.models.academic import Student
    students = []
    for i in range(3):
        student = Student(
            name=f'Student {i}',
            admission_number=f'ADM00{i}',
            grade_id=1,
            stream_id=1
        )
        db_session.add(student)
        students.append(student)
    db_session.commit()
    return students

@pytest.fixture()
def mock_callback_success():
    """Mock successful M-PESA callback data"""
    return {
        'Body': {
            'stkCallback': {
                'MerchantRequestID': 'test-merchant-123',
                'CheckoutRequestID': 'test-checkout-456',
                'ResultCode': 0,
                'ResultDesc': 'The service request is processed successfully.',
                'CallbackMetadata': {
                    'Item': [
                        {'Name': 'Amount', 'Value': 1000.00},
                        {'Name': 'MpesaReceiptNumber', 'Value': 'TK123456'},
                        {'Name': 'TransactionDate', 'Value': 20231104143000},
                        {'Name': 'PhoneNumber', 'Value': 254712345678}
                    ]
                }
            }
        }
    }

@pytest.fixture()
def mock_callback_failed():
    """Mock failed M-PESA callback data"""
    return {
        'Body': {
            'stkCallback': {
                'MerchantRequestID': 'test-merchant-123',
                'CheckoutRequestID': 'test-checkout-456',
                # Use 1032 to represent 'Cancelled by user' to align with test expectations
                'ResultCode': 1032,
                'ResultDesc': 'Request cancelled by user'
            }
        }
    }

@pytest.fixture()
def mock_callback_timeout():
    """Mock timeout M-PESA callback data"""
    return {
        'Body': {
            'stkCallback': {
                'MerchantRequestID': 'test-merchant-123',
                'CheckoutRequestID': 'test-checkout-456',
                'ResultCode': 1037,
                'ResultDesc': 'Timeout in completing transaction.'
            }
        }
    }

@pytest.fixture()
def safaricom_ip():
    """Valid Safaricom IP address"""
    return '196.201.214.200'

@pytest.fixture()
def invalid_ip():
    """Invalid IP address"""
    return '192.168.1.1'

@pytest.fixture()
def bypass_auth(app):
    """Fixture to bypass authentication for specific tests"""
    original = app.config.get('BYPASS_AUTH_FOR_TEST', False)
    app.config['BYPASS_AUTH_FOR_TEST'] = True
    yield
    app.config['BYPASS_AUTH_FOR_TEST'] = original

@pytest.fixture()
def bypass_ip_validation(app):
    """Fixture to bypass IP validation for specific tests"""
    original = app.config.get('BYPASS_IP_VALIDATION_FOR_TEST', False)
    app.config['BYPASS_IP_VALIDATION_FOR_TEST'] = True
    yield
    app.config['BYPASS_IP_VALIDATION_FOR_TEST'] = original

@pytest.fixture()
def mock_sms_env(monkeypatch):
    """Mock SMS environment variables"""
    monkeypatch.setenv('SMS_PROVIDER', 'test')
    monkeypatch.setenv('AFRICAS_TALKING_USERNAME', 'test_username')
    monkeypatch.setenv('AFRICAS_TALKING_API_KEY', 'test_api_key')
    monkeypatch.setenv('AFRICAS_TALKING_SENDER_ID', 'TEST')

@pytest.fixture()
def mock_email_env(monkeypatch):
    """Mock Email environment variables"""
    monkeypatch.setenv('SMTP_SERVER', 'smtp.gmail.com')
    monkeypatch.setenv('SMTP_PORT', '587')
    monkeypatch.setenv('SMTP_USERNAME', 'test@example.com')
    monkeypatch.setenv('SMTP_PASSWORD', 'test_password')
    monkeypatch.setenv('SMTP_FROM_EMAIL', 'test@example.com')
    monkeypatch.setenv('SCHOOL_NAME', 'Test School')

@pytest.fixture()
def mock_mpesa_env(monkeypatch):
    """Mock M-PESA environment variables"""
    monkeypatch.setenv('MPESA_ENVIRONMENT', 'test')
    monkeypatch.setenv('MPESA_CONSUMER_KEY', 'test_key_123')
    monkeypatch.setenv('MPESA_CONSUMER_SECRET', 'test_secret_456')
    monkeypatch.setenv('MPESA_SHORTCODE', '174379')
    monkeypatch.setenv('MPESA_PASSKEY', 'test_passkey_789')
    monkeypatch.setenv('MPESA_CALLBACK_URL', 'http://localhost:5000/mpesa/callback')
