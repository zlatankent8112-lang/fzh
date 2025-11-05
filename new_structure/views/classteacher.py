"""
Class Teacher views for the Hillview School Management System.
"""
import json
import logging
import time  # added for timing metrics in report preview
import threading
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, send_file, jsonify, make_response, abort
from security_helpers import secure_endpoint, ValidationError, wants_json, audit_log, validate_uploaded_file
from werkzeug.security import generate_password_hash
from sqlalchemy import text
import pandas as pd
import os
import traceback
from io import BytesIO
from werkzeug.utils import secure_filename
from ..models import Grade, Stream, Subject, Term, AssessmentType, Student, Mark, Teacher, TeacherSubjectAssignment
from ..utils.safe_get import safe_get
from ..models.academic import SubjectMarksStatus, ComponentMark
from ..utils.constants import educational_level_mapping
from ..services import is_authenticated, get_role, get_class_report_data, generate_individual_report, generate_class_report_pdf, RoleBasedDataService
from ..security.authorization import enforce
from ..services.report_service import generate_class_report_pdf_from_html
from ..services.mark_conversion_service import MarkConversionService
from ..services.subject_aggregation_service import aggregate_subjects_for_display, get_aggregated_abbreviated_subjects
from ..extensions import db, limiter, csrf
from ..utils.error_responses import error_response
from ..utils import get_performance_category, get_performance_remarks
from ..services.cache_service import (
    cache_marksheet, get_cached_marksheet,
    cache_report, get_cached_report,
    cache_pdf, get_cached_pdf,
    invalidate_cache
)
from ..services.collaborative_marks_service import CollaborativeMarksService
from ..services.grade_report_service import GradeReportService
from ..services.enhanced_permission_service import EnhancedPermissionService, function_permission_required
from ..services.report_config_service import ReportConfigService
from ..utils.database_health import check_database_health, create_missing_tables, safe_table_operation
from ..services.permission_service import PermissionService
from ..models.function_permission import DefaultFunctionPermissions
from ..services.flexible_marks_service import FlexibleMarksService
from ..services.grade_marksheet_service import GradeMarksheetService
from functools import wraps
from .decorators import dev_only

# In-memory progress tracker for long-running ZIP generations
_zip_generation_progress = {}
_zip_progress_lock = threading.Lock()


def _set_zip_progress(key: str, **kwargs):
    """Merge progress data for a given key in a thread-safe manner."""
    with _zip_progress_lock:
        current = _zip_generation_progress.get(key, {}).copy()
        current.update(kwargs)
        current.setdefault('updated_at', time.time())
        _zip_generation_progress[key] = current
        return current.copy()


def _get_zip_progress(key: str):
    """Return a shallow copy of progress for a key."""
    with _zip_progress_lock:
        return _zip_generation_progress.get(key, {}).copy()


def _clear_zip_progress(key: str):
    with _zip_progress_lock:
        _zip_generation_progress.pop(key, None)


def _schedule_zip_progress_cleanup(key: str, delay_seconds: int = 15):
    """Schedule asynchronous cleanup of a progress entry once finished."""
    def _cleanup():
        _clear_zip_progress(key)

    timer = threading.Timer(delay_seconds, _cleanup)
    timer.daemon = True
    timer.start()

# Create a blueprint for class teacher routes
classteacher_bp = Blueprint('classteacher', __name__, url_prefix='/classteacher')

# Set up logger
logger = logging.getLogger(__name__)

# --- Flexible resolvers for Term and AssessmentType names ---
def _resolve_term_object(term_input):
    """Resolve a Term row from flexible input like 'term 3', 'Term 3', 'T3', 'III', etc.

    Returns a tuple: (Term object or None, canonical term name or None)
    """
    try:
        if term_input is None:
            return None, None
        s = str(term_input).strip()
        if not s:
            return None, None
        sl = s.lower()
        import re
        # Determine term number if possible
        n = None
        m = re.search(r'(\d)', sl)
        if m:
            d = int(m.group(1))
            if d in (1, 2, 3):
                n = d
        if n is None:
            if any(tok in sl for tok in ("t1", "term 1", "term i")) or sl in ("i", "one"):
                n = 1
            elif any(tok in sl for tok in ("t2", "term 2", "term ii")) or sl in ("ii", "two"):
                n = 2
            elif any(tok in sl for tok in ("t3", "term 3", "term iii")) or sl in ("iii", "three"):
                n = 3

        if n:
            target = f"Term {n}"
            term = Term.query.filter(db.func.lower(Term.name) == target.lower()).first()
            if term:
                return term, term.name

        # Fallbacks: exact lower and partial ILIKE
        term = Term.query.filter(db.func.lower(Term.name) == sl).first()
        if term:
            return term, term.name
        term = Term.query.filter(Term.name.ilike(f"%{s}%")).first()
        if term:
            return term, term.name
    except Exception:
        pass
    return None, None

def _normalize_assessment_bucket(text: str):
    """Map arbitrary assessment text to a canonical bucket: 'entrance' | 'midterm' | 'endterm'."""
    if not text:
        return None
    t = (text or '').lower()
    import re
    # Remove years and numbers; unify separators
    t = re.sub(r'\d{4}', ' ', t)  # remove likely years
    t = re.sub(r'\d+', ' ', t)
    t = t.replace('_', ' ').replace('-', ' ')
    tokens = set([tok for tok in t.split() if tok])
    # Entrance/opener
    if {'opener'} & tokens or {'entrance'} & tokens or {'opening'} & tokens or ({'start', 'term'} <= tokens):
        return 'entrance'

    # Midterm
    if {'mid', 'midterm', 'mid_term'} & tokens or ({'middle', 'term'} <= tokens):
        return 'midterm'
    # Endterm/closing
    if {'endterm', 'end_term', 'closing', 'final'} & tokens or ({'end', 'term'} <= tokens):
        return 'endterm'
    return None

def _resolve_assessment_type_object(assessment_input):
    """Resolve an AssessmentType row from flexible input like 'opener', 'midterm', 'end term', etc.

    Returns a tuple: (AssessmentType object or None, canonical name or None)
    """
    try:
        if not assessment_input:
            return None, None
        bucket = _normalize_assessment_bucket(assessment_input)
        types = AssessmentType.query.all()
        for at in types:
            if bucket is not None and _normalize_assessment_bucket(at.name) == bucket:
                return at, at.name
        # Fallbacks
        s = str(assessment_input).strip()
        sl = s.lower()
        at = AssessmentType.query.filter(db.func.lower(AssessmentType.name) == sl).first()
        if at:
            return at, at.name
        at = AssessmentType.query.filter(AssessmentType.name.ilike(f"%{s}%")).first()
        if at:
            return at, at.name
    except Exception:
        pass
    return None, None

# Register template filter for education level (blueprint scope)
@classteacher_bp.app_template_filter('get_education_level')
def get_education_level_blueprint(grade):
    """Return canonical education level code for a given grade name."""
    try:
        from ..utils.constants import get_education_level_for_grade_name
        return get_education_level_for_grade_name(grade)
    except Exception:
        return ''

def classteacher_required(_func=None, *, allowed_roles=("classteacher", "headteacher")):
    """Decorator enforcing authentication, role restriction & session integrity.

    Can be used as:
        @classteacher_required
        def view(): ...

        @classteacher_required(allowed_roles=("headteacher",))
        def view(): ...
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not is_authenticated(session):
                if request.accept_mimetypes.accept_json or request.is_json:
                    return jsonify({"error": "Authentication required"}), 401
                return redirect(url_for('auth.classteacher_login'))
            role = (session.get('role') or '').lower()
            if role not in allowed_roles:
                if request.accept_mimetypes.accept_json or request.is_json:
                    return jsonify({"error": "Forbidden"}), 403
                abort(403)
            teacher_id = session.get('teacher_id')
            if not isinstance(teacher_id, int):
                if request.accept_mimetypes.accept_json or request.is_json:
                    return jsonify({"error": "Invalid session"}), 401
                return redirect(url_for('auth.classteacher_login'))
            return f(*args, **kwargs)
        return wrapper
    if _func is not None and callable(_func):
        return decorator(_func)
    return decorator

# Decorator for requiring class teacher OR teacher authentication (for shared routes)
def teacher_or_classteacher_required(f):
    """Decorator to require teacher or class teacher authentication for a route."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_authenticated(session):
            return redirect(url_for('auth.teacher_login'))

        try:
            role = get_role(session)
        except Exception:
            role = None

        # Allow both roles; fallback deny if role unexpected
        if role not in ('teacher', 'classteacher'):
            return redirect(url_for('auth.teacher_login'))

        return f(*args, **kwargs)
    return decorated_function

# ---------------------------------------------------------------------------
# Overview Route (placed after decorators to ensure definitions exist)
# ---------------------------------------------------------------------------
@classteacher_bp.route('/class_overview', methods=['GET'])
@limiter.limit("60 per minute")
@classteacher_required()
def class_overview():
    """Lightweight class overview page referenced in templates.

    Builds minimal class assignment cards so existing template renders.
    """
    from ..services.school_config_service import SchoolConfigService
    teacher_id = session.get('teacher_id')
    teacher = safe_get(Teacher, teacher_id) if teacher_id else None

    classes_data = []
    try:  # Build richer overview data with per-subject status
        if teacher_id:
            # Resolve assignments (fallback to teacher's own stream if no explicit assignment records)
            direct_stream = safe_get(Stream, teacher.stream_id) if (teacher and teacher.stream_id) else None
            try:
                assignment_summary = RoleBasedDataService.get_teacher_assignments_summary(teacher_id, 'classteacher')
            except Exception:
                assignment_summary = {}
            class_assignments = assignment_summary.get('class_teacher_assignments', []) if assignment_summary else []
            if not class_assignments and direct_stream:
                g_obj = safe_get(Grade, direct_stream.grade_id) if direct_stream else None
                class_assignments = [{
                    'grade_id': g_obj.id if g_obj else None,
                    'grade_name': g_obj.name if g_obj else '',
                    'stream_id': direct_stream.id,
                    'stream_name': direct_stream.name
                }]

            # Current term & default assessment (prefer current/active)
            current_term = Term.query.filter_by(is_current=True).first() or Term.query.first()
            default_assessment = AssessmentType.query.filter_by(is_active=True).order_by(AssessmentType.id.asc()).first() or AssessmentType.query.first()

            for assignment in class_assignments:
                grade_id = assignment.get('grade_id') or assignment.get('gradeId')
                stream_id = assignment.get('stream_id') or assignment.get('streamId')
                if not (grade_id and stream_id):
                    continue
                grade_obj = safe_get(Grade, grade_id)
                stream_obj = safe_get(Stream, stream_id)
                if not stream_obj:
                    continue

                # Robust grade name resolution (avoid <Grade 21> repr leaking to UI)
                raw_grade_name = assignment.get('grade_name') or assignment.get('grade') or ''
                if hasattr(raw_grade_name, 'name') and not grade_obj:
                    # raw_grade_name might itself be a Grade instance
                    try:  # pragma: no cover - defensive
                        raw_grade_name = raw_grade_name.name
                    except Exception:
                        raw_grade_name = ''
                # Use DB name if available
                display_grade_name = grade_obj.name if grade_obj and hasattr(grade_obj, 'name') else raw_grade_name
                if isinstance(display_grade_name, str):
                    # Normalize casing (e.g., GRADE 9 -> Grade 9)
                    if display_grade_name.upper().startswith('GRADE '):
                        parts = display_grade_name.strip().split()
                        if len(parts) == 2 and parts[1].isdigit():
                            display_grade_name = f"Grade {parts[1]}"
                    elif display_grade_name.upper() in ('PP1', 'PP2'):
                        display_grade_name = display_grade_name.upper()
                else:
                    display_grade_name = str(display_grade_name)

                term_obj = current_term
                assess_obj = default_assessment
                term_name = term_obj.name if term_obj else 'Term'
                assessment_name = assess_obj.name if assess_obj else 'Assessment'

                # Subjects constrained by grade's education level if available
                if grade_obj and grade_obj.education_level:
                    subjects_query = Subject.query.filter_by(education_level=grade_obj.education_level)
                else:
                    subjects_query = Subject.query
                subjects_list = subjects_query.all()
                total_subjects = len(subjects_list)

                # Preload students in stream for per-subject completion percentage
                student_ids = [s.id for s in Student.query.filter_by(stream_id=stream_id).all()]
                student_count = len(student_ids)

                # Fetch marks for this stream / term / assessment once to avoid N+1
                marks = Mark.query.filter(
                    Mark.stream_id == stream_id,
                    Mark.term_id == (term_obj.id if term_obj else Mark.term_id),
                    Mark.assessment_type_id == (assess_obj.id if assess_obj else Mark.assessment_type_id)
                ).all()

                # Organize marks by subject -> set(student_id)
                marks_by_subject = {}
                for m in marks:
                    marks_by_subject.setdefault(m.subject_id, set()).add(m.student_id)

                subject_status = []
                completed_subjects_counter = 0
                for subj in subjects_list:
                    student_marks = marks_by_subject.get(subj.id, set())
                    subj_completion_pct = (len(student_marks) / student_count * 100) if student_count else 0
                    is_uploaded = subj_completion_pct > 0
                    if is_uploaded:
                        completed_subjects_counter += 1
                    subject_status.append({
                        'id': subj.id,
                        'name': subj.name,
                        'is_uploaded': is_uploaded,
                        'completion_percentage': subj_completion_pct
                    })

                overall_completion_percentage = (completed_subjects_counter / total_subjects * 100) if total_subjects else 0

                classes_data.append({
                    'grade': display_grade_name,
                    'grade_id': grade_id,
                    'stream': stream_obj.name if stream_obj else (assignment.get('stream_name') or ''),
                    'stream_id': stream_id,
                    'term_name': term_name,
                    'term_id': term_obj.id if term_obj else None,
                    'assessment_name': assessment_name,
                    'assessment_type_id': assess_obj.id if assess_obj else None,
                    'total_subjects': total_subjects,
                    'completed_subjects': completed_subjects_counter,
                    'completion_percentage': overall_completion_percentage,
                    'subjects': subject_status,
                    # Enable report only when all subjects have some marks (100%)
                    'can_generate_report': overall_completion_percentage >= 100
                })
    except Exception as e:  # pragma: no cover
        print(f"⚠️ class_overview generation issue: {e}")

    try:
        school_info = SchoolConfigService.get_school_info_dict()
    except Exception:
        school_info = {}

    return render_template(
        'classteacher/class_overview.html',
        teacher=teacher,
        classes_data=classes_data,
        school_info=school_info
    )
@classteacher_bp.route('/preview_class_report/<grade>/<stream>/<term>/<assessment_type>', methods=['GET', 'POST'])
@secure_endpoint(roles=['headteacher','classteacher','teacher'], rate=(20,60), audit_event='report.class.preview')
def preview_class_report(grade, stream, term, assessment_type):
    """Unified class report route (refactored to service).

    Behavior should remain consistent with previous implementation; logic has
    been extracted to `ClassReportBuilder` for maintainability.
    """
    # --- Input validation (path params) ---
    from flask import current_app as _ca
    _log = _ca.logger
    start_time = time.time()
    _log.debug('preview_class_report.enter', extra={'grade': grade, 'stream': stream, 'term': term, 'assessment_type': assessment_type, 'session_role': session.get('role'), 'session_teacher_id': session.get('teacher_id')})
    validation_warning = False
    from ..services.validation_schemas import ClassReportPathSchema
    from marshmallow import ValidationError as _VE
    schema = ClassReportPathSchema()
    try:
        schema.load({'grade': grade, 'stream': stream, 'term': term, 'assessment_type': assessment_type})
    except _VE as ve:  # true validation error
        validation_warning = True
        try:
            _log.debug('preview_class_report.validation_warning', extra={'error': str(ve), 'details': getattr(ve, 'messages', None)})
        except Exception:
            pass
        # Only block when obvious length constraints are violated; otherwise continue
        too_long = (
            (isinstance(grade, str) and len(grade) > 100) or
            (isinstance(stream, str) and len(stream) > 50) or
            (isinstance(term, str) and len(term) > 50) or
            (isinstance(assessment_type, str) and len(assessment_type) > 50)
        )
        if too_long and (request.accept_mimetypes.accept_json or request.is_json):
            from ..utils.error_responses import error_response
            details = getattr(ve, 'messages', None)
            return error_response('INVALID_PATH_PARAMS', 'Invalid path parameters', 400, details=details)
        # Otherwise proceed with best-effort values (for both HTML and JSON)
    except Exception as ve:  # non-validation issue (e.g., import/version); log and continue
        validation_warning = True
        try:
            _log.debug('preview_class_report.validation_nonvalidation_exception', extra={'error': str(ve)})
        except Exception:
            pass
        # Proceed for both HTML and JSON to avoid blocking legitimate requests due to env issues
    from ..services.class_report_builder import ClassReportBuilder  # local import to avoid circulars

    # Handle subject selection form submission
    if request.method == 'POST':
        selected_subjects = []
        for key, value in request.form.items():
            if key.startswith('include_subject_') and value:
                try:
                    selected_subjects.append(int(value))
                except Exception:
                    pass
        session['selected_subjects'] = selected_subjects

    selected_subjects = session.get('selected_subjects', [])

    # If a plain subject teacher (not classteacher) restrict to assigned subjects
    try:
        role = get_role(session)
    except Exception:
        role = None
    if role == 'teacher':
        teacher_id = session.get('teacher_id')
        if teacher_id:
            stream_obj_temp = Stream.query.join(Grade).filter(Grade.name == grade, Stream.name == stream[-1]).first()
            if stream_obj_temp:
                grade_obj_temp = stream_obj_temp.grade
                assignments = TeacherSubjectAssignment.query.filter_by(teacher_id=teacher_id, grade_id=grade_obj_temp.id) \
                    .filter((TeacherSubjectAssignment.stream_id == stream_obj_temp.id) | (TeacherSubjectAssignment.stream_id == None)).all()
                teacher_subject_ids = [a.subject_id for a in assignments]
                selected_subjects = teacher_subject_ids
                session['selected_subjects'] = selected_subjects

    # --- IDOR / ownership enforcement ---
    try:
        teacher_id = session.get('teacher_id')
        role = (session.get('role') or '').lower()
        from ..services.role_based_data_service import RoleBasedDataService as _RBDS
        assignment_summary = _RBDS.get_teacher_assignments_summary(teacher_id, role)
        class_assignments = assignment_summary.get('class_teacher_assignments', []) if assignment_summary and not assignment_summary.get('error') else []
        stream_fragment = stream.split()[-1]
        grade_obj = Grade.query.filter_by(name=grade).first()
        stream_obj = Stream.query.filter_by(name=stream_fragment, grade_id=grade_obj.id).first() if grade_obj else None
        authorized = True if role == 'headteacher' else False
        if role == 'classteacher' and grade_obj and stream_obj:
            # Accept either explicit class_teacher_assignment OR direct stream_id linkage
            for a in class_assignments:
                if a.get('grade_id') == grade_obj.id and a.get('stream_id') == stream_obj.id:
                    authorized = True
                    break
            # Fallback: direct teacher.stream_id association
            if not authorized:
                from ..models import Teacher as _T
                t = safe_get(_T, teacher_id)
                if t and t.stream_id == stream_obj.id:
                    authorized = True
        if not authorized:
            _log.debug('preview_class_report.unauthorized', extra={'teacher_id': teacher_id, 'role': role, 'grade': grade, 'stream_fragment': stream_fragment})
            if request.accept_mimetypes.accept_json or request.is_json:
                from ..utils.error_responses import error_response
                return error_response('FORBIDDEN', 'Forbidden: not assigned to this class', 403)
            # Return explicit 403 instead of abort redirect pattern to satisfy tests
            return ('Forbidden', 403)
    except Exception as _auth_exc:  # Fallback protection
        if role == 'classteacher':
            if request.accept_mimetypes.accept_json or request.is_json:
                from ..utils.error_responses import error_response
                return error_response('FORBIDDEN', 'Forbidden', 403)
            abort(403)

    ctx = ClassReportBuilder.build(grade, stream, term, assessment_type, selected_subject_ids=selected_subjects)
    if ctx.get('error'):
        if request.accept_mimetypes.accept_json or request.is_json:
            from ..utils.error_responses import error_response
            return error_response('REPORT_BUILD_FAILED', ctx['error'], 400)
        _log.debug('preview_class_report.build_failed', extra={'error': ctx['error']})
        # Legacy behavior: still render template (status 200) with minimal context & error banner
        try:
            student_count = 0
            subject_count = 0
            elapsed_ms = int((time.time() - start_time) * 1000)
            from ..security_helpers import audit_log
            audit_log('report.class.preview:metrics', elapsed_ms=elapsed_ms, students=student_count, subjects=subject_count, degraded=True)
        except Exception:
            pass
        # Provide robust fallback context so legacy template variables resolve
        # Derive education_level using centralized helper
        try:
            from ..utils.constants import get_education_level_for_grade_name
            level_code = get_education_level_for_grade_name(grade) if isinstance(grade, str) else ''
        except Exception:
            level_code = ''
        education_level = level_code.replace('_', ' ').title() if level_code else ''
        placeholder_ctx = dict(
            education_level=education_level,
            education_level_code=level_code,
            total_students=0,
            student_count=0,
            total_subjects=0,
            subject_rows=[],
            students=[],
            assessment_summary=[],
            class_mean=0,
            grade_boundaries={},
            stream_name=stream.split()[-1] if isinstance(stream, str) else stream,
            grade_name=grade,
        )
        # Render minimal degraded page instead of full template to avoid missing variable errors
        try:
            from flask import make_response
            minimal_html = f"""
            <html><head><title>Class Report (Degraded)</title></head>
            <body>
              <h1>Class Report Preview (Degraded)</h1>
              <p>Grade: {grade} | Stream: {stream} | Term: {term} | Assessment: {assessment_type}</p>
              <p>Status: <strong>Unavailable</strong></p>
              <p>Error: {ctx['error']}</p>
              <p>This simplified view is shown because the report builder could not generate full data.</p>
            </body></html>
            """
            return make_response(minimal_html, 200)
        except Exception:
            return (ctx['error'], 200)

    # Add form subjects list (was `subjects` earlier – all subjects at level)
    if ctx.get('education_level_code'):
        subjects_for_form = Subject.query.filter_by(education_level=ctx['education_level_code']).all()
    else:
        subjects_for_form = Subject.query.all()

    # Metrics audit enrichment
    try:
        student_count = ctx.get('student_count') or len(ctx.get('students', []) or [])
        subject_count = ctx.get('subject_count') or len(ctx.get('subject_rows', []) or [])
        elapsed_ms = int((time.time() - start_time) * 1000)
        from ..security_helpers import audit_log
        audit_log('report.class.preview:metrics', elapsed_ms=elapsed_ms, students=student_count, subjects=subject_count)
    except Exception:  # pragma: no cover
        pass
    return render_template(
        'preview_class_report.html',
        grade=grade,
        stream=stream,
        term=term,
        assessment_type=assessment_type,
        subjects=subjects_for_form,
        **{k: v for k, v in ctx.items() if k not in ('education_level_code',)}
    )

@classteacher_bp.route('/api/class/<int:grade_id>/<int:stream_id>/students/<int:student_id>/flag', methods=['POST'])
@limiter.limit("10 per minute")
@classteacher_required()
def flag_student_note(grade_id, stream_id, student_id):
    """Example mutation endpoint demonstrating:
    - Path param validation (implicit int casting by converter + schema)
    - Ownership / authorization (classteacher or headteacher)
    - Safe DB access with db.session.get (SQLAlchemy 2.x style)
    """
    from marshmallow import Schema, fields, ValidationError, validate
    from ..models.academic import Student, Grade, Stream
    from ..models import Teacher as _Teacher
    from ..services.role_based_data_service import RoleBasedDataService as _RBDS
    class _FlagSchema(Schema):
        # Use built-in length validator to avoid signature mismatch issues with custom validator
        note = fields.Str(required=True, validate=validate.Length(max=500, error='note too long (max 500 chars)'))
    from ..utils.error_responses import error_response
    from werkzeug.exceptions import BadRequest
    try:
        payload = request.get_json(silent=False)
    except BadRequest:
        return error_response('MALFORMED_JSON', 'Malformed JSON body', 400)
    if payload is None:
        payload = {}
    try:
        data = _FlagSchema().load(payload)
    except ValidationError as ve:
        return error_response('INVALID_PAYLOAD', 'Invalid payload', 400, details=ve.messages)

    teacher_id = session.get('teacher_id')
    role = (session.get('role') or '').lower()
    if not teacher_id:
        return error_response('UNAUTHORIZED', 'Authentication required', 401)

    # Fetch objects using modern session.get pattern
    g_obj = db.session.get(Grade, grade_id)
    s_obj = db.session.get(Stream, stream_id)
    st_obj = db.session.get(Student, student_id)
    if not all([g_obj, s_obj, st_obj]) or s_obj.grade_id != g_obj.id or st_obj.grade_id != g_obj.id or st_obj.stream_id != s_obj.id:
        return error_response('NOT_FOUND', 'Resource not found', 404)

    # Ownership / authorization: headteacher always ok, classteacher must own class
    if role not in ('headteacher', 'classteacher'):
        # Maintain backward compatible simple error string expected by existing tests
        return jsonify({'error': 'Forbidden'}), 403
    if role == 'classteacher':
        assignment_summary = _RBDS.get_teacher_assignments_summary(teacher_id, role)
        class_assignments = assignment_summary.get('class_teacher_assignments', []) if assignment_summary and not assignment_summary.get('error') else []
        authorized = False
        for a in class_assignments:
            if a.get('grade_id') == g_obj.id and a.get('stream_id') == s_obj.id:
                authorized = True
                break
        if not authorized:
            # fallback direct stream linkage
            t = db.session.get(_Teacher, teacher_id)
            if t and t.stream_id == s_obj.id:
                authorized = True
        if not authorized:
            # Maintain backward compatible simple error string expected by existing tests
            return jsonify({'error': 'Forbidden: not assigned to this class'}), 403

    # For demonstration we do not persist new column; instead log note
    from flask import current_app as _ca
    _ca.logger.info("Flag note added", extra={
        'student_id': student_id,
        'teacher_id': teacher_id,
        'grade_id': grade_id,
        'stream_id': stream_id,
        'note_excerpt': data['note'][:120]
    })
    return jsonify({'status': 'ok', 'student_id': student_id, 'note_saved': True}), 200

@classteacher_bp.route('/test_component_upload')
@secure_endpoint(roles=['classteacher','headteacher'], debug_only=True, audit_event='debug.test_component_upload')
def test_component_upload():
    """Test route to verify component subjects are available for upload."""
    try:
        results = []
        results.append("🧪 Testing Component Subject Upload Availability")
        results.append("=" * 60)

        # Check each education level in canonical order (limited to supported flows)
        from ..utils.constants import EDUCATION_LEVELS_ORDER
        education_levels = [lv for lv in EDUCATION_LEVELS_ORDER if lv in {'lower_primary', 'upper_primary', 'junior_secondary'}]

        from ..services.enhanced_composite_service import EnhancedCompositeService
        for education_level in education_levels:
            results.append(f"\n📚 {education_level}:")

            # Get uploadable subjects (components + regular subjects)
            uploadable_subjects = EnhancedCompositeService.get_subjects_for_upload(education_level)

            results.append(f"   📝 Uploadable subjects ({len(uploadable_subjects)}):")

            for subject in uploadable_subjects:
                if getattr(subject, 'is_component', False):
                    results.append(f"      ✅ {subject.name} (component of {getattr(subject, 'composite_parent', 'N/A')})")
                else:
                    results.append(f"      📄 {subject.name} (regular subject)")

        # Test marks upload form for English Grammar
        results.append(f"\n🎯 Testing English Grammar Upload Form:")

        english_grammar = Subject.query.filter_by(
            name="English Grammar",
            education_level="lower_primary",
            is_component=True
        ).first()

        if english_grammar:
            results.append(f"   ✅ English Grammar subject found (ID: {english_grammar.id})")
            results.append(f"   📊 Composite parent: {english_grammar.composite_parent}")
            results.append(f"   ⚖️ Weight: {english_grammar.component_weight}")

            # Check if there are any existing marks
            existing_marks = Mark.query.filter_by(subject_id=english_grammar.id).count()
            results.append(f"   📈 Existing marks: {existing_marks}")
        else:
            results.append(f"   ❌ English Grammar subject not found!")

        # Test marks upload form for English Composition
        results.append(f"\n🎯 Testing English Composition Upload Form:")

        english_composition = Subject.query.filter_by(
            name="English Composition",
            education_level="lower_primary",
            is_component=True
        ).first()

        if english_composition:
            results.append(f"   ✅ English Composition subject found (ID: {english_composition.id})")
            results.append(f"   📊 Composite parent: {english_composition.composite_parent}")
            results.append(f"   ⚖️ Weight: {english_composition.component_weight}")

            # Check if there are any existing marks
            existing_marks = Mark.query.filter_by(subject_id=english_composition.id).count()
            results.append(f"   📈 Existing marks: {existing_marks}")
        else:
            results.append(f"   ❌ English Composition subject not found!")

        results.append(f"\n📝 Next Steps:")
        results.append(f"1. Go to 'Upload Marks' page")
        results.append(f"2. Select 'English Grammar' from the subject dropdown")
        results.append(f"3. Upload marks for English Grammar separately")
        results.append(f"4. Select 'English Composition' from the subject dropdown")
        results.append(f"5. Upload marks for English Composition separately")
        results.append(f"6. Generate enhanced class report to see combined results")

        # Content negotiation: JSON summary if requested
        if 'application/json' in (request.headers.get('Accept') or '').lower():
            return jsonify({'status': 'ok', 'lines': results[:15], 'total_lines': len(results)})
        return f"<pre>{'<br>'.join(results)}</pre>"
    except Exception as e:
        return f"<pre>❌ Error during testing: {str(e)}</pre>"

@classteacher_bp.route('/debug_marks_data/<grade>/<stream>/<term>/<assessment_type>')
@dev_only
def debug_marks_data(grade, stream, term, assessment_type):
    """Debug route to check what marks data exists."""
    try:
        results = []
        results.append("🔍 Debugging Marks Data")
        results.append("=" * 50)

        # Parse stream name
        if stream.startswith('Stream '):
            stream_name = stream.split(' ')[-1]
        else:
            stream_name = stream

        results.append(f"Parameters:")
        results.append(f"  Grade: {grade}")
        results.append(f"  Stream: {stream} -> {stream_name}")
        results.append(f"  Term: {term}")
        results.append(f"  Assessment Type: {assessment_type}")

        # Get objects
        stream_obj = Stream.query.join(Grade).filter(Grade.name == grade, Stream.name == stream_name).first()
        term_obj, term_name = _resolve_term_object(term)
        assessment_type_obj, at_name = _resolve_assessment_type_object(assessment_type)

        results.append(f"\nDatabase Objects:")
        results.append(f"  Stream Object: {stream_obj}")
        results.append(f"  Term Object: {term_obj}")
        results.append(f"  Assessment Type Object: {assessment_type_obj}")

        if not (stream_obj and term_obj and assessment_type_obj):
            results.append(f"\n❌ Missing objects!")
            return f"<pre>{'<br>'.join(results)}</pre>"

        # Get students
        students = Student.query.filter_by(stream_id=stream_obj.id).all()
        results.append(f"\nStudents in {grade} {stream_name}: {len(students)}")
        for student in students[:5]:  # Show first 5
            results.append(f"  - {student.name} (ID: {student.id})")

        # Get all marks for this stream/term/assessment
        all_marks = Mark.query.filter(
            Mark.student_id.in_([s.id for s in students]),
            Mark.term_id == term_obj.id,
            Mark.assessment_type_id == assessment_type_obj.id
        ).all()

        results.append(f"\nTotal marks found: {len(all_marks)}")

        # Group marks by subject
        marks_by_subject = {}
        for mark in all_marks:
            subject = safe_get(Subject, mark.subject_id)
            if subject:
                if subject.name not in marks_by_subject:
                    marks_by_subject[subject.name] = []
                marks_by_subject[subject.name].append({
                    'student_id': mark.student_id,
                    'raw_mark': mark.raw_mark,
                    'percentage': mark.percentage,
                    'is_component': subject.is_component,
                    'is_composite': subject.is_composite,
                    'composite_parent': subject.composite_parent
                })

        results.append(f"\nMarks by subject:")
        for subject_name, marks in marks_by_subject.items():
            results.append(f"  📚 {subject_name}: {len(marks)} marks")
            subject_obj = Subject.query.filter_by(name=subject_name).first()
            if subject_obj:
                results.append(f"      - Is Component: {subject_obj.is_component}")
                results.append(f"      - Is Composite: {subject_obj.is_composite}")
                results.append(f"      - Composite Parent: {subject_obj.composite_parent}")
                results.append(f"      - Component Weight: {subject_obj.component_weight}")

        # Check specific English subjects
        results.append(f"\n🎯 English Subject Analysis:")
        english_subjects = Subject.query.filter(
            Subject.name.like('%English%')
        ).all()

        for subject in english_subjects:
            marks_count = Mark.query.filter(
                Mark.subject_id == subject.id,
                Mark.student_id.in_([s.id for s in students]),
                Mark.term_id == term_obj.id,
                Mark.assessment_type_id == assessment_type_obj.id
            ).count()

            results.append(f"  📝 {subject.name}:")
            results.append(f"      - ID: {subject.id}")
            results.append(f"      - Education Level: {subject.education_level}")
            results.append(f"      - Is Component: {subject.is_component}")
            results.append(f"      - Is Composite: {subject.is_composite}")
            results.append(f"      - Composite Parent: {subject.composite_parent}")
            results.append(f"      - Component Weight: {subject.component_weight}")
            results.append(f"      - Marks Count: {marks_count}")

        return f"<pre>{'<br>'.join(results)}</pre>"

    except Exception as e:
        import traceback
        return f"<pre>❌ Error during debugging: {str(e)}<br><br>{traceback.format_exc()}</pre>"

@classteacher_bp.route('/fixed_class_report/<grade>/<stream>/<term>/<assessment_type>')
@dev_only
def fixed_class_report(grade, stream, term, assessment_type):
    """Deprecated legacy endpoint – redirects to unified preview route.

    Retained temporarily (dev-only) for any lingering manual checks. Will be
    removed after verification window.
    """
    flash('fixed_class_report has been merged into preview_class_report – redirecting.', 'info')
    return redirect(url_for('classteacher.preview_class_report', grade=grade, stream=stream, term=term, assessment_type=assessment_type))

@classteacher_bp.route('/dev/report_probe/<grade>/<stream>/<term>/<assessment_type>')
@dev_only
def report_probe(grade, stream, term, assessment_type):
    """Lightweight JSON probe to inspect builder output without rendering template."""
    from ..services.class_report_builder import ClassReportBuilder
    from flask import jsonify
    ctx = ClassReportBuilder.build(grade, stream, term, assessment_type, selected_subject_ids=session.get('selected_subjects', []))
    if ctx.get('error'):
        return jsonify({'error': ctx['error']}), 400
    # Trim large arrays for probe
    sample_students = ctx.get('class_data', [])[:5]
    return jsonify({
        'grade': grade,
        'stream': stream,
        'term': term,
        'assessment_type': assessment_type,
        'subjects': ctx.get('subject_names', []),
        'student_sample_count': len(sample_students),
        'first_student': sample_students[0] if sample_students else None,
        'class_average': ctx.get('class_average'),
        'subject_averages': ctx.get('subject_averages'),
    })

@classteacher_bp.route('/create_test_marks')
@dev_only
def create_test_marks():
    return ("<h2>🛑 Disabled</h2><p>Composite subject manual fix removed. "
            "Any structural adjustments must go through Alembic migrations.</p>")

@classteacher_bp.route('/test_report_debug')
@dev_only
def test_report_debug():
    """Debug route to test report calculations without authentication."""
    try:
        # Get Grade 9 B context like in the actual report
        grade_9 = Grade.query.filter_by(name='Grade 9').first()
        stream_b = Stream.query.filter_by(grade_id=grade_9.id, name='B').first()
        students = Student.query.filter_by(stream_id=stream_b.id).all()
        
        # Get filtered subjects (junior_secondary)
        filtered_subjects = Subject.query.filter_by(education_level='junior_secondary').all()
        subjects_with_marks = [s for s in filtered_subjects if Mark.query.filter_by(subject_id=s.id).count() > 0]
        
        results = []
        results.append(f'Students in Grade 9 B: {len(students)}')
        results.append(f'Subjects with marks: {[s.name for s in subjects_with_marks]}')
        results.append('')
        
        # Simulate the calculation logic
        class_data = []
        for i, student in enumerate(students, 1):
            filtered_marks = {}
            total_marks_value = 0
            subject_count = 0
            
            for subject in subjects_with_marks:
                mark = Mark.query.filter_by(
                    student_id=student.id,
                    subject_id=subject.id
                ).first()
                
                if mark and mark.percentage is not None:
                    filtered_marks[subject.name] = mark.percentage
                    total_marks_value += mark.percentage
                    subject_count += 1
                else:
                    filtered_marks[subject.name] = 0
            
            # Calculate average
            if subject_count > 0:
                average = total_marks_value / subject_count
            else:
                average = 0
            
            student_data = {
                'student': student.name,
                'filtered_marks': filtered_marks,
                'filtered_total': total_marks_value,
                'filtered_average': average,
                'rank': i
            }
            class_data.append(student_data)
            
            results.append(f'{student.name}:')
            results.append(f'  Marks: {filtered_marks}')
            results.append(f'  Total: {total_marks_value}')
            results.append(f'  Average: {average:.2f}%')
            results.append('')
        
        # Calculate class average
        total_sum = sum(s['filtered_total'] for s in class_data if s['filtered_total'] > 0)
        students_with_marks_count = len([s for s in class_data if s['filtered_total'] > 0])
        class_average = total_sum / students_with_marks_count if students_with_marks_count > 0 else 0
        
        results.append(f'Class Total Sum: {total_sum}')
        results.append(f'Students with marks: {students_with_marks_count}')
        results.append(f'Class Average: {class_average:.2f}')
        
        return f"<pre>{'<br>'.join(results)}</pre>"
        
    except Exception as e:
        return f"<pre>ERROR: {str(e)}</pre>"

@classteacher_bp.route('/clear_cache')
@classteacher_required
def clear_cache():
    """Clear all cached reports to ensure fresh data"""
    try:
        # Clear the in-memory cache and directory cache
        import shutil
        import os
        
        cache_dir = 'cache'
        if os.path.exists(cache_dir):
            shutil.rmtree(cache_dir)
            os.makedirs(cache_dir, exist_ok=True)
        
        flash('All report caches have been cleared. Reports will now show fresh data with composite subject fixes.', 'success')
    except Exception as e:
        flash(f'Error clearing cache: {str(e)}', 'error')
    
    return redirect(url_for('classteacher.dashboard'))

@classteacher_bp.route('/analytics')
@limiter.limit("30 per minute")
@classteacher_required()
def analytics_dashboard():
    """Dedicated analytics page for classteachers."""
    print("🔍 ANALYTICS ROUTE HIT!")
    
    try:
        teacher_id = session.get('teacher_id')
        print(f"🔍 Teacher ID from session: {teacher_id}")
        
        if not teacher_id:
            print("🚨 No teacher_id in session")
            flash('Please log in to access analytics.', 'error')
            return redirect(url_for('auth.classteacher_login'))

        # Get teacher information
        teacher = safe_get(Teacher, teacher_id)
        if not teacher:
            print("🚨 Teacher not found in database")
            flash('Teacher not found.', 'error')
            return redirect(url_for('auth.classteacher_login'))

        print(f"🔍 Teacher found: {teacher.first_name} {teacher.last_name}")

        # Helper function for performance status
        def get_performance_status(percentage):
            """Get performance status based on percentage."""
            if percentage >= 80:
                return 'Exceeding Expectation'
            elif percentage >= 65:
                return 'Meeting Expectation'
            elif percentage >= 50:
                return 'Approaching Expectation'
            else:
                return 'Below Expectation'

        # Get teacher's class assignments for analytics
        print("🔍 About to import services...")
        
        try:
            from ..services.role_based_data_service import RoleBasedDataService
            print("🔍 RoleBasedDataService imported successfully")
        except Exception as e:
            print(f"🚨 Error importing RoleBasedDataService: {e}")
            raise e
            
        try:
            from ..services.academic_analytics_service import AcademicAnalyticsService
            print("🔍 AcademicAnalyticsService imported successfully")
        except Exception as e:
            print(f"🚨 Error importing AcademicAnalyticsService: {e}")
            raise e

        print("🔍 Getting teacher assignments summary...")
        assignment_summary = RoleBasedDataService.get_teacher_assignments_summary(teacher_id, 'classteacher')
        print(f"🔍 Assignment summary: {assignment_summary}")

        # Initialize analytics data
        analytics_data = {
            'summary': {
                'students_analyzed': 0,
                'subjects_analyzed': 0,
                'best_subject_average': 0,
                'top_student_average': 0
            },
            'top_students': [],
            'subject_performance': [],
            'has_data': False,
            'data_source': 'real_data'
        }

        if 'error' not in assignment_summary:
            assigned_classes = assignment_summary.get('class_teacher_assignments', [])
            print(f"🔍 Assigned classes: {len(assigned_classes)}")

            if assigned_classes:
                # Use first assigned class for default analytics
                first_class = assigned_classes[0]
                grade_id = first_class.get('grade_id')
                stream_id = first_class.get('stream_id')

                print(f"🔍 Getting analytics for grade_id: {grade_id}, stream_id: {stream_id}")

                # Read filters from query string
                selected_term_id = request.args.get('term', type=int)
                selected_assessment_type_id = request.args.get('assessment_type', type=int)
                selected_grade_id = request.args.get('grade', type=int) or grade_id
                selected_stream_id = request.args.get('stream', type=int) or stream_id

                # Default missing filters to the latest generated class report (most recent mark)
                if not selected_term_id or not selected_assessment_type_id:
                    try:
                        latest_mark_query = Mark.query.join(Student, Mark.student_id == Student.id) \
                            .join(Stream, Student.stream_id == Stream.id) \
                            .filter(Stream.grade_id == selected_grade_id)
                        if selected_stream_id:
                            latest_mark_query = latest_mark_query.filter(Student.stream_id == selected_stream_id)
                        latest_mark = latest_mark_query.order_by(Mark.created_at.desc(), Mark.id.desc()).first()
                        if latest_mark:
                            if not selected_term_id and latest_mark.term_id:
                                selected_term_id = latest_mark.term_id
                            if not selected_assessment_type_id and latest_mark.assessment_type_id:
                                selected_assessment_type_id = latest_mark.assessment_type_id
                            print(f"🔍 Defaulted filters to latest report -> term_id: {selected_term_id}, assessment_type_id: {selected_assessment_type_id}")
                    except Exception as e:
                        print(f"⚠️ Could not default to latest report: {e}")

                # Determine top performers limit (support show-all toggle)
                show_all = request.args.get('show_all', type=int) == 1
                requested_limit = request.args.get('limit', type=int)
                top_limit = requested_limit if requested_limit else (999 if show_all else 5)

                # Subject controls: top N and view (rank vs average)
                top_subjects = request.args.get('top_subjects', type=int)
                view_mode = request.args.get('subject_view', default='average')  # 'rank' or 'average'

                print(f"🔍 Filters -> term_id: {selected_term_id}, assessment_type_id: {selected_assessment_type_id}, grade_id: {selected_grade_id}, stream_id: {selected_stream_id}")

                # Prefer analytics derived from the Class Report (single source of truth)
                try:
                    comprehensive_analytics = AcademicAnalyticsService.get_comprehensive_analytics_from_report(
                        grade_id=selected_grade_id,
                        stream_id=selected_stream_id,
                        term_id=selected_term_id,
                        assessment_type_id=selected_assessment_type_id,
                        top_performers_limit=top_limit,
                        top_subjects_limit=top_subjects,
                    )
                    # If report-based path yields no data, fall back to legacy DB-driven method
                    if not comprehensive_analytics or comprehensive_analytics.get('error') or not comprehensive_analytics.get('summary', {}).get('has_sufficient_data'):
                        print("ℹ️ Falling back to legacy analytics computation")
                        comprehensive_analytics = AcademicAnalyticsService.get_comprehensive_analytics(
                            grade_id=selected_grade_id,
                            stream_id=selected_stream_id,
                            term_id=selected_term_id,
                            assessment_type_id=selected_assessment_type_id,
                            top_performers_limit=top_limit,
                        )
                except Exception as _e:
                    print(f"⚠️ Report-based analytics failed: {_e}")
                    comprehensive_analytics = AcademicAnalyticsService.get_comprehensive_analytics(
                        grade_id=selected_grade_id,
                        stream_id=selected_stream_id,
                        term_id=selected_term_id,
                        assessment_type_id=selected_assessment_type_id,
                        top_performers_limit=top_limit,
                    )

                print(f"🔍 Comprehensive analytics result: {comprehensive_analytics}")
                print(f"🔍 Analytics keys: {list(comprehensive_analytics.keys()) if comprehensive_analytics else 'None'}")

                # Transform data for template compatibility
                if comprehensive_analytics and not comprehensive_analytics.get('error'):
                    top_performers = comprehensive_analytics.get('top_performers', [])
                    subject_analytics = comprehensive_analytics.get('subject_analytics', [])
                    summary = comprehensive_analytics.get('summary', {})

                    print(f"🔍 Top performers count: {len(top_performers)}")
                    print(f"🔍 Subject analytics count: {len(subject_analytics)}")
                    print(f"🔍 Summary data: {summary}")
                    
                    # Debug: Print first top performer data
                    if top_performers:
                        print(f"🔍 First top performer: {top_performers[0]}")
                        print(f"🔍 Assessment type: {top_performers[0].get('assessment_type', 'None')}")
                        print(f"🔍 Term: {top_performers[0].get('term', 'None')}")

                    # Use the comprehensive analytics data directly
                    analytics_data = comprehensive_analytics
                    analytics_data['has_data'] = summary.get('has_sufficient_data', False)

                    # Ensure backward compatibility by adding legacy field names
                    analytics_data['top_students'] = top_performers  # Legacy field name

                    print(f"🔍 Final analytics_data keys: {list(analytics_data.keys())}")
                    print(f"🔍 Has data: {analytics_data.get('has_data', False)}")
                    print(f"🔍 Summary has_sufficient_data: {analytics_data.get('summary', {}).get('has_sufficient_data', False)}")
                else:
                    print(f"🚨 No analytics data or error: {comprehensive_analytics.get('error', 'Unknown error')}")
                    analytics_data = {
                        'top_performers': [],
                        'top_students': [],
                        'subject_analytics': [],
                        'summary': {
                            'students_analyzed': 0,
                            'subjects_analyzed': 0,
                            'best_subject_average': 0,
                            'top_student_average': 0,
                            'has_sufficient_data': False
                        },
                        'has_data': False
                    }
            else:
                print("🚨 No assigned classes found for teacher")
                analytics_data = {
                    'top_performers': [],
                    'top_students': [],
                    'subject_analytics': [],
                    'summary': {
                        'students_analyzed': 0,
                        'subjects_analyzed': 0,
                        'best_subject_average': 0,
                        'top_student_average': 0,
                        'has_sufficient_data': False
                    },
                    'has_data': False
                }
        else:
            print(f"� Error in assignment summary: {assignment_summary['error']}")
            analytics_data = {
                'top_performers': [],
                'top_students': [],
                'subject_analytics': [],
                'summary': {
                    'students_analyzed': 0,
                    'subjects_analyzed': 0,
                    'best_subject_average': 0,
                    'top_student_average': 0,
                    'has_sufficient_data': False
                },
                'has_data': False
            }

        print(f"🔍 Final analytics data: {analytics_data}")

        # Get filter options
        terms = Term.query.all()
        assessment_types = AssessmentType.query.all()
        grades = Grade.query.all()

        print(f"🔍 Filter options - Terms: {len(terms)}, Assessments: {len(assessment_types)}, Grades: {len(grades)}")

        try:
            # Guarantee required summary keys to avoid template KeyErrors
            summary_defaults = {
                'students_analyzed': 0,
                'subjects_analyzed': 0,
                'best_subject_average': 0,
                'top_student_average': 0,
                'has_sufficient_data': False
            }
            analytics_data.setdefault('summary', {})
            for k,v in summary_defaults.items():
                analytics_data['summary'].setdefault(k, v)
            analytics_data.setdefault('top_performers', analytics_data.get('topPerformers', []))
            analytics_data.setdefault('subject_analytics', analytics_data.get('subjectAnalytics', []))

            # Ensure safe defaults for optional UI controls
            top_subjects_safe = top_subjects if 'top_subjects' in locals() and isinstance(top_subjects, int) else 0
            view_mode_safe = view_mode if 'view_mode' in locals() and view_mode in ('average', 'rank') else 'average'

            return render_template(
                'classteacher_analytics.html',
                teacher=teacher,
                analytics_data=analytics_data,
                terms=terms,
                assessment_types=assessment_types,
                grades=grades,
                # Effective filters reflect defaults when none selected
                effective_term_filter=selected_term_id if 'selected_term_id' in locals() else request.args.get('term', type=int),
                effective_assessment_filter=selected_assessment_type_id if 'selected_assessment_type_id' in locals() else request.args.get('assessment_type', type=int),
                effective_grade_filter=selected_grade_id if 'selected_grade_id' in locals() else request.args.get('grade', type=int),
                effective_stream_filter=selected_stream_id if 'selected_stream_id' in locals() else request.args.get('stream', type=int),
                current_term_filter=request.args.get('term', type=int),
                current_assessment_filter=request.args.get('assessment_type', type=int),
                current_grade_filter=request.args.get('grade', type=int),
                show_all=show_all if 'show_all' in locals() else False,
                current_limit=(requested_limit if 'requested_limit' in locals() else None),
                top_subjects=top_subjects_safe,
                subject_view=view_mode_safe,
                page_title="Academic Performance Analytics",
            )
        except Exception as render_err:
            print(f"🚨 Template render error analytics_dashboard: {render_err}")
            import traceback; traceback.print_exc()
            flash('Analytics page rendering error.', 'error')
            return redirect(url_for('classteacher.dashboard'))

    except Exception as e:
        print(f"🚨 Error in analytics route: {e}")
        import traceback
        traceback.print_exc()
        flash('Error loading analytics dashboard.', 'error')
        return redirect(url_for('classteacher.dashboard'))


@classteacher_bp.route('/analytics/data')
@limiter.limit("60 per minute")
@classteacher_required()
def analytics_data_api():
    """Lightweight JSON endpoint returning comprehensive analytics for async fetch.

    Mirrors filter logic from analytics_dashboard but returns JSON only.
    Front-end can call this to refresh charts without full page reload.
    """
    try:
        teacher_id = session.get('teacher_id')
        role = (session.get('role') or '').lower()
        # Allow headteachers to query without a bound teacher_id (shared UI scenario)
        if not teacher_id and role != 'headteacher':
            return jsonify({'error': 'Not authenticated'}), 401

        from ..services.role_based_data_service import RoleBasedDataService
        from ..services.academic_analytics_service import AcademicAnalyticsService

        # Determine role; headteacher can query any class via params (no assignment required)
    # role already computed above

        # Default values from first assignment (classteacher), but don't block headteacher when none
        assigned_classes = []
        if teacher_id:
            assignment_summary = RoleBasedDataService.get_teacher_assignments_summary(teacher_id, 'classteacher')
            if 'error' in assignment_summary:
                # For headteacher, proceed with provided params; otherwise return error
                if role != 'headteacher':
                    return jsonify({'error': assignment_summary['error']}), 400
                assigned_classes = []
            else:
                assigned_classes = assignment_summary.get('class_teacher_assignments', [])

        # Defaults from first assignment (classteacher only)
        default_grade_id = None
        default_stream_id = None
        if assigned_classes:
            first_class = assigned_classes[0]
            default_grade_id = first_class.get('grade_id')
            default_stream_id = first_class.get('stream_id')

        # Query params (ensure consistent indentation inside route)
        # For headteacher, do not fall back to classteacher defaults unless present
        grade_id = request.args.get('grade', type=int) or (default_grade_id if default_grade_id and role != 'headteacher' else None)
        stream_id = request.args.get('stream', type=int) or (default_stream_id if default_stream_id and role != 'headteacher' else None)
        term_id = request.args.get('term', type=int)
        assessment_type_id = request.args.get('assessment_type', type=int)
        limit = request.args.get('limit', type=int)
        show_all = request.args.get('show_all', type=int) == 1
        top_limit = limit if limit else (999 if show_all else 5)

        # Subject controls
        top_subjects = request.args.get('top_subjects', type=int)
        view_mode = request.args.get('subject_view', default='average')

        # Prefer report-driven analytics; fall back to legacy
        try:
            data = AcademicAnalyticsService.get_comprehensive_analytics_from_report(
                grade_id=grade_id,
                stream_id=stream_id,
                term_id=term_id,
                assessment_type_id=assessment_type_id,
                top_performers_limit=top_limit,
                top_subjects_limit=top_subjects,
            )
            if (not data) or data.get('error') or not data.get('summary', {}).get('has_sufficient_data'):
                data = AcademicAnalyticsService.get_comprehensive_analytics(
                    grade_id=grade_id,
                    stream_id=stream_id,
                    term_id=term_id,
                    assessment_type_id=assessment_type_id,
                    top_performers_limit=top_limit
                )
        except Exception:
            data = AcademicAnalyticsService.get_comprehensive_analytics(
                grade_id=grade_id,
                stream_id=stream_id,
                term_id=term_id,
                assessment_type_id=assessment_type_id,
                top_performers_limit=top_limit
            )

        # If critical identifiers are missing, return a friendly empty payload
        if not grade_id or not stream_id:
            return jsonify({
                'top_performers': [],
                'top_students': [],
                'subject_analytics': [],
                'summary': {
                    'students_analyzed': 0,
                    'subjects_analyzed': 0,
                    'best_subject_average': 0,
                    'top_student_average': 0,
                    'has_sufficient_data': False
                },
                'has_data': False,
                'applied_filters': {
                    'grade_id': grade_id,
                    'stream_id': stream_id,
                    'term_id': term_id,
                    'assessment_type_id': assessment_type_id,
                    'top_limit': top_limit,
                    'top_subjects': top_subjects,
                    'subject_view': view_mode,
                }
            })

        if data and not data.get('error'):
            # Normalize field names for front-end
            if 'top_students' not in data and 'top_performers' in data:
                data['top_students'] = data.get('top_performers', [])
            # Ensure alias for top_subject for clients that use camelCase
            if 'topSubject' not in data and 'top_subject' in data:
                data['topSubject'] = data.get('top_subject')
            summary = data.get('summary', {})
            data['has_data'] = summary.get('has_sufficient_data', False)
            data['applied_filters'] = {
                'grade_id': grade_id,
                'stream_id': stream_id,
                'term_id': term_id,
                'assessment_type_id': assessment_type_id,
                'top_limit': top_limit,
                'top_subjects': top_subjects,
                'subject_view': view_mode,
            }
            # Also provide IDs directly for building drill-down links
            data['context_ids'] = {
                'grade_id': grade_id,
                'stream_id': stream_id,
                'term_id': term_id,
                'assessment_type_id': assessment_type_id,
            }

            # Compute Class vs Grade vs School benchmarks if filters sufficient
            try:
                if term_id and assessment_type_id:
                    from sqlalchemy import func
                    # Student-level averages scoped by optional class/grade
                    student_avg = db.session.query(
                        Student.id.label('student_id'),
                        func.avg(Mark.percentage).label('avg_percentage')
                    ).join(Mark, Student.id == Mark.student_id)
                    if stream_id:
                        student_avg = student_avg.filter(Student.stream_id == stream_id)
                    elif grade_id:
                        student_avg = student_avg.join(Stream, Student.stream_id == Stream.id).filter(Stream.grade_id == grade_id)
                    student_avg = student_avg.filter(
                        Mark.term_id == term_id,
                        Mark.assessment_type_id == assessment_type_id,
                        Mark.percentage.isnot(None)
                    ).group_by(Student.id).subquery()

                    class_avg = None
                    if stream_id:
                        class_avg = db.session.query(func.avg(student_avg.c.avg_percentage)).scalar()

                    grade_avg = None
                    if grade_id:
                        grade_student_avg = db.session.query(
                            Student.id.label('student_id'), func.avg(Mark.percentage).label('avg_percentage')
                        ).join(Mark, Student.id == Mark.student_id).join(Stream, Student.stream_id == Stream.id).filter(
                            Stream.grade_id == grade_id,
                            Mark.term_id == term_id,
                            Mark.assessment_type_id == assessment_type_id,
                            Mark.percentage.isnot(None)
                        ).group_by(Student.id).subquery()
                        grade_avg = db.session.query(func.avg(grade_student_avg.c.avg_percentage)).scalar()

                    school_student_avg = db.session.query(
                        Student.id.label('student_id'), func.avg(Mark.percentage).label('avg_percentage')
                    ).join(Mark, Student.id == Mark.student_id).filter(
                        Mark.term_id == term_id,
                        Mark.assessment_type_id == assessment_type_id,
                        Mark.percentage.isnot(None)
                    ).group_by(Student.id).subquery()
                    school_avg = db.session.query(func.avg(school_student_avg.c.avg_percentage)).scalar()

                    data['benchmarks'] = {
                        'class_avg': float(class_avg) if class_avg is not None else None,
                        'grade_avg': float(grade_avg) if grade_avg is not None else None,
                        'school_avg': float(school_avg) if school_avg is not None else None,
                    }
            except Exception as _be:
                logger.debug(f"Benchmark computation (JSON) failed: {_be}")
            return jsonify(data)
        else:
            return jsonify({'error': data.get('error', 'No data'), 'has_data': False}), 200
    except Exception as e:  # pragma: no cover
        logger.exception("Analytics data API error")
        return jsonify({'error': str(e), 'has_data': False}), 500

@classteacher_bp.route('/analytics/export_pdf')
@limiter.limit("20 per minute")
@classteacher_required()
def analytics_export_pdf():
    """Generate a PDF of Top/Bottom N learners for the current filters.

    Query params:
    - kind: 'top' or 'bottom'
    - n: integer (e.g., 3,5,10)
    - term, assessment_type, grade, stream: same as analytics filters
    """
    try:
        # Inputs
        kind = request.args.get('kind', 'top')
        n = request.args.get('n', type=int) or 5
        if kind not in ('top', 'bottom'):
            kind = 'top'
        if not isinstance(n, int) or n <= 0:
            n = 5
        n = min(max(n, 1), 100)

        teacher_id = session.get('teacher_id')
        if not teacher_id:
            return jsonify({'error': 'Not authenticated'}), 401

        from ..services.role_based_data_service import RoleBasedDataService
        from ..services.academic_analytics_service import AcademicAnalyticsService

        assignment_summary = RoleBasedDataService.get_teacher_assignments_summary(teacher_id, 'classteacher')
        role = (session.get('role') or '').lower()
        if 'error' in assignment_summary and role != 'headteacher':
            return jsonify({'error': assignment_summary['error']}), 400
        assigned_classes = [] if 'error' in assignment_summary else assignment_summary.get('class_teacher_assignments', [])

        # Defaults when classteacher; headteacher can pass params directly
        default_grade_id = assigned_classes[0].get('grade_id') if assigned_classes else None
        default_stream_id = assigned_classes[0].get('stream_id') if assigned_classes else None

        grade_id = request.args.get('grade', type=int) or default_grade_id
        stream_id = request.args.get('stream', type=int) or default_stream_id
        term_id = request.args.get('term', type=int)
        assessment_type_id = request.args.get('assessment_type', type=int)

        # If identifiers are missing, return an informative empty PDF
        if not grade_id or not stream_id:
            empty_buf = BytesIO()
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import getSampleStyleSheet
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
            doc = SimpleDocTemplate(empty_buf, pagesize=A4)
            styles = getSampleStyleSheet()
            story = [Paragraph('Missing selection', styles['Title']), Paragraph('Please choose Grade and Stream to export.', styles['Normal'])]
            story.append(Spacer(1, 12))
            doc.build(story)
            empty_buf.seek(0)
            return send_file(empty_buf, mimetype='application/pdf', as_attachment=True, download_name='Analytics_Select_Class.pdf')

        # Fetch analytics with no performer slicing to get full rank list
        try:
            data = AcademicAnalyticsService.get_comprehensive_analytics_from_report(
                grade_id=grade_id,
                stream_id=stream_id,
                term_id=term_id,
                assessment_type_id=assessment_type_id,
                top_performers_limit=None,
                top_subjects_limit=None,
            )
            if (not data) or data.get('error') or not data.get('summary', {}).get('has_sufficient_data'):
                data = AcademicAnalyticsService.get_comprehensive_analytics(
                    grade_id=grade_id,
                    stream_id=stream_id,
                    term_id=term_id,
                    assessment_type_id=assessment_type_id,
                    top_performers_limit=999
                )
        except Exception:
            data = AcademicAnalyticsService.get_comprehensive_analytics(
                grade_id=grade_id,
                stream_id=stream_id,
                term_id=term_id,
                assessment_type_id=assessment_type_id,
                top_performers_limit=999
            )

        performers = (data or {}).get('top_performers') or []
        context = (data or {}).get('context') or {}

        # Select items
        if kind == 'top':
            selected = performers[:n]
            title = f"Top {n} Learners"
        else:
            # bottom n by average asc
            selected = sorted(performers, key=lambda x: float(x.get('average_percentage', 0)))[:n]
            title = f"Least {n} Performing Learners"

        # Build PDF
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
        styles = getSampleStyleSheet()
        story = []

        story.append(Paragraph(title, styles['Title']))
        try:
            ctx_line = f"{context.get('grade','')} {('Stream ' + context.get('stream')) if context.get('stream') else ''} • {context.get('term','')} • {context.get('assessment_type','')}"
            story.append(Paragraph(ctx_line, styles['Normal']))
        except Exception:
            pass
        story.append(Spacer(1, 12))

        table_data = [["Rank", "Student", "Average %", "Grade"]]
        for it in selected:
            try:
                rank_val = it.get('rank') if it.get('rank') is not None else ''
                name_val = it.get('name') or it.get('student_name') or ''
                avg_val = f"{float(it.get('average_percentage', 0)):.1f}%"
                grade_letter = it.get('grade_letter', '')
            except Exception:
                rank_val, name_val, avg_val, grade_letter = '', '', '0.0%', ''
            table_data.append([rank_val, name_val, avg_val, grade_letter])

        tbl = Table(table_data, colWidths=[50, 280, 90, 60])
        tbl.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#f5f5f5')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.black),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('ALIGN', (1,1), (1,-1), 'LEFT'),
            ('GRID', (0,0), (-1,-1), 0.4, colors.grey),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('BOTTOMPADDING', (0,0), (-1,0), 8),
        ]))
        story.append(tbl)

        # Footer
        story.append(Spacer(1, 12))
        try:
            gen_time = datetime.now().strftime('%Y-%m-%d %H:%M')
            story.append(Paragraph(f"Generated on {gen_time}", styles['Italic']))
        except Exception:
            pass

        doc.build(story)
        buffer.seek(0)
        filename = f"{title.replace(' ', '_')}.pdf"
        return send_file(buffer, mimetype='application/pdf', as_attachment=True, download_name=filename)
    except Exception as e:  # pragma: no cover
        logger.exception("Analytics export PDF error")
        return jsonify({'error': str(e)}), 500

@classteacher_bp.route('/analytics/export_subjects_pdf')
@limiter.limit("20 per minute")
@classteacher_required()
def analytics_export_subjects_pdf():
    """Generate a PDF of the Subject Performance grid for the current filters.

    Query params:
    - top_subjects: optional integer to slice Top N subjects (3,5,10)
    - subject_view: 'average' (default) or 'rank' to control first column
    - term, assessment_type, grade, stream: same as analytics filters
    """
    try:
        teacher_id = session.get('teacher_id')
        if not teacher_id:
            return jsonify({'error': 'Not authenticated'}), 401

        top_subjects = request.args.get('top_subjects', type=int)
        subject_view = request.args.get('subject_view', default='average')
        if subject_view not in ('average', 'rank'):
            subject_view = 'average'

        from ..services.role_based_data_service import RoleBasedDataService
        from ..services.academic_analytics_service import AcademicAnalyticsService

        assignment_summary = RoleBasedDataService.get_teacher_assignments_summary(teacher_id, 'classteacher')
        role = (session.get('role') or '').lower()
        if 'error' in assignment_summary and role != 'headteacher':
            return jsonify({'error': assignment_summary['error']}), 400
        assigned_classes = [] if 'error' in assignment_summary else assignment_summary.get('class_teacher_assignments', [])

        default_grade_id = assigned_classes[0].get('grade_id') if assigned_classes else None
        default_stream_id = assigned_classes[0].get('stream_id') if assigned_classes else None

        grade_id = request.args.get('grade', type=int) or default_grade_id
        stream_id = request.args.get('stream', type=int) or default_stream_id
        term_id = request.args.get('term', type=int)
        assessment_type_id = request.args.get('assessment_type', type=int)

        if not grade_id or not stream_id:
            empty_buf = BytesIO()
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import getSampleStyleSheet
            from reportlab.platypus import SimpleDocTemplate, Paragraph
            doc = SimpleDocTemplate(empty_buf, pagesize=A4)
            styles = getSampleStyleSheet()
            doc.build([Paragraph('Missing selection', styles['Title']), Paragraph('Choose Grade and Stream to export.', styles['Normal'])])
            empty_buf.seek(0)
            return send_file(empty_buf, mimetype='application/pdf', as_attachment=True, download_name='Subject_Performance_Select_Class.pdf')

        # Fetch analytics; apply top_subjects slicing using service support
        try:
            data = AcademicAnalyticsService.get_comprehensive_analytics_from_report(
                grade_id=grade_id,
                stream_id=stream_id,
                term_id=term_id,
                assessment_type_id=assessment_type_id,
                top_performers_limit=None,
                top_subjects_limit=top_subjects,
            )
            if (not data) or data.get('error') or not data.get('summary', {}).get('has_sufficient_data'):
                # Legacy fallback doesn't support per-subject enrichments fully
                data = AcademicAnalyticsService.get_comprehensive_analytics(
                    grade_id=grade_id,
                    stream_id=stream_id,
                    term_id=term_id,
                    assessment_type_id=assessment_type_id,
                    top_performers_limit=None
                )
        except Exception:
            data = AcademicAnalyticsService.get_comprehensive_analytics(
                grade_id=grade_id,
                stream_id=stream_id,
                term_id=term_id,
                assessment_type_id=assessment_type_id,
                top_performers_limit=None
            )

        subjects = (data or {}).get('subject_analytics') or []
        context = (data or {}).get('context') or {}

        # Build PDF
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
        styles = getSampleStyleSheet()
        story = []

        story.append(Paragraph('Subject Performance', styles['Title']))
        try:
            ctx_line = f"{context.get('grade','')} {('Stream ' + context.get('stream')) if context.get('stream') else ''} • {context.get('term','')} • {context.get('assessment_type','')}"
            story.append(Paragraph(ctx_line, styles['Normal']))
        except Exception:
            pass
        story.append(Paragraph(f"View: {'Rank' if subject_view=='rank' else 'Average'}" + (f" • Top {top_subjects}" if top_subjects else ''), styles['Normal']))
        story.append(Spacer(1, 12))

        # Table header depends on subject_view
        first_col = 'Rank' if subject_view == 'rank' else 'Average %'
        table_data = [[first_col, 'Subject', 'Teacher', 'Students', 'Assessments', 'Prev Term %', 'Δ', 'Status']]

        for s in subjects:
            try:
                avg = float(s.get('average_percentage', 0) or 0.0)
                rank_val = s.get('rank') if s.get('rank') is not None else ''
                prev = s.get('previous_average')
                delta = s.get('trend_delta') if s.get('trend_delta') is not None else ''
                status = s.get('performance_category') or ''
                teacher = s.get('teacher_name') or ''
                subject_name = s.get('subject_name') or s.get('name') or ''
                students_count = s.get('student_count') or s.get('students_count') or s.get('students_with_marks') or 0
                assess_count = s.get('total_marks') or s.get('total_assessments') or 0
                first_val = (f"#{rank_val}" if subject_view == 'rank' and rank_val != '' else f"{avg:.1f}%")
                prev_val = (f"{float(prev):.1f}%" if prev is not None else '')
                delta_val = (f"{float(delta):.1f}" if isinstance(delta, (float, int)) else '')
                table_data.append([first_val, subject_name, teacher, students_count, assess_count, prev_val, delta_val, status])
            except Exception:
                continue

        col_widths = [70, 170, 130, 60, 80, 80, 40, 80]
        tbl = Table(table_data, colWidths=col_widths)
        tbl.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#f5f5f5')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.black),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('ALIGN', (1,1), (1,-1), 'LEFT'),
            ('ALIGN', (2,1), (2,-1), 'LEFT'),
            ('GRID', (0,0), (-1,-1), 0.4, colors.grey),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('BOTTOMPADDING', (0,0), (-1,0), 8),
        ]))
        story.append(tbl)

        story.append(Spacer(1, 12))
        try:
            gen_time = datetime.now().strftime('%Y-%m-%d %H:%M')
            story.append(Paragraph(f"Generated on {gen_time}", styles['Italic']))
        except Exception:
            pass

        doc.build(story)
        buffer.seek(0)
        filename = 'Subject_Performance.pdf' if not top_subjects else f'Subject_Performance_Top_{top_subjects}.pdf'
        return send_file(buffer, mimetype='application/pdf', as_attachment=True, download_name=filename)
    except Exception as e:  # pragma: no cover
        logger.exception("Subject analytics export PDF error")
        return jsonify({'error': str(e)}), 500

@classteacher_bp.route('/', methods=['GET', 'POST'])
@limiter.limit("30 per minute")
@classteacher_required()
def dashboard():
    """Route for the class teacher dashboard."""
    print(f"🔍 DASHBOARD ROUTE HIT!")
    print(f"🔍 Classteacher dashboard accessed")
    print(f"🔍 Session contents: {dict(session)}")

    # Clean up any invalid marks on dashboard load
    cleanup_invalid_marks()

    # Get the teacher and their portal access summary
    teacher_id = session.get('teacher_id')
    print(f"🔍 Teacher ID from session: {teacher_id}")

    # Get comprehensive teacher portal summary
    portal_summary = FlexibleMarksService.get_teacher_portal_summary(teacher_id)

    if 'error' in portal_summary:
        flash(f"Error loading teacher information: {portal_summary['error']}", "error")
        # Get school information for error case
        from ..services.school_config_service import SchoolConfigService
        school_info = SchoolConfigService.get_school_info_dict()
        return render_template("classteacher.html", school_info=school_info)

    teacher = portal_summary['teacher']
    print(f"DEBUG: Teacher object: {teacher}")
    print(f"DEBUG: Portal access: {portal_summary['can_access_portal']}")
    print(f"DEBUG: Teacher type: {portal_summary['portal_type']}")
    print(f"DEBUG: Total classes: {portal_summary['total_classes']}")

    # Note: Removed early return for can_access_portal to let the updated has_assignments logic handle this

    # Get role-based assignment summary for classteacher
    teacher_id = session.get('teacher_id')
    role = session.get('role', 'classteacher')

    assignment_summary = RoleBasedDataService.get_teacher_assignments_summary(teacher_id, role)

    if 'error' in assignment_summary:
        flash(f"Error loading assignments: {assignment_summary['error']}", "error")
        assignment_summary = {
            'teacher': teacher,
            'role': role,
            'subject_assignments': [],
            'class_teacher_assignments': [],
            'total_subjects_taught': 0,
            'can_manage_classes': False
        }

    # Extract assignments for backward compatibility with additional safety checks
    class_teacher_assignments = assignment_summary.get('class_teacher_assignments', []) if assignment_summary else []
    subject_assignments = assignment_summary.get('subject_assignments', []) if assignment_summary else []

    # Ensure subject_assignments is always a list (never None or undefined)
    if subject_assignments is None:
        subject_assignments = []
    if not isinstance(subject_assignments, list):
        subject_assignments = []

    # Create JSON-safe version for the template (for JavaScript usage)
    subject_assignments_json_safe = RoleBasedDataService.make_assignments_json_safe(subject_assignments)

    # Initialize variables for teacher's assigned stream/grade (if any)
    stream = None
    grade = None
    grade_level = ""
    stream_name = ""

    # Check if teacher is assigned to a stream (direct assignment)
    if teacher.stream_id:
        stream = safe_get(Stream, teacher.stream_id)
        if stream:
            grade = safe_get(Grade, stream.grade_id)
            if grade:
                grade_level = grade.name
                stream_name = f"Stream {stream.name}"

    # If no direct stream assignment, check portal_summary for class assignments
    if not stream and portal_summary.get('class_assignments'):
        # Use the first class assignment to show assignment status
        first_class = portal_summary['class_assignments'][0]
        grade_level = first_class.get('grade_name', '')
        stream_name = f"Stream {first_class.get('stream_name', '')}"
        # Create mock objects for template compatibility
        stream = type('MockStream', (), {'name': first_class.get('stream_name', '')})()
        grade = type('MockGrade', (), {'name': first_class.get('grade_name', '')})()
        
        # If this is a class teacher assignment, also set the stream_id
        if first_class.get('is_class_teacher') and not teacher.stream_id:
            teacher.stream_id = first_class.get('stream_id')
            db.session.commit()
    # If teacher has class teacher assignments, use those
    if not stream and assignment_summary.get('class_teacher_assignments'):
        first_class_assignment = assignment_summary['class_teacher_assignments'][0]
        if first_class_assignment.get('grade_name') and first_class_assignment.get('stream_name'):
            grade_level = first_class_assignment['grade_name']
            stream_name = f"Stream {first_class_assignment['stream_name']}"
            # Create a mock stream object for template compatibility
            stream = type('MockStream', (), {'name': first_class_assignment['stream_name']})()
            grade = type('MockGrade', (), {'name': first_class_assignment['grade_name']})()

    # Check if teacher has any assignments at all - use portal_summary for accurate data
    has_assignments = (
        portal_summary.get('can_access_portal', False) and
        portal_summary.get('total_classes', 0) > 0
    )

    # Get data for the form
    grades = [grade.name for grade in Grade.query.all()]
    grades_dict = {grade.name: grade.id for grade in Grade.query.all()}
    terms = [term.name for term in Term.query.all()]
    assessment_types = [assessment_type.name for assessment_type in AssessmentType.query.all()]
    streams = []  # Empty list - streams will be populated via JavaScript
    subjects = [subject.name for subject in Subject.query.all()]

    # Initialize variables
    error_message = None
    show_students = False
    students = []
    education_level = ""
    grade_level = grade.name if grade else ""
    stream_name = f"Stream {stream.name}" if stream else ""
    term = ""
    assessment_type = ""
    total_marks = 0
    show_download_button = False
    show_individual_report_button = False
    class_data = []
    stats = {}

    # Fetch recent reports with improved sorting and more entries
    recent_reports = []
    # Get the sort parameter from the request, default to date
    sort_by = request.args.get('sort', 'date')
    filter_grade = request.args.get('filter_grade', '')
    filter_term = request.args.get('filter_term', '')

    # Check if we should show the download button and individual report button
    show_download_button = request.args.get('show_download', type=int, default=0) == 1
    show_individual_report_button = request.args.get('show_individual', type=int, default=0) == 1

    # Build the query with joins
    marks_query = Mark.query.join(Student).join(Stream).join(Grade).join(Term).join(AssessmentType)

    # Apply filters if provided
    if filter_grade:
        marks_query = marks_query.filter(Grade.name == filter_grade)
    if filter_term:
        marks_query = marks_query.filter(Term.name == filter_term)

    # Apply sorting
    if sort_by == 'grade':
        marks_query = marks_query.order_by(Grade.name)
    elif sort_by == 'term':
        marks_query = marks_query.order_by(Term.name)
    else:  # Default to date
        marks_query = marks_query.order_by(Mark.created_at.desc())

    # Execute the query
    marks = marks_query.all()

    # Process the results
    seen_combinations = set()
    for mark in marks:
        combination = (mark.student.stream.grade.name, mark.student.stream.name, mark.term.name, mark.assessment_type.name)
        if combination not in seen_combinations:
            seen_combinations.add(combination)
            # Get the count of marks for this combination
            mark_count = Mark.query.join(Student).join(Stream).join(Grade).join(Term).join(AssessmentType).filter(
                Grade.name == mark.student.stream.grade.name,
                Stream.name == mark.student.stream.name,
                Term.name == mark.term.name,
                AssessmentType.name == mark.assessment_type.name
            ).count()

            # Calculate class average for this grade/stream/term/assessment combination
            class_marks = Mark.query.filter_by(
                term_id=mark.term.id,
                assessment_type_id=mark.assessment_type.id
            ).join(Student).join(Stream).join(Grade).filter(
                Grade.name == mark.student.stream.grade.name,
                Stream.name == mark.student.stream.name
            ).all()

            # Calculate the class average percentage
            if class_marks:
                total_percentage = sum(m.percentage for m in class_marks if m.percentage is not None)
                class_average = round(total_percentage / len(class_marks), 1) if class_marks else 0
            else:
                class_average = 0

            recent_reports.append({
                'grade': mark.student.stream.grade.name,
                'stream': f"Stream {mark.student.stream.name}",
                'term': mark.term.name,
                'assessment_type': mark.assessment_type.name,
                'date': mark.created_at.strftime('%Y-%m-%d') if mark.created_at else 'N/A',
                'mark_count': mark_count,
                'class_average': class_average,  # Add the real class average
                'id': len(recent_reports) + 1  # Add an ID for easier reference
            })
            if len(recent_reports) >= 10:  # Increased limit to 10 recent reports
                break

    # Handle form submission
    if request.method == "POST":
        # Handle upload marks request (ADAPTED FROM PROVEN teacher.py LOGIC)
        if "upload_marks" in request.form:
            education_level = request.form.get("education_level")
            grade_level = request.form.get("grade")
            stream_name = request.form.get("stream")
            term = request.form.get("term")
            assessment_type = request.form.get("assessment_type")
            subject = request.form.get("subject")  # Single subject like teacher.py
            total_marks = request.form.get("total_marks", type=int, default=0)

            # Debug: Log form values to identify the issue
            print(f"DEBUG Form values: education_level='{education_level}', grade_level='{grade_level}', stream_name='{stream_name}', term='{term}', assessment_type='{assessment_type}', subject='{subject}', total_marks={total_marks}")
            print(f"DEBUG All form data: {dict(request.form)}")
            
            missing_fields = []
            if not education_level: missing_fields.append("Education Level")
            if not grade_level: missing_fields.append("Grade Level")
            if not stream_name: missing_fields.append("Class Stream")
            if not term: missing_fields.append("Academic Term")
            if not assessment_type: missing_fields.append("Assessment Type")
            if not subject: missing_fields.append("Subject")
            if not (total_marks and total_marks > 0): missing_fields.append("Total Marks")
            
            if missing_fields:
                error_message = f"Please fill in all fields before loading students. Missing: {', '.join(missing_fields)}"
                print(f"DEBUG Missing fields: {missing_fields}")
            else:
                # Get custom component max marks from form (dynamic configuration)
                grammar_max_marks = request.form.get("grammar_max", request.form.get("grammar_max_marks", "60"))
                composition_max_marks = request.form.get("composition_max", request.form.get("composition_max_marks", "40"))
                lugha_max_marks = request.form.get("lugha_max", request.form.get("lugha_max_marks", "60"))
                insha_max_marks = request.form.get("insha_max", request.form.get("insha_max_marks", "40"))

                # Update database components with new max marks if they changed
                subject_obj = Subject.query.filter_by(name=subject).first()
                if subject_obj and subject_obj.is_composite:
                    components = subject_obj.get_components()
                    for component in components:
                        if component.name == "Grammar" and grammar_max_marks != str(component.max_raw_mark):
                            component.max_raw_mark = int(grammar_max_marks)
                            db.session.add(component)
                        elif component.name == "Composition" and composition_max_marks != str(component.max_raw_mark):
                            component.max_raw_mark = int(composition_max_marks)
                            db.session.add(component)
                        elif component.name == "Lugha" and lugha_max_marks != str(component.max_raw_mark):
                            component.max_raw_mark = int(lugha_max_marks)
                            db.session.add(component)
                        elif component.name == "Insha" and insha_max_marks != str(component.max_raw_mark):
                            component.max_raw_mark = int(insha_max_marks)
                            db.session.add(component)

                    # Commit component updates
                    try:
                        db.session.commit()
                    except Exception as e:
                        db.session.rollback()
                        print(f"Error updating component max marks: {e}")

                print(f"🔍 DEBUG: Starting stream validation for stream: {stream_name}, grade: {grade_level}")

                # Handle both stream ID (from mobile) and stream name (from desktop) - TEACHER.PY LOGIC
                stream_obj = None

                # Check if stream is a numeric ID (from mobile form)
                if stream_name.isdigit():
                    stream_id = int(stream_name)
                    stream_obj = safe_get(Stream, stream_id)
                    print(f"🔍 DEBUG: Looking up stream ID {stream_id}, found: {stream_obj}")

                    # Validate that the stream belongs to the selected grade
                    if stream_obj:
                        # Handle both grade ID (from mobile) and grade name (from desktop)
                        if grade_level.isdigit():
                            # Mobile form submits grade ID
                            grade_id = int(grade_level)
                            grade_obj = safe_get(Grade, grade_id)
                            print(f"🔍 DEBUG: Looking up grade ID {grade_id}, found: {grade_obj}")
                        else:
                            # Desktop form submits grade name
                            grade_obj = Grade.query.filter_by(name=grade_level).first()
                            print(f"🔍 DEBUG: Looking up grade name '{grade_level}', found: {grade_obj}")

                        if not grade_obj or stream_obj.grade_id != grade_obj.id:
                            print(f"❌ DEBUG: Stream validation failed - stream belongs to grade {stream_obj.grade_id}, expected {grade_obj.id if grade_obj else 'None'}")
                            stream_obj = None  # Invalid stream for this grade
                        else:
                            print(f"✅ DEBUG: Stream validation passed - stream {stream_id} belongs to grade {grade_obj.name}")
                    else:
                        print(f"❌ DEBUG: Stream ID {stream_id} not found in database")
                else:
                    # Desktop format: extract stream letter from "Stream X" format
                    stream_letter = stream_name.replace("Stream ", "") if stream_name.startswith("Stream ") else stream_name

                    # Handle both grade ID and grade name for desktop compatibility
                    if grade_level.isdigit():
                        # Grade submitted as ID
                        grade_id = int(grade_level)
                        stream_obj = Stream.query.filter_by(
                            name=stream_letter, grade_id=grade_id
                        ).first()
                    else:
                        # Grade submitted as name (original desktop logic)
                        stream_obj = Stream.query.join(Grade).filter(
                            Grade.name == grade_level, Stream.name == stream_letter
                        ).first()

                if stream_obj:
                    print(f"✅ DEBUG: Stream object found, getting students for stream_id: {stream_obj.id}")

                    # Get students for this stream
                    students = Student.query.filter_by(stream_id=stream_obj.id).order_by(Student.name).all()
                    print(f"🔍 DEBUG: Found {len(students)} students for marks processing")

                    if students:
                        show_students = True
                        show_download_button = False
                    else:
                        stream_name_display = stream_obj.name if stream_obj else stream_name
                        error_message = f"No students found for grade {grade_level} stream {stream_name_display}"
                else:
                    if stream_name.isdigit():
                        error_message = f"Stream ID {stream_name} not found or does not belong to grade {grade_level}"
                    else:
                        stream_letter = stream_name.replace("Stream ", "") if stream_name.startswith("Stream ") else stream_name
                        error_message = f"Stream {stream_letter} not found for grade {grade_level}"

        # Handle submit marks request (ADAPTED FROM PROVEN teacher.py LOGIC)
        elif "submit_marks" in request.form:
            print(f"🔥 DEBUG: Submit marks request received (classteacher)")
            print(f"🔥 DEBUG: Form data: {dict(request.form)}")
            
            education_level = request.form.get("education_level")
            grade_level = request.form.get("grade")
            stream_name = request.form.get("stream")
            term = request.form.get("term")
            assessment_type = request.form.get("assessment_type")
            subject = request.form.get("subject")  # Single subject like teacher.py
            total_marks = request.form.get("total_marks", type=int, default=0)

            print(f"🔥 DEBUG: Required fields - education_level: {education_level}, subject: {subject}, grade: {grade_level}, stream: {stream_name}, term: {term}, assessment_type: {assessment_type}, total_marks: {total_marks}")

            if not all([education_level, subject, grade_level, stream_name, term, assessment_type, total_marks > 0]):
                error_message = "Missing required information"
                print(f"❌ DEBUG: Missing required information - stopping processing")
            else:
                print(f"✅ DEBUG: All required fields present, proceeding with marks processing")

                # Use the same validation logic as teacher.py (handle both IDs and names)
                stream_obj = None

                # Check if stream is a numeric ID (from mobile form)
                if stream_name.isdigit():
                    stream_id = int(stream_name)
                    stream_obj = safe_get(Stream, stream_id)
                    print(f"🔍 DEBUG: Submit - Looking up stream ID {stream_id}, found: {stream_obj}")

                    # Validate that the stream belongs to the selected grade
                    if stream_obj:
                        # Handle both grade ID (from mobile) and grade name (from desktop)
                        if grade_level.isdigit():
                            # Mobile form submits grade ID
                            grade_id = int(grade_level)
                            grade_obj = safe_get(Grade, grade_id)
                            print(f"🔍 DEBUG: Submit - Looking up grade ID {grade_id}, found: {grade_obj}")
                        else:
                            # Desktop form submits grade name
                            grade_obj = Grade.query.filter_by(name=grade_level).first()
                            print(f"🔍 DEBUG: Submit - Looking up grade name '{grade_level}', found: {grade_obj}")

                        if not grade_obj or stream_obj.grade_id != grade_obj.id:
                            print(f"❌ DEBUG: Submit - Stream validation failed")
                            stream_obj = None  # Invalid stream for this grade
                        else:
                            print(f"✅ DEBUG: Submit - Stream validation passed")
                else:
                    # Desktop format: extract stream letter from "Stream X" format
                    stream_letter = stream_name.replace("Stream ", "") if stream_name.startswith("Stream ") else stream_name

                    # Handle both grade ID and grade name for desktop compatibility
                    if grade_level.isdigit():
                        # Grade submitted as ID
                        grade_id = int(grade_level)
                        stream_obj = Stream.query.filter_by(
                            name=stream_letter, grade_id=grade_id
                        ).first()
                    else:
                        # Grade submitted as name (original desktop logic)
                        stream_obj = Stream.query.join(Grade).filter(
                            Grade.name == grade_level, Stream.name == stream_letter
                        ).first()
                    print(f"🔍 DEBUG: Submit - Desktop stream lookup, found: {stream_obj}")

                # Get other database objects
                subject_obj = Subject.query.filter_by(name=subject).first()
                term_obj = Term.query.filter_by(name=term).first()
                assessment_type_obj = AssessmentType.query.filter_by(name=assessment_type).first()

                print(f"🔍 DEBUG: Submit - Database objects - Subject: {subject_obj}, Term: {term_obj}, Assessment: {assessment_type_obj}")

                if not (stream_obj and subject_obj and term_obj and assessment_type_obj):
                    error_message = "Invalid selection for grade, stream, subject, term, or assessment type"
                    print(f"❌ DEBUG: Submit - Missing database objects, stopping processing")
                else:
                    print(f"✅ DEBUG: Submit - All database objects found, proceeding with student lookup")

                    # Get students for this stream
                    students = Student.query.filter_by(stream_id=stream_obj.id).order_by(Student.name).all()
                    print(f"🔍 DEBUG: Submit - Found {len(students)} students for marks processing")

                    if not students:
                        error_message = "No students found for this stream"
                    else:
                        marks_added = 0
                        marks_updated = 0

                        print(f"🔥 DEBUG: Submit - Starting marks processing for {len(students)} students")
                        print(f"🔥 DEBUG: Submit - Subject object: {subject_obj.name}, is_composite: {subject_obj.is_composite}")
                        print(f"🔥 DEBUG: Submit - Form keys containing 'mark': {[k for k in request.form.keys() if 'mark_' in k]}")

                        try:
                            # Check if this is a composite subject (using proven teacher.py logic)
                            if subject_obj.is_composite:
                                # Handle composite subject marks (English/Kiswahili)
                                components = subject_obj.get_components()

                                print(f"DEBUG: Processing composite subject marks for {len(students)} students")
                                print(f"DEBUG: Form data keys: {list(request.form.keys())}")

                                for student in students:
                                    student_key = student.name.replace(' ', '_')

                                    # Try multiple field naming patterns for composite subjects
                                    possible_percentage_keys = [
                                        f"hidden_percentage_{student_key}_{subject_obj.id}",
                                        f"percentage_{student_key}",
                                        f"hidden_percentage_{student_key}"
                                    ]

                                    percentage_value = 0.0
                                    used_key = ''
                                    for key in possible_percentage_keys:
                                        value = request.form.get(key, type=float, default=0.0)
                                        if value > 0:
                                            percentage_value = value
                                            used_key = key
                                            break

                                    print(f"DEBUG: Student {student.name} -> used key: {used_key}, value: {percentage_value}")

                                    if percentage_value > 0:  # Only process if percentage was calculated
                                        # Check if mark already exists
                                        existing_mark = Mark.query.filter_by(
                                            student_id=student.id,
                                            subject_id=subject_obj.id,
                                            term_id=term_obj.id,
                                            assessment_type_id=assessment_type_obj.id
                                        ).first()

                                        if existing_mark:
                                            # Update existing mark
                                            existing_mark.percentage = percentage_value
                                            existing_mark.raw_mark = (percentage_value / 100) * total_marks
                                            existing_mark.mark = existing_mark.raw_mark
                                            existing_mark.max_raw_mark = total_marks
                                            existing_mark.total_marks = total_marks
                                            marks_updated += 1
                                        else:
                                            # Create new mark
                                            new_mark = Mark(
                                                student_id=student.id,
                                                subject_id=subject_obj.id,
                                                term_id=term_obj.id,
                                                assessment_type_id=assessment_type_obj.id,
                                                grade_id=stream_obj.grade_id,  # Use grade from stream_obj (from form)
                                                stream_id=stream_obj.id,  # Use stream from stream_obj (from form)
                                                percentage=percentage_value,
                                                raw_mark=(percentage_value / 100) * total_marks,
                                                raw_total_marks=total_marks,  # Use correct field name
                                                mark=(percentage_value / 100) * total_marks,  # For backward compatibility
                                                total_marks=total_marks  # For backward compatibility
                                            )
                                            db.session.add(new_mark)
                                            db.session.flush()  # Get the ID
                                            marks_added += 1

                                        # Save component marks (using proven teacher.py logic)
                                        from ..models.academic import ComponentMark
                                        for component in components:
                                            component_key = f"component_{student_key}_{component.id}"
                                            component_value = request.form.get(component_key, '')

                                            if component_value and component_value.isdigit():
                                                component_mark = int(component_value)
                                                component_percentage = (component_mark / component.max_raw_mark) * 100

                                                # Find existing component mark or create new one
                                                component_mark_obj = ComponentMark.query.filter_by(
                                                    mark_id=existing_mark.id if existing_mark else new_mark.id,
                                                    component_id=component.id
                                                ).first()

                                                if component_mark_obj:
                                                    # Update existing component mark
                                                    component_mark_obj.raw_mark = component_mark
                                                    component_mark_obj.max_raw_mark = component.max_raw_mark
                                                    component_mark_obj.percentage = component_percentage
                                                else:
                                                    # Create new component mark
                                                    component_mark_obj = ComponentMark(
                                                        mark_id=existing_mark.id if existing_mark else new_mark.id,
                                                        component_id=component.id,
                                                        raw_mark=component_mark,
                                                        max_raw_mark=component.max_raw_mark,
                                                        percentage=component_percentage
                                                    )
                                                    db.session.add(component_mark_obj)
                            else:
                                # Handle regular subjects (using proven teacher.py logic)
                                print(f"DEBUG: Processing regular subject marks for {len(students)} students")
                                print(f"DEBUG: Form data keys: {list(request.form.keys())}")

                                for student in students:
                                    student_key = student.name.replace(' ', '_')

                                    # Try multiple field naming patterns (MOBILE USES mark_{student.id})
                                    possible_keys = [
                                        f"mark_{student.id}",  # MOBILE FORMAT - ADD THIS FIRST!
                                        f"mark_{student_key}_{subject_obj.id}",
                                        f"mark_{student_key}_{subject_obj.name}",  # Add subject name pattern
                                        f"mark_{student_key}_1",
                                        f"mark_{student_key}",
                                        f"mark_{student_key}_{subject_obj.name.replace(' ', '').lower()}"
                                    ]

                                    mark_value = ''
                                    used_key = ''
                                    for key in possible_keys:
                                        value = request.form.get(key, '')
                                        if value:
                                            mark_value = value
                                            used_key = key
                                            break

                                    print(f"DEBUG: Student {student.name} -> used key: {used_key}, value: '{mark_value}'")

                                    if mark_value and mark_value.replace('.', '').replace('-', '').isdigit():
                                        mark = float(mark_value)
                                        if 0 <= mark <= total_marks:
                                            percentage = (mark / total_marks) * 100

                                            # Additional validation: Ensure percentage doesn't exceed 100%
                                            if percentage > 100:
                                                print(f"DEBUG: Skipping {student.name} - percentage {percentage:.1f}% exceeds 100%")
                                                continue

                                            # Check if mark already exists
                                            existing_mark = Mark.query.filter_by(
                                                student_id=student.id,
                                                subject_id=subject_obj.id,
                                                term_id=term_obj.id,
                                                assessment_type_id=assessment_type_obj.id
                                            ).first()

                                            if existing_mark:
                                                # Update existing mark
                                                existing_mark.percentage = percentage
                                                existing_mark.raw_mark = mark
                                                existing_mark.mark = mark
                                                existing_mark.max_raw_mark = total_marks
                                                existing_mark.total_marks = total_marks
                                                marks_updated += 1
                                            else:
                                                # Create new mark
                                                new_mark = Mark(
                                                    student_id=student.id,
                                                    subject_id=subject_obj.id,
                                                    term_id=term_obj.id,
                                                    assessment_type_id=assessment_type_obj.id,
                                                    grade_id=stream_obj.grade_id,  # Use grade from stream_obj (from form)
                                                    stream_id=stream_obj.id,  # Use stream from stream_obj (from form)
                                                    percentage=percentage,
                                                    raw_mark=mark,
                                                    max_raw_mark=total_marks,
                                                    mark=mark,
                                                    total_marks=total_marks
                                                )
                                                db.session.add(new_mark)
                                                marks_added += 1

                            # Commit all changes
                            db.session.commit()

                            # Show success message and enable subject report download
                            print(f"🎉 DEBUG: Submit - Final results - marks_added: {marks_added}, marks_updated: {marks_updated}")

                            if marks_added > 0 or marks_updated > 0:
                                show_download_button = True
                                show_individual_report_button = True
                                flash(f"Successfully saved {marks_added} new marks and updated {marks_updated} existing marks.", "success")

                                # Keep the form values for reference
                                education_level = education_level
                                grade = grade_level
                                stream = stream_name
                                term = term
                                assessment_type = assessment_type
                                total_marks = total_marks

                                # Option 1: Redirect to Subject Report for immediate feedback and validation
                                flash(f"✅ Marks saved successfully for {subject_name} - {grade_level} {stream_name}!", "success")
                                # Ensure selected_subject is defined for redirect
                                selected_subject = subject_obj.id
                                return redirect(url_for('classteacher.subject_report',
                                                      grade_id=grade_obj.id,
                                                      stream_id=stream_obj.id,
                                                      subject_id=selected_subject,
                                                      term_id=term_obj.id,
                                                      assessment_type_id=assessment_type_obj.id))
                            else:
                                print("DEBUG: No marks were processed - checking form data...")
                                print(f"DEBUG: Total form fields: {len(request.form)}")
                                print(f"DEBUG: Form keys containing 'mark': {[k for k in request.form.keys() if 'mark' in k.lower()]}")
                                error_message = "No marks were processed. Please ensure you have entered marks for at least one student."

                        except Exception as e:
                            db.session.rollback()
                            error_message = f"Error saving marks: {str(e)}"

        # Handle bulk upload marks request
        elif "bulk_upload_marks" in request.form:
            if 'marks_file' not in request.files:
                flash("No file selected.", "error")
                return redirect(url_for('classteacher.dashboard'))

            file = request.files['marks_file']

            if file.filename == '':
                flash("No file selected.", "error")
                return redirect(url_for('classteacher.dashboard'))

            # Get form data
            education_level = request.form.get("education_level")
            grade_level = request.form.get("grade")
            stream_name = request.form.get("stream")
            term = request.form.get("term")
            assessment_type = request.form.get("assessment_type")
            total_marks = request.form.get("total_marks", type=int, default=100)

            if not all([education_level, grade_level, stream_name, term, assessment_type]):
                flash("Please fill in all required fields.", "error")
                return redirect(url_for('classteacher.dashboard'))

            # Extract stream letter from "Stream X" format
            stream_letter = stream_name.replace("Stream ", "") if stream_name.startswith("Stream ") else stream_name

            # Get the stream, term, and assessment type objects
            stream_obj = Stream.query.join(Grade).filter(Grade.name == grade_level, Stream.name == stream_letter).first()
            term_obj = Term.query.filter_by(name=term).first()
            assessment_type_obj = AssessmentType.query.filter_by(name=assessment_type).first()

            if not (stream_obj and term_obj and assessment_type_obj):
                flash("Invalid stream, term, or assessment type.", "error")
                return redirect(url_for('classteacher.dashboard'))

            try:
                # Check file extension
                file_ext = os.path.splitext(file.filename)[1].lower()

                if file_ext == '.csv':
                    df = pd.read_csv(file)
                elif file_ext in ['.xlsx', '.xls']:
                    df = pd.read_excel(file)
                else:
                    flash("Unsupported file format. Please upload a CSV or Excel file.", "error")
                    return redirect(url_for('classteacher.dashboard'))

                # Process the file
                marks_added = 0
                marks_updated = 0
                errors = 0

                # Get all students in this stream
                students = {student.name: student for student in Student.query.filter_by(stream_id=stream_obj.id).all()}

                # Get all subjects for this education level
                all_subjects = Subject.query.filter_by(education_level=education_level).all()

                # Define core subjects that should appear first
                core_subjects = ["Mathematics", "English", "Kiswahili", "Science", "Integrated Science",
                                "Science and Technology", "Integrated Science and Health Education"]

                # Sort subjects with core subjects first
                sorted_subjects = []

                # First add core subjects in the specified order
                for core_subject in core_subjects:
                    for subject in all_subjects:
                        if subject.name == core_subject or subject.name.upper() == core_subject.upper():
                            sorted_subjects.append(subject)

                # Then add remaining subjects alphabetically
                remaining_subjects = [s for s in all_subjects if s not in sorted_subjects]
                remaining_subjects.sort(key=lambda x: x.name)
                sorted_subjects.extend(remaining_subjects)

                # Create a dictionary of subject name to subject object
                subjects = {subject.name: subject for subject in sorted_subjects}

                # Check if we have the required columns
                if 'Student Name' not in df.columns and 'Admission Number' not in df.columns:
                    flash("File must contain either 'Student Name' or 'Admission Number' column.", "error")
                    return redirect(url_for('classteacher.dashboard'))

                # Process each row (student)
                for _, row in df.iterrows():
                    # Identify the student
                    student = None

                    if 'Student Name' in df.columns and row['Student Name'] in students:
                        student = students[row['Student Name']]
                    elif 'Admission Number' in df.columns:
                        # Find student by admission number
                        student_obj = Student.query.filter_by(
                            admission_number=str(row['Admission Number']),
                            stream_id=stream_obj.id
                        ).first()
                        if student_obj:
                            student = student_obj

                    if not student:
                        errors += 1
                        continue

                    # Process each subject column
                    for subject_name in subjects.keys():
                        if subject_name in df.columns:
                            mark_value = row[subject_name]

                            # Skip empty or non-numeric values
                            if pd.isna(mark_value) or not (isinstance(mark_value, (int, float)) or (isinstance(mark_value, str) and mark_value.isdigit())):
                                continue

                            try:
                                # Convert to integer
                                raw_mark = int(float(mark_value))

                                # Sanitize the raw mark and total marks to ensure they're within acceptable ranges
                                raw_mark, sanitized_total_marks = MarkConversionService.sanitize_raw_mark(raw_mark, total_marks)

                                # Calculate percentage using our service
                                percentage = MarkConversionService.calculate_percentage(raw_mark, sanitized_total_marks)

                                # Check if mark already exists
                                existing_mark = Mark.query.filter_by(
                                    student_id=student.id,
                                    subject_id=subjects[subject_name].id,
                                    term_id=term_obj.id,
                                    assessment_type_id=assessment_type_obj.id
                                ).first()

                                if existing_mark:
                                    # Update existing mark with both old and new field names
                                    existing_mark.mark = raw_mark  # Old field name
                                    existing_mark.total_marks = sanitized_total_marks  # Old field name
                                    existing_mark.raw_mark = raw_mark  # New field name
                                    existing_mark.max_raw_mark = sanitized_total_marks  # New field name
                                    existing_mark.percentage = percentage
                                    marks_updated += 1
                                else:
                                    # Create new mark with both old and new field names
                                    new_mark = Mark(
                                        student_id=student.id,
                                        subject_id=subjects[subject_name].id,
                                        term_id=term_obj.id,
                                        assessment_type_id=assessment_type_obj.id,
                                        mark=raw_mark,  # Old field name
                                        total_marks=sanitized_total_marks,  # Old field name
                                        raw_mark=raw_mark,  # New field name
                                        max_raw_mark=sanitized_total_marks,  # New field name
                                        percentage=percentage
                                    )
                                    db.session.add(new_mark)
                                    marks_added += 1
                            except Exception as e:
                                print(f"Error processing bulk mark: {e}")
                                errors += 1

                # Commit changes to the database
                db.session.commit()

                # Invalidate any existing cache for this grade/stream/term/assessment combination
                invalidate_cache(grade_level, stream_name, term, assessment_type)

                # Show success message
                if marks_added > 0 or marks_updated > 0:
                    flash(f"Successfully processed {marks_added + marks_updated} marks ({marks_added} new, {marks_updated} updated). {errors} errors encountered.", "success")

                    # Redirect to the dashboard with success parameters
                    return redirect(url_for('classteacher.dashboard',
                                          grade=grade_level,
                                          stream=stream_name,
                                          term=term,
                                          assessment_type=assessment_type,
                                          show_download=1,
                                          show_individual=1))
                else:
                    flash(f"No marks were processed. {errors} errors encountered.", "error")
                    return redirect(url_for('classteacher.dashboard'))

            except Exception as e:
                flash(f"Error processing file: {str(e)}", "error")
                return redirect(url_for('classteacher.dashboard'))

        # Handle generate grade marksheet request
        elif "generate_stream_marksheet" in request.form or "download_stream_marksheet" in request.form:
            grade = request.form.get("stream_grade")
            term = request.form.get("stream_term")
            assessment_type = request.form.get("stream_assessment_type")

            if not all([grade, term, assessment_type]):
                flash("Please select grade, term, and assessment type.", "error")
                return redirect(url_for('classteacher.dashboard'))

            action = "preview" if "generate_stream_marksheet" in request.form else "download"

            # Redirect to the grade marksheet route
            return redirect(url_for('classteacher.generate_grade_marksheet',
                                   grade=grade,
                                   term=term,
                                   assessment_type=assessment_type,
                                   action=action))

    # Check if a marksheet was just deleted
    marksheet_deleted = session.pop('marksheet_deleted', False)
    deleted_marksheet_info = session.pop('deleted_marksheet_info', None)

    # If a marksheet was deleted, add a special confirmation message
    if marksheet_deleted and deleted_marksheet_info:
        grade_info = deleted_marksheet_info.get('grade', '')
        stream_info = deleted_marksheet_info.get('stream', '')
        term_info = deleted_marksheet_info.get('term', '')
        assessment_info = deleted_marksheet_info.get('assessment_type', '')
        count_info = deleted_marksheet_info.get('count', 0)

        # Add a special confirmation message
        confirmation_message = f"""
        <div class="deletion-confirmation">
            <h3>Marksheet Deleted Successfully</h3>
            <p>The marksheet for <strong>{grade_info} {stream_info}</strong> in <strong>{term_info} {assessment_info}</strong> has been permanently deleted.</p>
            <p>A total of <strong>{count_info} marks</strong> were removed from the database.</p>
            <p>If you need to recreate this marksheet, you will need to enter all the marks again.</p>
        </div>
        """
    else:
        confirmation_message = ""

    # Determine which tab should be active
    active_tab = "recent-reports"  # Default tab

    # If students are being loaded or marks are being submitted, set the active tab to upload-marks
    if show_students or "upload_marks" in request.form or "submit_marks" in request.form or "bulk_upload_marks" in request.form:
        active_tab = "upload-marks"
        # Store in session that we're showing students
        session['show_students_tab'] = True
    # If generating marksheets, set the active tab to generate-marksheet
    elif "generate_stream_marksheet" in request.form or "download_stream_marksheet" in request.form:
        active_tab = "generate-marksheet"
    # Check if we have a tab preference in the session
    elif session.get('show_students_tab'):
        active_tab = "upload-marks"
        # Clear the session preference
        session.pop('show_students_tab', None)

    # Get management statistics for the dashboard
    total_students = Student.query.count()
    total_teachers = Teacher.query.count()
    total_subjects = Subject.query.count()
    total_grades = Grade.query.count()

    # Fetch students for the teacher's assigned class
    my_students = []
    if class_teacher_assignments:
        # Get students from all assigned classes
        for assignment in class_teacher_assignments:
            if assignment.get('stream_id'):
                class_students = Student.query.filter_by(stream_id=assignment['stream_id']).order_by(Student.name).all()
                for student in class_students:
                    # Split name into first and last name for display
                    name_parts = student.name.split(' ', 1)
                    first_name = name_parts[0] if name_parts else student.name
                    last_name = name_parts[1] if len(name_parts) > 1 else ""
                    
                    my_students.append({
                        'id': student.id,
                        'first_name': first_name,
                        'last_name': last_name,
                        'full_name': student.name,
                        'admission_number': student.admission_number,
                        'grade_level': assignment.get('grade_level', ''),
                        'stream_name': assignment.get('stream_name', '')
                    })
    elif teacher.stream_id:
        # Direct stream assignment
        class_students = Student.query.filter_by(stream_id=teacher.stream_id).order_by(Student.name).all()
        for student in class_students:
            # Split name into first and last name for display
            name_parts = student.name.split(' ', 1)
            first_name = name_parts[0] if name_parts else student.name
            last_name = name_parts[1] if len(name_parts) > 1 else ""
            
            my_students.append({
                'id': student.id,
                'first_name': first_name,
                'last_name': last_name,
                'full_name': student.name,
                'admission_number': student.admission_number,
                'grade_level': grade_level,
                'stream_name': stream_name
            })

    # Get school information
    from ..services.school_config_service import SchoolConfigService
    school_info = SchoolConfigService.get_school_info_dict()

    # Organize subjects by education level for the template
    all_subjects = Subject.query.all()
    subjects_by_education_level = {
        'lower_primary': [s.name for s in all_subjects if s.education_level == 'lower_primary'],
        'upper_primary': [s.name for s in all_subjects if s.education_level == 'upper_primary'],
        'junior_secondary': [s.name for s in all_subjects if s.education_level == 'junior_secondary'],
        'senior_secondary': [s.name for s in all_subjects if s.education_level == 'senior_secondary']
    }

    # Render the class teacher dashboard
    return render_template(
        "classteacher.html",
        school_info=school_info,
        grades=grades,
        grades_dict=grades_dict,
        subjects_by_education_level=subjects_by_education_level,
        terms=terms,
        assessment_types=assessment_types,
        streams=streams,
        students=students,
        education_level=education_level,
        grade=grade_level,
        stream=stream_name,
        term=term,
        assessment_type=assessment_type,
        total_marks=total_marks,
        show_students=show_students,
        error_message=error_message,
        show_download_button=show_download_button,
        show_individual_report_button=show_individual_report_button,
        subjects=subjects,
        stats=stats,
        class_data=class_data,
        recent_reports=recent_reports,
        class_teacher_assignments=class_teacher_assignments,
        subject_assignments=subject_assignments,
        subject_assignments_json_safe=subject_assignments_json_safe,
        confirmation_message=confirmation_message,
        total_students=total_students,
        total_teachers=total_teachers,
        total_subjects=total_subjects,
        total_grades=total_grades,
        active_tab=active_tab,  # Pass the active tab to the template
        # Role-based assignment data with safety checks
        assignment_summary=assignment_summary if assignment_summary else {},
        total_subjects_taught=assignment_summary.get('total_subjects_taught', 0) if assignment_summary else 0,
        can_manage_classes=assignment_summary.get('can_manage_classes', False) if assignment_summary else False,
        grades_involved=assignment_summary.get('grades_involved', []) if assignment_summary else [],
        streams_involved=assignment_summary.get('streams_involved', []) if assignment_summary else [],
        subjects_involved=assignment_summary.get('subjects_involved', []) if assignment_summary else [],
        has_assignments=has_assignments,  # Pass assignment status to template
        portal_summary=portal_summary if portal_summary and isinstance(portal_summary, dict) else {},  # Pass portal summary for multi-class interface
        my_students=my_students  # Pass the students for the teacher's assigned class
    )

@classteacher_bp.route('/all_reports', methods=['GET'])
@limiter.limit("60 per minute")
@classteacher_required()
def all_reports():
    """Redirect /all_reports to /view_all_reports; always return a valid response (adds safeguards)."""
    try:
        target = url_for('classteacher.view_all_reports')
        if request.query_string:
            qs = request.query_string.decode('utf-8')
            target = f"{target}?{qs}"
        resp = redirect(target)
        # Ensure Flask Response
        from werkzeug.wrappers import Response as _Resp
        if not isinstance(resp, _Resp):
            return redirect(target)  # attempt again
        return resp
    except Exception as e:
        logger.error(f"all_reports redirect failed: {e}")
        # Fallback: try to call the view function directly
        try:
            return view_all_reports()
        except Exception as inner:
            logger.error(f"all_reports fallback failed: {inner}")
            return jsonify({"error":"Unable to load reports"}), 500

@classteacher_bp.route('/debug_grades', methods=['GET'])
def debug_grades_route():
    """Debug route to check what grades are in the database"""
    try:
        from ..models.academic import Grade
        grades = Grade.query.all()
        
        result = "<h2>Grades in Database</h2><ul>"
        for grade in grades:
            result += f"<li>ID: {grade.id}, Name: '{grade.name}', Level: {grade.education_level}</li>"
        result += f"</ul><p>Total: {len(grades)} grades</p>"
        
        # Check educational level mapping
        from ..utils.constants import educational_level_mapping
        educational_level_mapping_ui = educational_level_mapping.copy()
        educational_level_mapping_ui['pre_primary'] = ['PP1', 'PP2']
        educational_level_mapping_ui['lower_primary'] = ['Grade 1', 'Grade 2', 'Grade 3']
        
        result += "<h2>Educational Level Mapping Results</h2>"
        grade_names = [g.name for g in grades]
        for level, expected_grades in educational_level_mapping_ui.items():
            matches = [name for name in expected_grades if name in grade_names]
            result += f"<p><strong>{level}:</strong> Expected {expected_grades} → Found {matches}</p>"
        
        return result
        
    except Exception as e:
        return f"<h2>Error</h2><p>{str(e)}</p>"

@classteacher_bp.route('/debug_reinitialize', methods=['GET'])
def debug_reinitialize_db():
    """Debug route to reinitialize database with updated structure"""
    try:
        from ..models.academic import Grade, Stream
        from ..utils.database_init import initialize_database_completely
        
        # Drop and recreate all tables
        initialize_database_completely()
        
        # Check what grades we now have
        grades = Grade.query.all()
        grades_data = [(g.name, g.education_level) for g in grades]
        
        return f"""
        <h1>Database Reinitialized Successfully</h1>
        <p>Grades available: {grades_data}</p>
        <p>Total grades: {len(grades)}</p>
        <a href="/classteacher/debug_grades">Check Grades Now</a>
        """
    except Exception as e:
        import traceback
        return f"Error reinitializing database: {str(e)}<br><pre>{traceback.format_exc()}</pre>"

@classteacher_bp.route('/manage_students', methods=['GET', 'POST'])
@limiter.limit("30 per minute")
@classteacher_required()
def manage_students():
    """Route for managing students."""
    print("Entering manage_students route")

    # Get the teacher
    teacher_id = session.get('teacher_id')
    print(f"Teacher ID: {teacher_id}")

    teacher = safe_get(Teacher, teacher_id)
    print(f"Teacher: {teacher}")

    if not teacher:
        print("Teacher not found")
        flash("Teacher not found.", "error")
        return redirect(url_for('classteacher.dashboard'))

    # Define canonical educational level mapping and order for UI
    from ..utils.constants import educational_level_mapping, EDUCATION_LEVELS_ORDER
    educational_level_mapping_ui = educational_level_mapping

    # Initialize variables
    stream = None
    grade = None
    stream_id = None

    # Check if teacher is assigned to a stream
    if teacher.stream_id:
        stream = safe_get(Stream, teacher.stream_id)
        if stream:
            grade = safe_get(Grade, stream.grade_id)
            stream_id = stream.id
            print(f"Stream: {stream}")
            print(f"Grade: {grade}")

    # Get filter parameters
    educational_level = request.args.get('educational_level', '')
    grade_id = request.args.get('grade_id', '')
    stream_id_filter = request.args.get('stream_id', '')
    search_query = request.args.get('search', '').strip()

    # If a stream_id is provided in the URL, use it instead of the teacher's assigned stream
    if stream_id_filter:
        stream_id = stream_id_filter
    stream = safe_get(Stream, stream_id)
    if stream:
        grade = safe_get(Grade, stream.grade_id)

    # If a grade_id is provided in the URL, use it for filtering
    if grade_id:
        grade = safe_get(Grade, grade_id)

    # Get pagination parameters
    page = request.args.get('page', 1, type=int)
    per_page = 20  # Number of students per page

    # Build the query based on filters
    students_query = Student.query

    # Apply search if provided
    if search_query:
        # Search by name or admission number
        students_query = students_query.filter(
            (Student.name.ilike(f'%{search_query}%')) |
            (Student.admission_number.ilike(f'%{search_query}%'))
        )

    # Filter by stream if specified
    if stream_id:
        students_query = students_query.filter_by(stream_id=stream_id)

    # Filter by grade if specified (and not already filtered by stream)
    elif grade and not stream_id:
        # Get all streams for this grade
        grade_streams = Stream.query.filter_by(grade_id=grade.id).all()
        stream_ids = [s.id for s in grade_streams]
        if stream_ids:
            students_query = students_query.filter(Student.stream_id.in_(stream_ids))

    # Filter by educational level if specified
    if educational_level:
        # Get all grades for this educational level
        allowed_grades = educational_level_mapping_ui.get(educational_level, [])
        grades = Grade.query.filter(Grade.name.in_(allowed_grades)).all()
        grade_ids = [g.id for g in grades]

        # Get all streams for these grades
        streams = Stream.query.filter(Stream.grade_id.in_(grade_ids)).all()
        stream_ids = [s.id for s in streams]

        if stream_ids:
            students_query = students_query.filter(Student.stream_id.in_(stream_ids))

    # Count total students matching the filters
    total_students = students_query.count()
    print(f"Number of students matching filters: {total_students}")

    # Apply pagination
    students_paginated = students_query.order_by(Student.name).paginate(page=page, per_page=per_page, error_out=False)
    students = students_paginated.items

    # Get all grades for the template
    grades = [{"id": grade.id, "name": grade.name} for grade in Grade.query.all()]
    
    # DEBUG: Print what grades we're sending to the template
    print("=== DEBUG: manage_students grades data ===")
    print(f"Grades being sent to template: {grades}")
    print(f"Educational level mapping: {educational_level_mapping_ui}")
    print("===========================================")

    # Handle form submissions
    if request.method == 'POST':
        action = request.form.get('action', '')

        # Add new student
        if action == 'add_student':
            name = request.form.get('name', '').strip()
            admission_number = request.form.get('admission_number', '').strip()
            gender = request.form.get('gender', '').strip()

            if not name or not admission_number:
                flash("Name and admission number are required.", "error")
            else:
                # Check if admission number already exists
                existing_student = Student.query.filter_by(admission_number=admission_number).first()
                if existing_student:
                    flash(f"Admission number '{admission_number}' is already in use.", "error")
                else:
                    # Get stream_id from form if teacher is not assigned to a stream
                    selected_stream_id = request.form.get('stream')

                    # Use the teacher's assigned stream if available, otherwise use the selected stream
                    final_stream_id = stream_id if stream_id else selected_stream_id

                    # Stream is now optional, so we don't need to check if it's provided

                    # Get grade_id from stream if stream_id is provided
                    grade_id = None
                    if final_stream_id:
                        stream = safe_get(Stream, final_stream_id)
                        if stream:
                            grade_id = stream.grade_id

                    # Add new student
                    student = Student(
                        name=name,
                        admission_number=admission_number,
                        stream_id=final_stream_id if final_stream_id else None,
                        grade_id=grade_id,  # Set grade_id based on stream
                        gender=gender.lower() if gender else "unknown"
                    )
                    db.session.add(student)
                    db.session.commit()
                    flash(f"Student '{name}' added successfully.", "success")
                    return redirect(url_for('classteacher.manage_students'))

        # Delete student
        elif action == 'delete_student':
            student_id = request.form.get('student_id')
            if student_id:
                student = safe_get(Student, student_id)
                if student:
                    try:
                        # TEMPORARY FIX: Use raw SQL to avoid parent email log schema issues

                        # First delete parent-student relationships using raw SQL
                        try:
                            # Check if parent_student table exists
                            result = db.session.execute(text("SHOW TABLES LIKE 'parent_student'"))
                            if result.fetchone():
                                # Use raw SQL to avoid triggering parent email log queries
                                db.session.execute(
                                    text("DELETE FROM parent_student WHERE student_id = :student_id"),
                                    {"student_id": student.id}
                                )
                                print(f"Deleted parent-student relationships for student {student.id}")
                        except Exception as parent_error:
                            # Parent table might not exist or have schema issues, continue with deletion
                            print(f"Warning: Could not delete parent relationships: {parent_error}")

                        # Delete any parent email logs using raw SQL to avoid ORM schema issues
                        try:
                            result = db.session.execute(text("SHOW TABLES LIKE 'parent_email_log'"))
                            if result.fetchone():
                                # Use raw SQL to avoid column schema issues
                                db.session.execute(
                                    text("DELETE FROM parent_email_log WHERE student_id = :student_id"),
                                    {"student_id": student.id}
                                )
                                print(f"Deleted parent email logs for student {student.id}")
                        except Exception as email_log_error:
                            # Email log table might have schema issues, continue with deletion
                            print(f"Warning: Could not delete parent email logs: {email_log_error}")

                        # Delete component marks that reference the marks
                        marks = Mark.query.filter_by(student_id=student.id).all()
                        for mark in marks:
                            # Delete component marks first
                            ComponentMark.query.filter_by(mark_id=mark.id).delete()

                        # Then delete the marks
                        Mark.query.filter_by(student_id=student.id).delete()

                        # Finally delete the student
                        db.session.delete(student)
                        db.session.commit()
                        flash(f"Student '{student.name}' deleted successfully.", "success")
                    except Exception as e:
                        db.session.rollback()
                        flash(f"Error deleting student: {str(e)}", "error")
                    return redirect(url_for('classteacher.manage_students'))

        # Bulk delete students
        elif action == 'bulk_delete_students':
            student_ids = request.form.getlist('student_ids')
            if student_ids:
                deleted_count = 0
                try:
                    for student_id in student_ids:
                        student = safe_get(Student, student_id)
                        if student:
                            # TEMPORARY FIX: Use raw SQL to avoid parent email log schema issues

                            # First delete parent-student relationships using raw SQL
                            try:
                                # Check if parent_student table exists
                                result = db.session.execute(text("SHOW TABLES LIKE 'parent_student'"))
                                if result.fetchone():
                                    # Use raw SQL to avoid triggering parent email log queries
                                    db.session.execute(
                                        text("DELETE FROM parent_student WHERE student_id = :student_id"),
                                        {"student_id": student.id}
                                    )
                                    print(f"Deleted parent-student relationships for student {student.id}")
                            except Exception as parent_error:
                                # Parent table might not exist or have schema issues, continue with deletion
                                print(f"Warning: Could not delete parent relationships: {parent_error}")

                            # Delete any parent email logs using raw SQL to avoid ORM schema issues
                            try:
                                result = db.session.execute(text("SHOW TABLES LIKE 'parent_email_log'"))
                                if result.fetchone():
                                    # Use raw SQL to avoid column schema issues
                                    db.session.execute(
                                        text("DELETE FROM parent_email_log WHERE student_id = :student_id"),
                                        {"student_id": student.id}
                                    )
                                    print(f"Deleted parent email logs for student {student.id}")
                            except Exception as email_log_error:
                                # Email log table might have schema issues, continue with deletion
                                print(f"Warning: Could not delete parent email logs: {email_log_error}")

                            # Delete component marks that reference the marks
                            marks = Mark.query.filter_by(student_id=student.id).all()
                            for mark in marks:
                                # Delete component marks first
                                ComponentMark.query.filter_by(mark_id=mark.id).delete()

                            # Then delete the marks
                            Mark.query.filter_by(student_id=student.id).delete()

                            # Finally delete the student
                            db.session.delete(student)
                            deleted_count += 1

                    if deleted_count > 0:
                        db.session.commit()
                        flash(f"Successfully deleted {deleted_count} student(s).", "success")
                    else:
                        flash("No students were deleted.", "error")

                except Exception as e:
                    db.session.rollback()
                    flash(f"Error deleting students: {str(e)}", "error")

                return redirect(url_for('classteacher.manage_students'))

        # Bulk update genders
        elif action == 'bulk_update_genders':
            updated_count = 0
            for key, value in request.form.items():
                if key.startswith('gender_') and value:
                    student_id = key.replace('gender_', '')
                    student = safe_get(Student, student_id)
                    if student and student.gender != value.lower():
                        student.gender = value.lower()
                        updated_count += 1

            if updated_count > 0:
                db.session.commit()
                flash(f"Successfully updated gender for {updated_count} student(s).", "success")
            else:
                flash("No gender information was updated.", "info")

            return redirect(url_for('classteacher.manage_students'))

        # Bulk edit students
        elif action == 'bulk_edit_students':
            student_ids = request.form.getlist('student_ids')
            if not student_ids:
                flash('No students selected for editing.', 'error')
                return redirect(url_for('classteacher.manage_students'))

            edit_type = request.form.get('bulk_edit_type')

            if edit_type == 'gender':
                # Update gender for selected students
                gender = request.form.get('bulk_gender')
                if not gender:
                    flash('Please select a gender.', 'error')
                    return redirect(url_for('classteacher.manage_students'))

                updated_count = 0
                for student_id in student_ids:
                    student = safe_get(Student, student_id)
                    if student:
                        student.gender = gender.lower()
                        updated_count += 1

                if updated_count > 0:
                    db.session.commit()
                    flash(f'Successfully updated gender for {updated_count} student(s).', 'success')
                else:
                    flash('No students were updated.', 'info')

            elif edit_type == 'stream':
                # Move students to a different stream
                grade_id = request.form.get('bulk_grade')
                stream_id = request.form.get('bulk_stream')

                if not grade_id or not stream_id:
                    flash('Please select both grade and stream.', 'error')
                    return redirect(url_for('classteacher.manage_students'))

                # Verify that the stream belongs to the selected grade
                stream = safe_get(Stream, stream_id)
                if not stream or str(stream.grade_id) != grade_id:
                    flash('Invalid stream selected.', 'error')
                    return redirect(url_for('classteacher.manage_students'))

                updated_count = 0
                for student_id in student_ids:
                    student = Student.query.get(student_id)
                    if student:
                        student.stream_id = stream_id
                        updated_count += 1

                if updated_count > 0:
                    db.session.commit()
                    flash(f'Successfully moved {updated_count} student(s) to {stream.name}.', 'success')
                else:
                    flash('No students were moved.', 'info')

            else:
                flash('Invalid edit type.', 'error')

            return redirect(url_for('classteacher.manage_students'))

        # Transfer student to different grade/stream
        elif action == 'transfer_student':
            student_id = request.form.get('student_id')
            new_grade_id = request.form.get('new_grade_id')
            new_stream_id = request.form.get('new_stream_id')

            if not student_id or not new_grade_id or not new_stream_id:
                flash('Please select a student, grade, and stream for transfer.', 'error')
                return redirect(url_for('classteacher.manage_students'))

            try:
                student = Student.query.get(student_id)
                if not student:
                    flash('Student not found.', 'error')
                    return redirect(url_for('classteacher.manage_students'))

                # Verify that the stream belongs to the selected grade
                stream = Stream.query.get(new_stream_id)
                grade = Grade.query.get(new_grade_id)
                
                if not stream or not grade:
                    flash('Invalid grade or stream selected.', 'error')
                    return redirect(url_for('classteacher.manage_students'))

                if stream.grade_id != int(new_grade_id):
                    flash('The selected stream does not belong to the selected grade.', 'error')
                    return redirect(url_for('classteacher.manage_students'))

                # Store old information for the flash message
                old_grade_name = student.stream.grade.name if student.stream and student.stream.grade else "Unknown Grade"
                old_stream_name = student.stream.name if student.stream else "Unknown Stream"

                # Update student's grade and stream
                student.grade_id = new_grade_id
                student.stream_id = new_stream_id
                
                db.session.commit()
                
                flash(f"Student '{student.name}' successfully transferred from {old_grade_name} Stream {old_stream_name} to {grade.name} Stream {stream.name}.", "success")

            except Exception as e:
                db.session.rollback()
                flash(f"Error transferring student: {str(e)}", "error")

            return redirect(url_for('classteacher.manage_students'))

        # Bulk upload students
        elif action == 'bulk_upload_students':
            print("Processing bulk upload of students")
            if 'student_file' not in request.files:
                flash("No file part", "error")
                return redirect(request.url)

            file = request.files['student_file']
            if file.filename == '':
                flash("No selected file", "error")
                return redirect(request.url)

            if file:
                try:
                    # Check file extension
                    filename = secure_filename(file.filename)
                    file_ext = os.path.splitext(filename)[1].lower()

                    if file_ext == '.csv':
                        df = pd.read_csv(file)
                    elif file_ext in ['.xlsx', '.xls']:
                        df = pd.read_excel(file)
                    else:
                        flash("Unsupported file format. Please upload a CSV or Excel file.", "error")
                        return redirect(request.url)

                    # Normalize column names to handle variations
                    df.columns = df.columns.str.strip().str.lower()

                    # Map common column name variations
                    column_mapping = {
                        'student_name': 'name',
                        'student name': 'name',
                        'full_name': 'name',
                        'full name': 'name',
                        'addmission_number': 'admission_number',
                        'addmission number': 'admission_number',
                        'admission_no': 'admission_number',
                        'admission no': 'admission_number',
                        'adm_number': 'admission_number',
                        'adm number': 'admission_number',
                        'adm_no': 'admission_number',
                        'adm no': 'admission_number',
                        'reg_number': 'admission_number',
                        'reg number': 'admission_number',
                        'registration_number': 'admission_number',
                        'registration number': 'admission_number'
                    }

                    # Apply column mapping
                    df.rename(columns=column_mapping, inplace=True)

                    # Check required columns after mapping
                    required_columns = ['name']
                    missing_columns = []
                    for col in required_columns:
                        if col not in df.columns:
                            missing_columns.append(col)

                    if missing_columns:
                        available_columns = list(df.columns)
                        flash(f"Missing required column(s): {', '.join(missing_columns)}. Available columns: {', '.join(available_columns)}. Please ensure your file has a 'name' column.", "error")
                        return redirect(request.url)

                    # Process each row
                    success_count = 0
                    error_count = 0
                    error_details = []

                    for index, row in df.iterrows():
                        try:
                            name = str(row['name']).strip()

                            # Handle admission number (might be missing or have different column name)
                            admission_number = None
                            if 'admission_number' in row and pd.notna(row['admission_number']):
                                admission_number = str(row['admission_number']).strip()

                            # If no admission number, generate one
                            if not admission_number or admission_number == 'nan':
                                # Generate admission number based on name and index
                                name_part = ''.join(c for c in name.replace(' ', '')[:3] if c.isalnum()).upper()
                                admission_number = f"ADM{name_part}{index+1:03d}"

                            # Optional fields
                            gender = str(row.get('gender', '')).strip().lower() if pd.notna(row.get('gender', '')) else 'unknown'

                            # Handle grade and stream from Excel file
                            excel_grade = None
                            excel_stream = None

                            if 'grade' in row and pd.notna(row['grade']):
                                excel_grade = str(row['grade']).strip()

                            if 'stream' in row and pd.notna(row['stream']):
                                excel_stream = str(row['stream']).strip().upper()

                            # Skip empty rows
                            if not name:
                                error_details.append(f"Row {index+1}: Missing student name")
                                error_count += 1
                                continue

                            # Check if admission number already exists
                            existing_student = Student.query.filter_by(admission_number=admission_number).first()
                            if existing_student:
                                error_details.append(f"Row {index+1}: Student with admission number {admission_number} already exists")
                                error_count += 1
                                continue

                            # Determine stream_id
                            final_stream_id = None

                            # Priority 1: Use grade and stream from Excel file
                            if excel_grade and excel_stream:
                                # Find the stream based on grade and stream from Excel
                                grade_obj = Grade.query.filter_by(name=excel_grade).first()
                                if not grade_obj:
                                    # Try variations like "Grade 1", "1", etc.
                                    grade_variations = [
                                        f"Grade {excel_grade}",
                                        excel_grade.replace("Grade ", ""),
                                        excel_grade
                                    ]
                                    for variation in grade_variations:
                                        grade_obj = Grade.query.filter_by(name=variation).first()
                                        if grade_obj:
                                            break

                                if grade_obj:
                                    stream_obj = Stream.query.filter_by(grade_id=grade_obj.id, name=excel_stream).first()
                                    if stream_obj:
                                        final_stream_id = stream_obj.id
                                    else:
                                        error_details.append(f"Row {index+1}: Stream '{excel_stream}' not found for grade '{excel_grade}'")
                                else:
                                    error_details.append(f"Row {index+1}: Grade '{excel_grade}' not found")

                            # Priority 2: Use teacher's assigned stream
                            if not final_stream_id and stream_id:
                                final_stream_id = stream_id

                            # Priority 3: Use selected stream from form
                            if not final_stream_id:
                                selected_stream_id = request.form.get('stream')
                                if selected_stream_id:
                                    final_stream_id = selected_stream_id

                            # Add new student
                            student = Student(
                                name=name,
                                admission_number=admission_number,
                                stream_id=final_stream_id if final_stream_id else None,
                                gender=gender
                            )
                            db.session.add(student)
                            success_count += 1

                        except Exception as e:
                            error_details.append(f"Row {index+1}: Error processing student - {str(e)}")
                            error_count += 1
                            continue

                    db.session.commit()

                    # Provide detailed feedback
                    if success_count > 0:
                        success_msg = f"Successfully added {success_count} students."
                        if error_count > 0:
                            success_msg += f" {error_count} errors encountered."
                        flash(success_msg, "success")

                        # Show first few error details if any
                        if error_details:
                            error_summary = "; ".join(error_details[:3])  # Show first 3 errors
                            if len(error_details) > 3:
                                error_summary += f" ... and {len(error_details) - 3} more errors"
                            flash(f"Error details: {error_summary}", "warning")
                    else:
                        error_msg = f"No students added. {error_count} errors encountered."
                        if error_details:
                            error_summary = "; ".join(error_details[:5])  # Show first 5 errors
                            error_msg += f" Details: {error_summary}"
                        flash(error_msg, "error")

                    return redirect(url_for('classteacher.manage_students'))

                except Exception as e:
                    flash(f"An error occurred: {str(e)}", "error")
                    print(f"Error processing file: {str(e)}")
                    return redirect(request.url)

    print("Rendering manage_students.html template")
    return render_template(
        'manage_students.html',
        students=students,
        pagination=students_paginated,
        total_students=total_students,
        grade=grade.name if grade else "",
        stream=stream.name if stream else "",
        grades=grades,
        educational_level_mapping=educational_level_mapping_ui,
        educational_levels=EDUCATION_LEVELS_ORDER,
        selected_level=educational_level
    )


@classteacher_bp.route('/transfer_student', methods=['POST'])
@csrf.exempt
@classteacher_required
def transfer_student():
    """Route for transferring a student to a different grade/stream via JSON API."""
    try:
        print("🔄 transfer_student endpoint hit")
        # Get JSON data from request
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400

        student_id = data.get('student_id')
        target_stream_id = data.get('target_stream_id')
        reason = data.get('reason', '')

        if not student_id or not target_stream_id:
            return jsonify({'success': False, 'message': 'Student ID and target stream ID are required'}), 400

        # Get student
        student = Student.query.get(student_id)
        if not student:
            return jsonify({'success': False, 'message': 'Student not found'}), 404

        # Get target stream and grade
        target_stream = Stream.query.get(target_stream_id)
        if not target_stream:
            return jsonify({'success': False, 'message': 'Target stream not found'}), 404

        target_grade = target_stream.grade
        if not target_grade:
            return jsonify({'success': False, 'message': 'Target grade not found'}), 404

        # Store old information for the response message
        old_grade_name = student.stream.grade.name if student.stream and student.stream.grade else "Unknown Grade"
        old_stream_name = student.stream.name if student.stream else "Unknown Stream"

        # Update student's grade and stream
        student.grade_id = target_grade.id
        student.stream_id = target_stream_id
        
        db.session.commit()
        
        message = f"Student '{student.name}' successfully transferred from {old_grade_name} Stream {old_stream_name} to {target_grade.name} Stream {target_stream.name}."
        if reason:
            message += f" Reason: {reason}"

        return jsonify({
            'success': True, 
            'message': message,
            'student_name': student.name,
            'old_grade': old_grade_name,
            'old_stream': old_stream_name,
            'new_grade': target_grade.name,
            'new_stream': target_stream.name
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Error transferring student: {str(e)}'}), 500


@classteacher_bp.route('/edit_class_marks/<grade>/<stream>/<term>/<assessment_type>')
@classteacher_required
def edit_class_marks(grade, stream, term, assessment_type):
    """Route for editing class marks."""
    stream_obj = Stream.query.join(Grade).filter(Grade.name == grade, Stream.name == stream[-1]).first()
    term_obj = Term.query.filter_by(name=term).first()
    assessment_type_obj = AssessmentType.query.filter_by(name=assessment_type).first()

    if not (stream_obj and term_obj and assessment_type_obj):
        flash("Invalid grade, stream, term, or assessment type", "error")
        return redirect(url_for('classteacher.dashboard'))

    # Get the selected subjects from the form
    selected_subjects = []
    for key, value in request.form.items():
        if key.startswith('include_subject_') and value:
            try:
                subject_id = int(value)
                selected_subjects.append(subject_id)
            except (ValueError, TypeError):
                pass

    # Get class report data
    report_data = get_class_report_data(grade, stream, term, assessment_type, selected_subject_ids=selected_subjects)

    if not report_data or report_data.get("error"):
        error_msg = report_data.get("error") if report_data and report_data.get("error") else f"No marks found for {grade} Stream {stream[-1]} in {term} {assessment_type}"
        flash(error_msg, "error")
        return redirect(url_for('classteacher.dashboard'))

    # Get the class data from the report
    class_data = report_data.get("class_data", [])

    # Get education level from report_data or determine based on grade
    if report_data.get("education_level"):
        education_level_code = report_data.get("education_level")
        if education_level_code == "lower_primary":
            education_level = "lower primary"
        elif education_level_code == "upper_primary":
            education_level = "upper primary"
        elif education_level_code == "junior_secondary":
            education_level = "junior secondary"
        else:
            education_level = ""
    else:
        # Fallback to determining education level based on grade
        education_level = ""
        grade_num = int(grade.split()[1]) if len(grade.split()) > 1 else int(grade)
        if 1 <= grade_num <= 3:
            education_level = "lower primary"
        elif 4 <= grade_num <= 6:
            education_level = "upper primary"
        elif 7 <= grade_num <= 9:
            education_level = "junior secondary"

    # Get subjects for this grade based on education level
    grade_obj = Grade.query.filter_by(name=grade).first()
    if grade_obj:
        # Filter subjects by education level, then EXCLUDE composite subjects
        if education_level == "lower primary":
            filtered_subjects = Subject.query.filter_by(education_level="lower_primary").all()
        elif education_level == "upper primary":
            filtered_subjects = Subject.query.filter_by(education_level="upper_primary").all()
        elif education_level == "junior secondary":
            filtered_subjects = Subject.query.filter_by(education_level="junior_secondary").all()
        else:
            filtered_subjects = Subject.query.all()

        # Exclude composite subjects from edit marks UI
        filtered_subjects = [s for s in filtered_subjects if not getattr(s, 'is_composite', False)]

        # Get subject names, IDs, and objects
        subject_names = [subject.name for subject in filtered_subjects]
        subject_ids = [subject.id for subject in filtered_subjects]
        subject_objects = filtered_subjects  # Pass the actual subject objects

        # Debug: Print subject IDs and composite status
        print("\n=== SUBJECTS BEING PASSED TO TEMPLATE (COMPOSITES EXCLUDED) ===")
        for subject in filtered_subjects:
            print(f"Subject: {subject.name}, ID: {subject.id}, Is Composite: {getattr(subject, 'is_composite', False)}")
        print("================================================================")
    else:
        # Use subjects from report data if grade not found
        # Then exclude composite subjects
        report_subject_names = report_data.get("subjects", [])
        subject_names = []
        subject_ids = []
        for subject_name in report_subject_names:
            subject = Subject.query.filter_by(name=subject_name).first()
            if subject and not getattr(subject, 'is_composite', False):
                subject_names.append(subject.name)
                subject_ids.append(subject.id)

    # Get subject total marks from the database
    subject_total_marks = {}
    for subject_name, subject_id in zip(subject_names, subject_ids):
        # Check if any marks exist for this subject to get the total_marks
        mark = Mark.query.filter_by(
            subject_id=subject_id,
            term_id=term_obj.id,
            assessment_type_id=assessment_type_obj.id
        ).first()

        if mark:
            subject_total_marks[subject_name] = mark.total_marks
        else:
            # Default to 100 if no marks exist
            subject_total_marks[subject_name] = 100

    # Get student IDs for the form and fetch actual marks from the database
    for student_data in class_data:
        student = Student.query.filter_by(name=student_data["student"]).first()
        if student:
            student_data["student_id"] = student.id

            # Get actual marks from the database for this student
            filtered_marks = {}
            filtered_raw_marks = {}
            for subject_name in subject_names:
                subject = Subject.query.filter_by(name=subject_name).first()
                if subject:
                    # Check if mark exists in database
                    mark = Mark.query.filter_by(
                        student_id=student.id,
                        subject_id=subject.id,
                        term_id=term_obj.id,
                        assessment_type_id=assessment_type_obj.id
                    ).first()

                    if mark:
                        # Store both the standardized mark (out of 100) and the raw mark
                        total_marks = mark.total_marks if mark.total_marks > 0 else 100
                        raw_mark = mark.mark

                        # Calculate standardized mark (out of 100)
                        standardized_mark = (raw_mark / total_marks) * 100

                        filtered_marks[subject_name] = standardized_mark
                        filtered_raw_marks[subject_name] = raw_mark
                    else:
                        # If no mark in database, use the one from report data if available
                        filtered_marks[subject_name] = student_data["marks"].get(subject_name, 0)
                        filtered_raw_marks[subject_name] = student_data["marks"].get(subject_name, 0)
                else:
                    filtered_marks[subject_name] = student_data["marks"].get(subject_name, 0)
                    filtered_raw_marks[subject_name] = student_data["marks"].get(subject_name, 0)
        else:
            student_data["student_id"] = 0
            # Filter marks to only include these subjects
            filtered_marks = {subject: student_data["marks"].get(subject, 0) for subject in subject_names}
            filtered_raw_marks = {subject: student_data["marks"].get(subject, 0) for subject in subject_names}

        student_data["filtered_marks"] = filtered_marks
        student_data["filtered_raw_marks"] = filtered_raw_marks

    # Helper function to get component marks
    def get_component_mark(student_id, component_id):
        """Get the component mark for a student and component."""
        from ..models.academic import ComponentMark

        try:
            # Find the mark in the database
            component_mark = ComponentMark.query.filter_by(
                component_id=component_id
            ).join(
                Mark, ComponentMark.mark_id == Mark.id
            ).filter(
                Mark.student_id == student_id,
                Mark.term_id == term_obj.id,
                Mark.assessment_type_id == assessment_type_obj.id
            ).first()

            # Debug output
            if component_mark:
                print(f"Found component mark: student_id={student_id}, component_id={component_id}, raw_mark={component_mark.raw_mark}, max_raw_mark={component_mark.max_raw_mark}, percentage={component_mark.percentage}")

            return component_mark
        except Exception as e:
            print(f"Error getting component mark: {e}")
            return None

    return render_template(
        'edit_class_marks.html',
        grade=grade,
        stream=stream,
        term=term,
        assessment_type=assessment_type,
        class_data=class_data,
        subject_names=subject_names,
        subject_ids=subject_ids,
        subject_objects=subject_objects,
        subject_total_marks=subject_total_marks,
        education_level=education_level,
        get_component_mark=get_component_mark
    )

@classteacher_bp.route('/update_class_marks/<grade>/<stream>/<term>/<assessment_type>', methods=['POST'])
@enforce('marks', 'write', class_scope=True, grade_arg='grade', stream_arg='stream')
def update_class_marks(grade, stream, term, assessment_type):
    """Route for updating class marks."""
    stream_obj = Stream.query.join(Grade).filter(Grade.name == grade, Stream.name == stream[-1]).first()
    term_obj = Term.query.filter_by(name=term).first()
    assessment_type_obj = AssessmentType.query.filter_by(name=assessment_type).first()

    if not (stream_obj and term_obj and assessment_type_obj):
        flash("Invalid grade, stream, term, or assessment type", "error")
        return redirect(url_for('classteacher.dashboard'))

    # Determine education level based on grade
    education_level = ""
    grade_num = int(grade.split()[1]) if len(grade.split()) > 1 else int(grade)
    if 1 <= grade_num <= 3:
        education_level = "lower primary"
    elif 4 <= grade_num <= 6:
        education_level = "upper primary"
    elif 7 <= grade_num <= 9:
        education_level = "junior secondary"

    # Get subjects for this grade based on education level and EXCLUDE composite subjects
    if education_level == "lower primary":
        filtered_subjects = Subject.query.filter_by(education_level="lower_primary").all()
    elif education_level == "upper primary":
        filtered_subjects = Subject.query.filter_by(education_level="upper_primary").all()
    elif education_level == "junior secondary":
        filtered_subjects = Subject.query.filter_by(education_level="junior_secondary").all()
    else:
        filtered_subjects = Subject.query.all()

    # Exclude composite subjects; edit marks page should only use independent subjects now
    filtered_subjects = [s for s in filtered_subjects if not getattr(s, 'is_composite', False)]

    # Get students in this stream
    students = Student.query.filter_by(stream_id=stream_obj.id).all()

    # Process form data
    marks_updated = 0
    component_marks_updated = 0

    for student in students:
        for subject in filtered_subjects:
            # Only regular subjects (composites already excluded)
                # Regular subject (not composite)
                mark_key = f"mark_{student.id}_{subject.id}"
                total_marks_key = f"total_marks_{subject.id}"

                if mark_key in request.form and total_marks_key in request.form:
                    try:
                        # Get the raw mark and total marks from the form
                        raw_mark = int(request.form[mark_key])
                        max_raw_mark = int(request.form[total_marks_key])

                        # Sanitize the raw mark and total marks to ensure they're within acceptable ranges
                        raw_mark, max_raw_mark = MarkConversionService.sanitize_raw_mark(raw_mark, max_raw_mark)

                        # Calculate percentage using our service
                        percentage = MarkConversionService.calculate_percentage(raw_mark, max_raw_mark)

                        # Find existing mark or create new one
                        mark = Mark.query.filter_by(
                            student_id=student.id,
                            subject_id=subject.id,
                            term_id=term_obj.id,
                            assessment_type_id=assessment_type_obj.id
                        ).first()

                        if mark:
                            # Update existing mark with both old and new field names
                            mark.mark = raw_mark  # Old field name
                            mark.total_marks = max_raw_mark  # Old field name
                            mark.raw_mark = raw_mark  # New field name
                            mark.max_raw_mark = max_raw_mark  # New field name
                            mark.percentage = percentage
                        else:
                            # Create new mark with both old and new field names
                            mark = Mark(
                                student_id=student.id,
                                subject_id=subject.id,
                                term_id=term_obj.id,
                                assessment_type_id=assessment_type_obj.id,
                                grade_id=stream_obj.grade_id,  # Use grade from stream_obj (from URL)
                                stream_id=stream_obj.id,  # Use stream from stream_obj (from URL)
                                mark=raw_mark,  # Old field name
                                total_marks=max_raw_mark,  # Old field name
                                raw_mark=raw_mark,  # New field name
                                max_raw_mark=max_raw_mark,  # New field name
                                percentage=percentage
                            )
                            db.session.add(mark)

                        marks_updated += 1
                    except ValueError:
                        # Skip invalid values
                        pass

    # Commit changes to database
    try:
        db.session.commit()
        if component_marks_updated > 0:
            flash(f"Successfully updated {marks_updated} marks and {component_marks_updated} component marks.", "success")
        else:
            flash(f"Successfully updated {marks_updated} marks.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error updating marks: {str(e)}", "error")

    # Redirect back to the preview report page where the success message will be visible
    return redirect(url_for('classteacher.preview_class_report',
                          grade=grade, stream=stream, term=term, assessment_type=assessment_type))

@classteacher_bp.route('/download_class_report/<grade>/<stream>/<term>/<assessment_type>')
@enforce('class_report', 'read', class_scope=True, grade_arg='grade', stream_arg='stream')
def download_class_report(grade, stream, term, assessment_type):
    """Route for downloading class reports as PDF."""
    # Check if we have a cached PDF
    cached_pdf = get_cached_pdf(grade, stream, term, assessment_type, "class_report")
    if cached_pdf:
        return send_file(
            cached_pdf,
            as_attachment=True,
            download_name=f"{grade}_{stream}_{term}_{assessment_type}_Class_Report.pdf",
            mimetype='application/pdf'
        )

    # If no cache or cache miss, generate the report
    stream_obj = Stream.query.join(Grade).filter(Grade.name == grade, Stream.name == stream[-1]).first()
    term_obj = Term.query.filter_by(name=term).first()
    assessment_type_obj = AssessmentType.query.filter_by(name=assessment_type).first()

    if not (stream_obj and term_obj and assessment_type_obj):
        flash("Invalid grade, stream, term, or assessment type", "error")
        return redirect(url_for('classteacher.dashboard'))

    # Get the selected subjects from the session if available
    selected_subjects = session.get('selected_subjects', [])

    # Check if this is a subject teacher (role = 'teacher') and filter subjects accordingly
    current_role = get_role(session)
    if current_role == 'teacher':
        # For subject teachers, only show subjects they are assigned to teach
        teacher_id = session.get('teacher_id')
        if teacher_id:
            # Get the grade object
            grade_obj_temp = stream_obj.grade

            # Get subjects assigned to this teacher for this grade/stream
            teacher_assignments = TeacherSubjectAssignment.query.filter_by(
                teacher_id=teacher_id,
                grade_id=grade_obj_temp.id
            ).filter(
                (TeacherSubjectAssignment.stream_id == stream_obj.id) |
                (TeacherSubjectAssignment.stream_id == None)  # Assignments for all streams
            ).all()

            # Get subject IDs from assignments
            teacher_subject_ids = [assignment.subject_id for assignment in teacher_assignments]

            # Override selected_subjects to only include teacher's assigned subjects
            selected_subjects = teacher_subject_ids

    # Get class report data first with selected subjects
    report_data = get_class_report_data(grade, stream, term, assessment_type, selected_subject_ids=selected_subjects)

    if not report_data or report_data.get("error"):
        error_msg = report_data.get("error") if report_data and report_data.get("error") else f"No marks found for {grade} Stream {stream[-1]} in {term} {assessment_type}"
        flash(error_msg, "error")
        return redirect(url_for('classteacher.dashboard'))

    # Get education level from report_data or determine based on grade
    if report_data.get("education_level"):
        education_level_code = report_data.get("education_level")
        if education_level_code == "lower_primary":
            education_level = "lower primary"
        elif education_level_code == "upper_primary":
            education_level = "upper primary"
        elif education_level_code == "junior_secondary":
            education_level = "junior secondary"
        else:
            education_level = ""
    else:
        # Fallback to determining education level based on grade
        education_level = ""
        grade_num = int(grade.split()[1]) if len(grade.split()) > 1 else int(grade)
        if 1 <= grade_num <= 3:
            education_level = "lower primary"
        elif 4 <= grade_num <= 6:
            education_level = "upper primary"
        elif 7 <= grade_num <= 9:
            education_level = "junior secondary"

    # Calculate subject averages for the PDF
    subject_averages = {}
    for subject in report_data["subjects"]:
        subject_total = 0
        subject_count = 0
        for student_data in report_data["class_data"]:
            mark = student_data['marks'].get(subject, 0)
            if mark > 0:
                subject_total += mark
                subject_count += 1

        if subject_count > 0:
            subject_averages[subject] = round(subject_total / subject_count, 2)
        else:
            subject_averages[subject] = 0

    # Calculate class average for the PDF
    class_total = 0
    student_count = 0
    for student_data in report_data["class_data"]:
        if student_data['total_marks'] > 0:
            class_total += student_data['total_marks']
            student_count += 1

    class_average = round(class_total / student_count, 2) if student_count > 0 else 0

    # Get staff information for the report
    from ..services.staff_assignment_service import StaffAssignmentService
    staff_info = StaffAssignmentService.get_report_staff_info(grade, stream)

    # Generate PDF report using HTML-to-PDF conversion for better formatting
    pdf_file = generate_class_report_pdf_from_html(
        grade,
        stream,
        term,
        assessment_type,
        report_data["class_data"],
        report_data["stats"],
        report_data["total_marks"],
        report_data["subjects"],
        education_level,
        subject_averages,
        class_average,
        selected_subject_ids=selected_subjects,
        staff_info=staff_info
    )

    # Notify parents if report generation was successful and notification is requested
    notify_parents = request.form.get('notify_parents') == 'on'
    if pdf_file and notify_parents:
        try:
            from ..services.parent_notification_service import ParentNotificationService
            notification_result = ParentNotificationService.notify_parents_of_new_results(
                grade=grade,
                stream=stream,
                term=term,
                assessment_type=assessment_type,
                report_file_path=pdf_file
            )

            if notification_result.get('success'):
                if notification_result.get('notifications_sent', 0) > 0:
                    flash(f"✅ Report generated and {notification_result['notifications_sent']} parent(s) notified via email", 'success')
                else:
                    flash("✅ Report generated successfully (no parents to notify)", 'success')

                if notification_result.get('notifications_failed', 0) > 0:
                    flash(f"⚠️ {notification_result['notifications_failed']} parent notification(s) failed", 'warning')
            else:
                flash(f"✅ Report generated successfully. Parent notification failed: {notification_result.get('error', 'Unknown error')}", 'warning')

        except Exception as e:
            flash(f"✅ Report generated successfully. Parent notification error: {str(e)}", 'warning')

    if not pdf_file:
        flash(f"Failed to generate report for {grade} Stream {stream[-1]} in {term} {assessment_type}", "error")
        return redirect(url_for('classteacher.dashboard'))

    # Cache the report data
    cache_report(grade, stream, term, assessment_type, report_data)

    # Cache the PDF file
    with open(pdf_file, 'rb') as f:
        pdf_data = f.read()
    cache_pdf(grade, stream, term, assessment_type, "class_report", pdf_data)

    # Return the PDF file
    filename = f"{grade}_Stream_{stream[-1]}_{term}_{assessment_type}_Report.pdf"
    return send_file(
        pdf_file,
        as_attachment=True,
        download_name=filename,
        mimetype='application/pdf'
    )

@classteacher_bp.route('/print_individual_report/<grade>/<stream>/<term>/<assessment_type>/<student_name>')
@classteacher_required
def print_individual_report(grade, stream, term, assessment_type, student_name):
    """Route for printing individual student reports with a clean format."""
    stream_obj = Stream.query.join(Grade).filter(Grade.name == grade, Stream.name == stream[-1]).first()
    term_obj = Term.query.filter_by(name=term).first()
    assessment_type_obj = AssessmentType.query.filter_by(name=assessment_type).first()

    if not (stream_obj and term_obj and assessment_type_obj):
        flash("Invalid grade, stream, term, or assessment type", "error")
        return redirect(url_for('classteacher.dashboard'))

    student = Student.query.filter_by(name=student_name, stream_id=stream_obj.id).first()
    if not student:
        flash(f"No data available for student {student_name}.", "error")
        return redirect(url_for('classteacher.dashboard'))

    # Get education level based on grade
    education_level = ""
    grade_num = int(grade.split()[1]) if len(grade.split()) > 1 else int(grade)
    if 1 <= grade_num <= 3:
        education_level = "lower primary"
    elif 4 <= grade_num <= 6:
        education_level = "upper primary"
    elif 7 <= grade_num <= 9:
        education_level = "junior secondary"

    # Get class report data first
    class_data_result = get_class_report_data(grade, stream, term, assessment_type)

    if class_data_result.get("error"):
        flash(class_data_result.get("error"), "error")
        return redirect(url_for('classteacher.dashboard'))

    # Find student data in the class report
    student_data = None
    for data in class_data_result["class_data"]:
        if data["student"] == student.name:
            student_data = data
            break

    if not student_data:
        flash(f"No marks found for {student_name} in {term} {assessment_type}", "error")
        return redirect(url_for('classteacher.dashboard'))

    # Calculate class averages for each subject
    class_data = class_data_result["class_data"]
    subjects = class_data_result["subjects"]
    subject_averages = {}

    for subject in subjects:
        subject_total = 0
        subject_count = 0
        for data in class_data:
            mark = data.get("marks", {}).get(subject, 0)
            if mark != 0 and mark != "-":
                subject_total += mark
                subject_count += 1

        if subject_count > 0:
            subject_averages[subject] = round(subject_total / subject_count, 2)
        else:
            subject_averages[subject] = 0

    # Calculate overall class average
    overall_total = 0
    overall_count = 0
    for data in class_data:
        overall_total += data.get("total_marks", 0)
        overall_count += 1

    class_average = 0
    if overall_count > 0:
        class_average = round((overall_total / overall_count) / len(subjects) * 100, 2) if subjects else 0

    # Calculate mean grade and points
    avg_percentage = student_data.get("average_percentage", 0)
    from ..utils import get_grade_and_points
    mean_grade, mean_points = get_grade_and_points(avg_percentage)

    # Build composite-aware context using ClassReportBuilder
    from ..services.class_report_builder import ClassReportBuilder
    builder_ctx = ClassReportBuilder.build(grade, stream, term, assessment_type, selected_subject_ids=session.get('selected_subjects', []), invalidate=False)
    # Local helper to expand abbreviations to full remarks
    def _full_remarks(code: str) -> str:
        mapping = {
            'EE1': 'Exceeding Expectation 1',
            'EE2': 'Exceeding Expectation 2',
            'ME1': 'Meeting Expectation 1',
            'ME2': 'Meeting Expectation 2',
            'AE1': 'Approaching Expectation 1',
            'AE2': 'Approaching Expectation 2',
            'BE1': 'Below Expectation 1',
            'BE2': 'Below Expectation 2',
        }
        return mapping.get(str(code), str(code))

    composite_structure = builder_ctx.get('composite_structure', {}) or {}
    subject_names = builder_ctx.get('subject_names', class_data_result.get('subjects', []))

    # Find this student's filtered marks from builder (includes composite totals and component marks)
    student_filtered = None
    for sd in builder_ctx.get('class_data', []) or []:
        if sd.get('student') == student.name:
            student_filtered = sd
            break
    filtered_marks = (student_filtered or {}).get('filtered_marks', {})

    # Prepare table data using builder subject order and filtered marks
    table_data = []
    visible_count = 0
    for subject_name in subject_names:
        mark = filtered_marks.get(subject_name, 0)
        if not mark or mark == 0:
            continue
        # Normalize to int where appropriate
        if isinstance(mark, float):
            mark_disp = int(round(mark)) if mark == int(mark) else round(mark, 1)
        else:
            mark_disp = int(mark)

        # Single-assessment display for print (rendered as current/avg the same)
        table_data.append({
            "subject": subject_name,
            "entrance": mark_disp,
            "mid_term": mark_disp,
            "end_term": mark_disp,
            "current_assessment": mark_disp,
            "avg": mark_disp,
            "remarks": get_performance_remarks(mark, class_data_result.get("total_marks", 100))
        })
        visible_count += 1

    # Build composite_data for English/Kiswahili from builder structure using display labels
    composite_data = {}
    for main_name, info in composite_structure.items():
        comp_names = info.get('component_names', [])
        display_labels = info.get('component_display', [])
        components_map = {}
        for idx, comp_name in enumerate(comp_names):
            disp = display_labels[idx] if idx < len(display_labels) else comp_name
            comp_mark = filtered_marks.get(comp_name, 0) or 0
            # Normalize display
            if isinstance(comp_mark, float):
                comp_mark_disp = int(round(comp_mark)) if comp_mark == int(comp_mark) else round(comp_mark, 1)
            else:
                comp_mark_disp = int(comp_mark)
            components_map[disp] = {
                'mark': comp_mark_disp,
                'max_mark': 100,
                'percentage': round(comp_mark, 1) if isinstance(comp_mark, (int, float)) else 0,
                'remarks': get_performance_remarks(comp_mark, 100)
            }
        composite_data[main_name] = {
            'components': components_map,
            'total': filtered_marks.get(main_name, 0) or 0
        }

    # Calculate total marks and points based on visible subjects
    total_marks = student_filtered.get('filtered_total', student_data.get('total_marks', 0)) if student_filtered else student_data.get('total_marks', 0)
    total_possible_marks = visible_count * class_data_result.get("total_marks", 100)
    total_points = mean_points * visible_count

    # Generate admission number if not available
    admission_no = student.admission_number if hasattr(student, 'admission_number') and student.admission_number else f"KPS{grade}{stream[-1]}{student.id}"

    # Get academic year
    academic_year = term_obj.academic_year if hasattr(term_obj, 'academic_year') and term_obj.academic_year else "2023"

    # Get current date for the report
    from datetime import datetime
    current_date = datetime.now().strftime("%Y-%m-%d")

    # Get school information for dynamic display
    from ..services.school_config_service import SchoolConfigService
    school_info = SchoolConfigService.get_school_info_dict()

    # Get dynamic logo URL from school setup
    logo_path = SchoolConfigService.get_school_logo_path()
    logo_url = url_for('static', filename=logo_path)

    # Get staff information for dynamic teacher names
    from ..services.staff_assignment_service import StaffAssignmentService
    # Extract stream letter (e.g., "Stream B" -> "B")
    stream_letter = stream.replace("Stream ", "") if stream.startswith("Stream ") else (stream[-1] if len(stream) > 1 else stream)
    staff_info = StaffAssignmentService.get_report_staff_info(grade, stream_letter)

    # Get subject teachers mapping for the teacher column
    subject_teachers = StaffAssignmentService.get_subject_teachers(grade, stream_letter)
    # Also map composite (logical) subjects to teachers by aggregating component subject teachers
    try:
        from ..models import Grade as GradeModel, TeacherSubjectAssignment
        from ..models.academic import Subject
        # Build once
        grade_obj = GradeModel.query.filter_by(name=grade).first()
        if grade_obj and stream_obj:
            # Determine candidate subject names that might be composites (appear in report subjects but not real Subject rows)
            candidate_names = subject_names  # list from builder
            for cname in candidate_names:
                if cname in subject_teachers:
                    continue
                # If no exact Subject exists, treat as composite name
                subj_obj = Subject.query.filter_by(name=cname).first()
                if subj_obj is None:
                    # Find component subjects bound to this composite name
                    component_subjects = Subject.query.filter_by(composite_parent=cname, is_component=True).all()
                    if component_subjects:
                        # Collect teacher names assigned to these components for this grade/stream
                        names = []
                        for cs in component_subjects:
                            assignments = TeacherSubjectAssignment.query.filter_by(
                                grade_id=grade_obj.id,
                                stream_id=stream_obj.id,
                                subject_id=cs.id
                            ).all()
                            for a in assignments:
                                if a.teacher:
                                    tname = getattr(a.teacher, 'full_name', None) or getattr(a.teacher, 'username', None) or ''
                                    if tname and tname not in names:
                                        names.append(tname)
                        if names:
                            label = ", ".join(names)
                            # Insert a dict compatible with template attribute access
                            subject_teachers[cname] = { 'full_name': label, 'username': label }
    except Exception as _e:
        # Non-fatal: continue without composite teacher aggregation
        pass

    # composite_data populated above

    # Get term information from report configuration
    from ..services.report_config_service import ReportConfigService
    report_config_data = ReportConfigService.get_comprehensive_report_data(grade, stream_letter, term)
    if report_config_data and report_config_data.get('term_info'):
        term_config = report_config_data['term_info']
        next_term_opening = term_config.get('opening_date')
        if next_term_opening:
            next_term_opening_str = next_term_opening.strftime('%B %d, %Y') if hasattr(next_term_opening, 'strftime') else str(next_term_opening)
        else:
            next_term_opening_str = 'TBD'
    else:
        next_term_opening_str = 'TBD'

    term_info = {
        'next_term_opening_date': next_term_opening_str,
        'current_term': term,
        'academic_year': academic_year
    }

    # Legends for grading/weights/status (non-invasive; templates may opt-in)
    try:
        from ..services.mark_calculator_adapter import build_legends  # local import to avoid cycles
        _calc_legends = build_legends()
    except Exception:
        _calc_legends = None

    # Render the PROFESSIONAL print template instead of the Solar Theme preview
    return render_template(
        'print_individual_report.html',
        student=student,
        student_data=student_data,  # Add student_data like preview route
        grade=grade,
        stream=stream,
        term=term,
        assessment_type=assessment_type,
        education_level=education_level,
        current_date=current_date,
        table_data=table_data,
        composite_data=composite_data,
        total=total_marks,
        avg_percentage=avg_percentage,
        mean_grade=mean_grade,
        mean_points=mean_points,
        total_possible_marks=total_possible_marks,
        total_points=total_points,
        admission_no=admission_no,
        academic_year=academic_year,
        print_mode=True,  # Add print_mode like preview route
        school_info=school_info,
        logo_url=logo_url,
        staff_info=staff_info,
        term_info=term_info,
        subject_teachers=subject_teachers,
        # Optional legends for templates
        calculator_legends=_calc_legends
    )

@classteacher_bp.route('/preview_individual_report/<grade>/<stream>/<term>/<assessment_type>/<student_name>')
@classteacher_required
def preview_individual_report(grade, stream, term, assessment_type, student_name):
    # Flags
    print_mode = request.args.get('print', '0') == '1'
    if print_mode:
        return redirect(url_for('classteacher.print_individual_report',
                               grade=grade,
                               stream=stream,
                               term=term,
                               assessment_type=assessment_type,
                               student_name=student_name))
    """Route for previewing individual student reports."""
    stream_obj = Stream.query.join(Grade).filter(Grade.name == grade, Stream.name == stream[-1]).first()
    term_obj = Term.query.filter_by(name=term).first()
    assessment_type_obj = AssessmentType.query.filter_by(name=assessment_type).first()

    if not (stream_obj and term_obj and assessment_type_obj):
        flash("Invalid grade, stream, term, or assessment type", "error")
        return redirect(url_for('classteacher.dashboard'))

    student = Student.query.filter_by(name=student_name, stream_id=stream_obj.id).first()
    if not student:
        flash(f"No data available for student {student_name}.", "error")
        return redirect(url_for('classteacher.dashboard'))

    # Get education level based on grade
    education_level = ""
    grade_num = int(grade.split()[1]) if len(grade.split()) > 1 else int(grade)
    if 1 <= grade_num <= 3:
        education_level = "lower primary"
    elif 4 <= grade_num <= 6:
        education_level = "upper primary"
    elif 7 <= grade_num <= 9:
        education_level = "junior secondary"

    # Get class report data first
    class_data_result = get_class_report_data(grade, stream, term, assessment_type)

    if class_data_result.get("error"):
        flash(class_data_result.get("error"), "error")
        return redirect(url_for('classteacher.dashboard'))

    # Find student data in the class report
    student_data = None
    for data in class_data_result["class_data"]:
        if data["student"] == student.name:
            student_data = data
            break

    if not student_data:
        flash(f"No marks found for {student_name} in {term} {assessment_type}", "error")
        return redirect(url_for('classteacher.dashboard'))

    # Calculate mean grade and points
    avg_percentage = student_data.get("average_percentage", 0)
    from ..utils import get_grade_and_points
    mean_grade, mean_points = get_grade_and_points(avg_percentage)

    # Use ClassReportBuilder to derive composite structure and filtered marks
    from ..services.class_report_builder import ClassReportBuilder
    builder_ctx = ClassReportBuilder.build(grade, stream, term, assessment_type, selected_subject_ids=session.get('selected_subjects', []), invalidate=False)
    composite_structure = builder_ctx.get('composite_structure', {}) or {}
    subject_names = builder_ctx.get('subject_names', class_data_result.get('subjects', []))

    # Pull this student's filtered marks (includes composite totals and components)
    student_filtered = None
    for sd in builder_ctx.get('class_data', []) or []:
        if sd.get('student') == student.name:
            student_filtered = sd
            break
    filtered_marks = (student_filtered or {}).get('filtered_marks', {})

    # Local helper to expand remarks codes to full descriptive text
    def _full_remarks(code: str) -> str:
        mapping = {
            'EE1': 'Exceeding Expectation 1',
            'EE2': 'Exceeding Expectation 2',
            'ME1': 'Meeting Expectation 1',
            'ME2': 'Meeting Expectation 2',
            'AE1': 'Approaching Expectation 1',
            'AE2': 'Approaching Expectation 2',
            'BE1': 'Below Expectation 1',
            'BE2': 'Below Expectation 2',
        }
        return mapping.get(str(code), str(code))

    # Build table data in builder order; render per assessment mode as before
    table_data = []
    visible_count = 0
    for subject_name in subject_names:
        mark_val = filtered_marks.get(subject_name, 0)
        if not mark_val or mark_val == 0:
            continue
        # Normalize display
        if isinstance(mark_val, float):
            mark_disp = int(round(mark_val)) if mark_val == int(mark_val) else round(mark_val, 1)
        else:
            mark_disp = int(mark_val)

        entrance_mark = mid_term_mark = end_term_mark = 0
        if assessment_type.lower() in ['end_term', 'endterm']:
            # For preview, we don't requery; show same value across columns and compute avg accordingly
            entrance_mark = mid_term_mark = end_term_mark = mark_disp
            avg_mark = mark_disp
        else:
            if assessment_type.lower() in ['entrance', 'opener']:
                entrance_mark = mark_disp
            elif assessment_type.lower() in ['mid_term', 'midterm']:
                mid_term_mark = mark_disp
            elif assessment_type.lower() in ['end_term', 'endterm']:
                end_term_mark = mark_disp
            avg_mark = mark_disp

        table_data.append({
            "subject": subject_name,
            "entrance": entrance_mark,
            "mid_term": mid_term_mark,
            "end_term": end_term_mark,
            "current_assessment": mark_disp,
            "avg": avg_mark,
            "remarks": _full_remarks(get_performance_remarks(avg_mark if assessment_type.lower() in ['end_term', 'endterm'] else mark_disp, class_data_result.get("total_marks", 100)))
        })
        visible_count += 1

    # Build composite component breakdown using builder component names and display labels
    composite_data = {}
    for main_name, info in composite_structure.items():
        comp_names = info.get('component_names', [])
        display_labels = info.get('component_display', [])
        components_map = {}
        for idx, comp_name in enumerate(comp_names):
            label = display_labels[idx] if idx < len(display_labels) else comp_name
            val = filtered_marks.get(comp_name, 0) or 0
            # Normalize
            if isinstance(val, float):
                val_disp = int(round(val)) if val == int(val) else round(val, 1)
            else:
                val_disp = int(val)
            components_map[label] = {
                'mark': val_disp,
                'max_mark': 100,
                'percentage': round(val, 1) if isinstance(val, (int, float)) else 0,
                'remarks': _full_remarks(get_performance_remarks(val, 100))
            }
        composite_data[main_name] = {
            'components': components_map,
            'total': filtered_marks.get(main_name, 0) or 0
        }

    # Totals based on visible subjects in preview
    total_marks = student_filtered.get('filtered_total', student_data.get('total_marks', 0)) if student_filtered else student_data.get('total_marks', 0)
    total_possible_marks = visible_count * class_data_result.get("total_marks", 100)
    total_points = mean_points * visible_count

    # Generate admission number if not available
    admission_no = student.admission_number if hasattr(student, 'admission_number') and student.admission_number else f"KPS{grade}{stream[-1]}{student.id}"

    # Get academic year
    academic_year = term_obj.academic_year if hasattr(term_obj, 'academic_year') and term_obj.academic_year else "2023"

    # Get current date for the report
    from datetime import datetime
    current_date = datetime.now().strftime("%Y-%m-%d")

    # Get school information for dynamic display
    from ..services.school_config_service import SchoolConfigService
    school_info = SchoolConfigService.get_school_info_dict()

    # Get dynamic logo URL from school setup
    logo_path = SchoolConfigService.get_school_logo_path()
    logo_url = url_for('static', filename=logo_path)

    # Get staff information for dynamic teacher names
    from ..services.staff_assignment_service import StaffAssignmentService
    # Extract stream letter (e.g., "Stream B" -> "B")
    stream_letter = stream.replace("Stream ", "") if stream.startswith("Stream ") else (stream[-1] if len(stream) > 1 else stream)
    staff_info = StaffAssignmentService.get_report_staff_info(grade, stream_letter)

    # Get subject teachers mapping for the teacher column
    subject_teachers = StaffAssignmentService.get_subject_teachers(grade, stream_letter)
    # Also map composite (logical) subjects to teachers by aggregating component subject teachers
    try:
        from ..models import Grade as GradeModel, TeacherSubjectAssignment
        from ..models.academic import Subject
        # Build once
        grade_obj = GradeModel.query.filter_by(name=grade).first()
        if grade_obj and stream_obj:
            # Determine candidate subject names that might be composites (appear in report subjects but not real Subject rows)
            candidate_names = subject_names  # list from builder
            for cname in candidate_names:
                if cname in subject_teachers:
                    continue
                # If no exact Subject exists, treat as composite name
                subj_obj = Subject.query.filter_by(name=cname).first()
                if subj_obj is None:
                    # Find component subjects bound to this composite name
                    component_subjects = Subject.query.filter_by(composite_parent=cname, is_component=True).all()
                    if component_subjects:
                        # Collect teacher names assigned to these components for this grade/stream
                        names = []
                        for cs in component_subjects:
                            assignments = TeacherSubjectAssignment.query.filter_by(
                                grade_id=grade_obj.id,
                                stream_id=stream_obj.id,
                                subject_id=cs.id
                            ).all()
                            for a in assignments:
                                if a.teacher:
                                    tname = getattr(a.teacher, 'full_name', None) or getattr(a.teacher, 'username', None) or ''
                                    if tname and tname not in names:
                                        names.append(tname)
                        if names:
                            label = ", ".join(names)
                            # Insert a dict compatible with template attribute access
                            subject_teachers[cname] = { 'full_name': label, 'username': label }
    except Exception as _e:
        # Non-fatal: continue without composite teacher aggregation
        pass

    # Get term information from report configuration
    from ..services.report_config_service import ReportConfigService
    report_config_data = ReportConfigService.get_comprehensive_report_data(grade, stream_letter, term)
    if report_config_data and report_config_data.get('term_info'):
        term_config = report_config_data['term_info']
        next_term_opening = term_config.get('opening_date')
        if next_term_opening:
            next_term_opening_str = next_term_opening.strftime('%B %d, %Y') if hasattr(next_term_opening, 'strftime') else str(next_term_opening)
        else:
            next_term_opening_str = 'TBD'
    else:
        next_term_opening_str = 'TBD'

    term_info = {
        'next_term_opening_date': next_term_opening_str,
        'current_term': term,
        'academic_year': academic_year
    }

    # Legends for grading/weights/status (non-invasive; templates may opt-in)
    try:
        from ..services.mark_calculator_adapter import build_legends  # local import to avoid cycles
        _calc_legends = build_legends()
    except Exception:
        _calc_legends = None

    return render_template(
        'preview_individual_report.html',
        student=student,
        student_data=student_data,
        grade=grade,
        stream=stream,
        term=term,
        assessment_type=assessment_type,
        education_level=education_level,
        current_date=current_date,
        table_data=table_data,
        composite_data=composite_data,  # Add composite subject data
        total=total_marks,
        avg_percentage=avg_percentage,
        mean_grade=mean_grade,
        mean_points=mean_points,
        total_possible_marks=total_possible_marks,
        total_points=total_points,
        admission_no=admission_no,
        academic_year=academic_year,
        print_mode=print_mode,
        school_info=school_info,  # Pass school information
        logo_url=logo_url,       # Pass dynamic logo URL
        staff_info=staff_info,   # Pass staff information
        term_info=term_info,     # Pass term information
        subject_teachers=subject_teachers,  # Pass subject teachers mapping
        # Optional legends for templates
        calculator_legends=_calc_legends
    )

@classteacher_bp.route('/api/check_stream_status/<grade>/<term>/<assessment_type>', methods=['GET'])
@classteacher_required
def check_stream_status(grade, term, assessment_type):
    """API route to check if all streams in a grade have marks for a term and assessment type."""
    try:
        # Get the grade object
        grade_obj = Grade.query.filter_by(name=grade).first()

        if not grade_obj:
            return jsonify({"success": False, "message": f"Grade {grade} not found", "streams": []})

        # Get the term and assessment type objects
        term_obj, term_name = _resolve_term_object(term)
        assessment_type_obj, at_name = _resolve_assessment_type_object(assessment_type)

        if not term_obj or not assessment_type_obj:
            return jsonify({"success": False, "message": "Invalid term or assessment type", "streams": []})

        # Get streams for this grade
        streams = Stream.query.filter_by(grade_id=grade_obj.id).all()

        # Check if each stream has marks for this term and assessment type
        streams_data = []
        for stream in streams:
            # Get students in this stream
            students = Student.query.filter_by(stream_id=stream.id).all()

            # Check if there are marks for any student in this stream
            has_report = False
            if students:
                for student in students:
                    mark = Mark.query.filter_by(
                        student_id=student.id,
                        term_id=term_obj.id,
                        assessment_type_id=assessment_type_obj.id
                    ).first()

                    if mark:
                        has_report = True
                        break

            streams_data.append({
                "id": stream.id,
                "name": stream.name,
                "has_report": has_report
            })

        return jsonify({"success": True, "streams": streams_data})
    except Exception as e:
        print(f"Error checking stream status: {str(e)}")
        return jsonify({"success": False, "message": str(e), "streams": []})

@classteacher_bp.route('/get_streams_by_level/<grade>', methods=['GET'])
@classteacher_required
def get_streams_by_level(grade):
    """API route to get streams for a grade by grade level."""
    try:
        # Get current teacher info for debugging
        teacher_id = session.get('teacher_id')
        current_role = get_role(session)
        print(f"=== STREAM FETCH DEBUG ===")
        print(f"Teacher ID: {teacher_id}, Role: {current_role}")
        print(f"Requested grade: {grade}")

        # Handle both "Grade X" format and just "X" format
        if grade.isdigit():
            grade_name = f"Grade {grade}"
        else:
            grade_name = grade

        print(f"Looking for grade: {grade_name}")

        # Get the grade object by name
        grade_obj = Grade.query.filter_by(name=grade_name).first()

        if not grade_obj:
            print(f"Grade {grade_name} not found in database")
            # Try alternative formats
            if grade_name.startswith("Grade "):
                alt_grade = grade_name.replace("Grade ", "")
                grade_obj = Grade.query.filter_by(name=alt_grade).first()
                print(f"Trying alternative format: {alt_grade}")

            if not grade_obj:
                # List all available grades for debugging
                all_grades = Grade.query.all()
                available_grades = [g.name for g in all_grades]
                print(f"Available grades in database: {available_grades}")
                return jsonify({"success": False, "message": f"Grade {grade} not found. Available: {available_grades}", "streams": []})

        print(f"Found grade: {grade_obj.name} (ID: {grade_obj.id})")

        # Get streams for this grade
        streams = Stream.query.filter_by(grade_id=grade_obj.id).all()
        print(f"Found {len(streams)} streams for grade {grade_obj.name}")

        # Debug: List all streams
        for stream in streams:
            print(f"  Stream: {stream.name} (ID: {stream.id})")

        # Check if teacher has access to these streams (for role-based filtering)
        accessible_streams = streams  # Default: all streams

        if current_role in ['teacher', 'classteacher']:
            # Check teacher assignments
            teacher_assignments = TeacherSubjectAssignment.query.filter_by(teacher_id=teacher_id).all()
            print(f"Teacher has {len(teacher_assignments)} assignments")

            # Get assigned stream IDs
            assigned_stream_ids = []
            for assignment in teacher_assignments:
                if assignment.stream_id:
                    assigned_stream_ids.append(assignment.stream_id)
                    print(f"  Assigned to stream ID: {assignment.stream_id}")
                else:
                    print(f"  Assigned to all streams in grade {assignment.grade_id}")

            # If teacher has specific stream assignments, filter streams
            if assigned_stream_ids:
                accessible_streams = [s for s in streams if s.id in assigned_stream_ids]
                print(f"Filtered to {len(accessible_streams)} accessible streams")
            else:
                # If no specific stream assignments, check if teacher has any assignments for this grade
                grade_assignments = [a for a in teacher_assignments if a.grade_id == grade_obj.id]
                if grade_assignments:
                    print(f"Teacher has {len(grade_assignments)} assignments for this grade - allowing all streams")
                    accessible_streams = streams
                else:
                    print(f"Teacher has no assignments for grade {grade_obj.name}")
                    # For now, still allow all streams - we can restrict this later if needed
                    accessible_streams = streams

        # Format streams for JSON response
        streams_data = [{"id": stream.id, "name": stream.name} for stream in accessible_streams]
        print(f"Returning {len(streams_data)} streams: {[s['name'] for s in streams_data]}")

        return jsonify({"success": True, "streams": streams_data})
    except Exception as e:
        print(f"Error fetching streams: {str(e)}")
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e), "streams": []})

@classteacher_bp.route('/get_subjects_by_education_level/<education_level>', methods=['GET'])
@classteacher_required
def get_subjects_by_education_level(education_level):
    """API route to get subjects for a specific education level."""
    try:
        # Get subjects for this education level
        all_subjects = Subject.query.filter_by(education_level=education_level).all()

        if not all_subjects:
            return jsonify({"success": True, "message": f"No subjects found for {education_level}", "subjects": []})

        # Define core subjects that should appear first
        core_subjects = ["Mathematics", "English", "Kiswahili", "Science", "Integrated Science",
                        "Science and Technology", "Integrated Science and Health Education"]

        # Sort subjects with core subjects first
        sorted_subjects = []

        # First add core subjects in the specified order
        for core_subject in core_subjects:
            for subject in all_subjects:
                if subject.name == core_subject or subject.name.upper() == core_subject.upper():
                    sorted_subjects.append(subject)

        # Then add remaining subjects alphabetically
        remaining_subjects = [s for s in all_subjects if s not in sorted_subjects]
        remaining_subjects.sort(key=lambda x: x.name)
        sorted_subjects.extend(remaining_subjects)

        # Convert to list of dictionaries
        subjects_data = [{"id": subject.id, "name": subject.name} for subject in sorted_subjects]

        return jsonify({"success": True, "subjects": subjects_data})
    except Exception as e:
        print(f"Error fetching subjects: {str(e)}")
        return jsonify({"success": False, "message": str(e), "subjects": []})

@classteacher_bp.route('/download_marks_template', methods=['GET'])
@classteacher_required
def download_marks_template():
    """Route to download the marks upload template."""
    try:
        # Get parameters from the request
        education_level = request.args.get('education_level', '')
        grade_level = request.args.get('grade', '')
        stream_name = request.args.get('stream', '')
        term = request.args.get('term', '')
        assessment_type = request.args.get('assessment_type', '')
        file_format = request.args.get('format', 'xlsx')

        print(f"=== TEMPLATE DOWNLOAD REQUEST ===")
        print(f"Education Level: {education_level}")
        print(f"Grade: {grade_level}")
        print(f"Stream: {stream_name}")
        print(f"Term: {term}")
        print(f"Assessment Type: {assessment_type}")

        # If specific parameters are provided, generate a customized template
        if education_level and grade_level and stream_name:
            # Extract stream letter from "Stream X" format
            stream_letter = stream_name.replace("Stream ", "") if stream_name.startswith("Stream ") else stream_name[-1]

            # Get the stream object
            stream_obj = Stream.query.join(Grade).filter(Grade.name == grade_level, Stream.name == stream_letter).first()

            if stream_obj:
                # Get students for this stream
                students = Student.query.filter_by(stream_id=stream_obj.id).order_by(Student.name).all()

                # Get subjects for this education level
                subjects = [subject.name for subject in Subject.query.filter_by(education_level=education_level).all()]

                print(f"Found {len(students)} students and {len(subjects)} subjects")

                if students and subjects:
                    try:
                        # Import the template generator from utils
                        from ..utils.marks_upload_template import create_marks_template

                        # Default total marks value
                        default_total_marks = 100

                        # Create a custom template
                        template_data = create_marks_template(
                            students=students,
                            subjects=subjects,
                            grade=grade_level,
                            stream=stream_name,
                            term=term,
                            assessment_type=assessment_type,
                            total_marks=default_total_marks
                        )

                        print(f"Template created successfully, size: {len(template_data.getvalue())} bytes")

                        # Create the filename
                        filename = f"marks_template_{grade_level}_{stream_letter}_{term}_{assessment_type}.xlsx".replace(" ", "_")

                        # Create response with proper headers
                        response = make_response(send_file(
                            template_data,
                            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                            as_attachment=True,
                            download_name=filename
                        ))
                        
                        # Add additional headers to ensure download
                        response.headers['Content-Disposition'] = f'attachment; filename={filename}'
                        response.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
                        response.headers['Pragma'] = 'no-cache'
                        response.headers['Expires'] = '0'
                        
                        print(f"Sending response with filename: {filename}")
                        return response
                        
                    except Exception as e:
                        print(f"Error creating custom template: {str(e)}")
                        traceback.print_exc()
                        flash(f"Error creating template: {str(e)}", "error")
                        return redirect(url_for('classteacher.dashboard'))
                else:
                    print("No students or subjects found for the specified parameters")

        # If no specific parameters or an error occurred, return a generic template
        print("Creating generic template...")
        try:
            from ..utils.marks_upload_template import create_simple_marks_template
            
            template_data = create_simple_marks_template(
                grade=grade_level,
                stream=stream_name,
                term=term,
                assessment_type=assessment_type
            )
            
            print(f"Generic template created, size: {len(template_data.getvalue())} bytes")
            
            # Create filename for generic template
            filename = f"marks_template_{grade_level or 'generic'}_{term or 'generic'}.xlsx".replace(" ", "_")
            
            # Create response with proper headers
            response = make_response(send_file(
                template_data,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                as_attachment=True,
                download_name=filename
            ))
            
            # Add headers to ensure download
            response.headers['Content-Disposition'] = f'attachment; filename={filename}'
            response.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
            
            print(f"Sending generic template response with filename: {filename}")
            return response
            
        except Exception as e:
            print(f"Error creating generic template: {str(e)}")
            traceback.print_exc()
            flash(f"Error creating generic template: {str(e)}", "error")
            return redirect(url_for('classteacher.dashboard'))
            
    except Exception as e:
        print(f"General error in download_marks_template: {str(e)}")
        traceback.print_exc()
        flash(f"Error processing template request: {str(e)}", "error")
        return redirect(url_for('classteacher.dashboard'))


@classteacher_bp.route('/generate_grade_marksheet/<grade>/<term>/<assessment_type>/<action>')
@classteacher_required
def generate_grade_marksheet(grade, term, assessment_type, action):
    """Route for generating grade marksheets (preview or download)."""
    # Check if we have a cached version
    cached_marksheet = get_cached_marksheet(grade, "all", term, assessment_type)
    if cached_marksheet and action == 'preview':
        # Use the cached marksheet data
        return render_template(
            'preview_grade_marksheet.html',
            grade=grade,
            term=term,
            assessment_type=assessment_type,
            subjects=cached_marksheet['subjects'],
            data=cached_marksheet['data'],
            statistics=cached_marksheet['statistics']
        )
    elif cached_marksheet and action == 'download':
        # Check if we have a cached PDF
        cached_pdf = get_cached_pdf(grade, "all", term, assessment_type, "marksheet")
        if cached_pdf:
            return render_template(
                cached_pdf,
                as_attachment=True,
                download_name=f"{grade}_{term}_{assessment_type}_Grade_Marksheet.xlsx",
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )

    # If no cache or cache miss, generate the marksheet
    # Fetch grade and related data
    grade_obj = Grade.query.filter_by(name=grade).first()
    if not grade_obj:
        flash(f"Grade {grade} not found", "error")
        return redirect(url_for('classteacher.dashboard'))

    term_obj = Term.query.filter_by(name=term).first()
    assessment_type_obj = AssessmentType.query.filter_by(name=assessment_type).first()
    if not term_obj or not assessment_type_obj:
        flash("Invalid term or assessment type", "error")
        return redirect(url_for('classteacher.dashboard'))

    streams = Stream.query.filter_by(grade_id=grade_obj.id).all()
    if not streams:
        flash(f"No streams found for grade {grade}", "error")
        return redirect(url_for('classteacher.dashboard'))

    # Fetch all subjects
    subjects = Subject.query.all()
    if not subjects:
        flash("No subjects found", "error")
        return redirect(url_for('classteacher.dashboard'))

    # Prepare data for the marksheet
    all_data = []
    for stream in streams:
        students = Student.query.filter_by(stream_id=stream.id).all()
        for student in students:
            student_marks = {}
            student_total = 0
            subject_count = 0

            # Get marks for each subject
            student_standardized_total = 0
            for subject in subjects:
                mark = Mark.query.filter_by(
                    student_id=student.id,
                    subject_id=subject.id,
                    term_id=term_obj.id,
                    assessment_type_id=assessment_type_obj.id
                ).first()

                if mark:
                    # Store the raw mark
                    student_marks[subject.name] = mark.mark

                    # Calculate standardized mark (out of 100)
                    total_marks = mark.total_marks if mark.total_marks > 0 else 100
                    standardized_mark = (mark.mark / total_marks) * 100

                    # Add to standardized total for average calculation
                    student_standardized_total += standardized_mark
                    subject_count += 1
                else:
                    student_marks[subject.name] = "-"

            # Calculate average percentage (already standardized to 100)
            average_percentage = student_standardized_total / subject_count if subject_count > 0 else 0
            grade_label = get_performance_category(average_percentage)

            # Include stream name with student name for clarity
            student_data = {
                'name': f"{student.name} ({stream.name})",
                'marks': student_marks,
                'total': student_total,
                'percentage': average_percentage,
                'grade': grade_label
            }
            all_data.append(student_data)

    if not all_data:
        flash(f"No marks found for grade {grade}, term {term}, assessment {assessment_type}", "error")
        return redirect(url_for('classteacher.dashboard'))

    # Sort data by percentage (descending)
    all_data.sort(key=lambda x: x['percentage'], reverse=True)

    # Calculate statistics for the marksheet
    stream_data = {}
    subject_totals = {subject.name: 0 for subject in subjects}
    subject_counts = {subject.name: 0 for subject in subjects}
    overall_total = 0
    overall_count = 0

    # Collect statistics for each stream
    for stream in streams:
        stream_students = [s for s in all_data if s['name'].endswith(f"({stream.name})")]
        stream_total = sum(s['total'] for s in stream_students)
        stream_count = len(stream_students) * len(subjects)  # Total possible marks

        # Calculate stream average
        stream_average = stream_total / stream_count if stream_count > 0 else 0

        # Calculate subject averages for this stream
        stream_subject_totals = {subject.name: 0 for subject in subjects}
        stream_subject_counts = {subject.name: 0 for subject in subjects}

        for student in stream_students:
            for subject in subjects:
                mark = student['marks'].get(subject.name)
                if mark != "-":
                    stream_subject_totals[subject.name] += mark
                    stream_subject_counts[subject.name] += 1

                    # Update overall subject totals
                    subject_totals[subject.name] += mark
                    subject_counts[subject.name] += 1

                    # Update overall totals
                    overall_total += mark
                    overall_count += 1

        # Calculate stream subject averages
        stream_subject_averages = {}
        for subject_name in stream_subject_totals:
            if stream_subject_counts[subject_name] > 0:
                stream_subject_averages[subject_name] = round(stream_subject_totals[subject_name] / stream_subject_counts[subject_name], 1)
            else:
                stream_subject_averages[subject_name] = 0

        # Store stream statistics
        stream_data[stream.name] = {
            'total': stream_total,
            'count': len(stream_students),
            'average': round(stream_average, 1),
            'subject_averages': stream_subject_averages
        }

    # Calculate overall subject averages
    subject_averages = {}
    for subject_name in subject_totals:
        if subject_counts[subject_name] > 0:
            subject_averages[subject_name] = round(subject_totals[subject_name] / subject_counts[subject_name], 1)
        else:
            subject_averages[subject_name] = 0

    # Calculate overall average
    overall_average = overall_total / overall_count if overall_count > 0 else 0

    # Calculate performance statistics
    performance_counts = {}
    for category in ['Excellent', 'Very Good', 'Good', 'Average', 'Below Average', 'Poor']:
        performance_counts[category] = len([d for d in all_data if d['grade'] == category])

    # Calculate gender statistics
    gender_counts = {
        'male': 0,
        'female': 0,
        'other': 0
    }

    # Add rank to each student
    for i, student_data in enumerate(all_data):
        student_data['rank'] = i + 1

    # Prepare statistics for the template
    statistics = {
        'total_students': len(all_data),
        'overall_average': round(overall_average, 1),
        'subject_averages': subject_averages,
        'stream_data': stream_data,
        'performance_counts': performance_counts,
        'gender_counts': gender_counts
    }

    # Cache the marksheet data
    marksheet_data = {
        'subjects': subjects,
        'data': all_data,
        'statistics': statistics
    }
    cache_marksheet(grade, "all", term, assessment_type, marksheet_data)

    # Handle preview or download action
    if action == 'preview':
        return render_template(
            'preview_grade_marksheet.html',
            grade=grade,
            term=term,
            assessment_type=assessment_type,
            subjects=subjects,
            data=all_data,
            statistics=statistics
        )
    elif action == 'download':
        try:
            # Import the Excel export service
            from ..services.excel_export import generate_grade_marksheet_excel

            # Generate Excel file
            excel_file = generate_grade_marksheet_excel(
                grade=grade,
                term=term,
                assessment_type=assessment_type,
                subjects=subjects,
                data=all_data,
                statistics=statistics
            )

            # Cache the Excel file
            cached_file = cache_pdf(grade, "all", term, assessment_type, "marksheet", open(excel_file, 'rb').read())

            # Return the Excel file
            return send_file(
                excel_file,
                as_attachment=True,
                download_name=f"{grade}_{term}_{assessment_type}_Grade_Marksheet.xlsx",
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
        except Exception as e:
            flash(f"Error generating Excel file: {str(e)}", "error")
            return redirect(url_for('classteacher.dashboard'))
    else:
        flash("Invalid action", "error")
        return redirect(url_for('classteacher.dashboard'))

@classteacher_bp.route('/preview_grade_marksheet/<grade>/<term>/<assessment_type>')
@classteacher_required
def preview_grade_marksheet(grade, term, assessment_type):
    """Shortcut route for previewing grade marksheets."""
    return redirect(url_for('classteacher.generate_grade_marksheet',
                           grade=grade,
                           term=term,
                           assessment_type=assessment_type,
                           action='preview'))

@classteacher_bp.route('/download_grade_marksheet/<grade>/<term>/<assessment_type>')
@classteacher_required
def download_grade_marksheet(grade, term, assessment_type):
    """Shortcut route for downloading grade marksheets."""
    return redirect(url_for('classteacher.generate_grade_marksheet',
                           grade=grade,
                           term=term,
                           assessment_type=assessment_type,
                           action='download'))

@classteacher_bp.route('/delete_marksheet/<grade>/<stream>/<term>/<assessment_type>', methods=['POST'])
@enforce('marksheet', 'delete', class_scope=True, grade_arg='grade', stream_arg='stream', roles=(
    'headteacher', 'admin'
))
def delete_marksheet(grade, stream, term, assessment_type):
    """Route for deleting a class marksheet (all marks for a grade/stream/term/assessment combination)."""
    try:
        # Get the stream object
        stream_letter = stream.split()[-1] if " " in stream else stream[-1]
        stream_obj = (
            Stream.query.join(Grade)
            .filter(Grade.name == grade, Stream.name == stream_letter)
            .first()
        )

        if not stream_obj:
            flash(f"Stream {stream} not found for grade {grade}", "error")
            return redirect(url_for('classteacher.dashboard'))

        # Get the term and assessment type objects (flexible resolution)
        term_obj, term_name = _resolve_term_object(term)
        assessment_type_obj, at_name = _resolve_assessment_type_object(assessment_type)

        if not (term_obj and assessment_type_obj):
            flash("Invalid term or assessment type", "error")
            return redirect(url_for('classteacher.dashboard'))

        # Get all students in this stream
        students = Student.query.filter_by(stream_id=stream_obj.id).all()
        student_ids = [student.id for student in students]

        if not student_ids:
            flash(f"No students found in {grade} Stream {stream_letter}", "error")
            return redirect(url_for('classteacher.dashboard'))

        # Determine education level based on grade
        grade_num = int(grade.split()[1]) if len(grade.split()) > 1 else int(grade)
        if 1 <= grade_num <= 3:
            education_level_code = "lower_primary"
        elif 4 <= grade_num <= 6:
            education_level_code = "upper_primary"
        elif 7 <= grade_num <= 9:
            education_level_code = "junior_secondary"
        else:
            education_level_code = ""

        # Get subjects for this education level
        if education_level_code:
            subjects = Subject.query.filter_by(education_level=education_level_code).all()
        else:
            subjects = Subject.query.all()

        subject_ids = [subject.id for subject in subjects]

        # Get marks to delete first (to handle component marks)
        marks_to_delete = (
            Mark.query.filter(
                Mark.student_id.in_(student_ids),
                Mark.subject_id.in_(subject_ids),
                Mark.term_id == term_obj.id,
                Mark.assessment_type_id == assessment_type_obj.id,
            ).all()
        )

        deleted_count = len(marks_to_delete)

        # Delete component marks first, then marks
        for mark in marks_to_delete:
            ComponentMark.query.filter_by(mark_id=mark.id).delete()
            db.session.delete(mark)

        # Commit the changes
        db.session.commit()

        # Invalidate the cache for this grade/stream/term/assessment combination
        invalidate_cache(grade, stream, term, assessment_type)

        # Create a detailed success message
        if deleted_count > 0:
            success_message = (
                f"Successfully deleted {deleted_count} marks for {grade} Stream {stream_letter} in {term} {assessment_type}. "
                f"The marksheet has been completely removed."
            )
            session['marksheet_deleted'] = True
            session['deleted_marksheet_info'] = {
                'grade': grade,
                'stream': stream,
                'term': term,
                'assessment_type': assessment_type,
                'count': deleted_count,
            }
            flash(success_message, "success")
        else:
            flash(
                f"No marks were found to delete for {grade} Stream {stream_letter} in {term} {assessment_type}.",
                "info",
            )

        return redirect(url_for('classteacher.dashboard'))

    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting marksheet: {str(e)}", "error")
        return redirect(url_for('classteacher.dashboard'))

@classteacher_bp.route('/get_streams/<grade_id>', methods=['GET'])
@classteacher_required
def get_streams(grade_id):
    """API endpoint to get streams for a specific grade."""
    try:
        grade_id = int(grade_id)
        # Return only streams for this grade, sorted by name
        streams = Stream.query.filter_by(grade_id=grade_id).order_by(Stream.name.asc()).all()
        # Include grade_id for client-side validation if needed
        return jsonify({
            "streams": [
                {"id": stream.id, "name": stream.name, "grade_id": stream.grade_id}
                for stream in streams
            ]
        })
    except ValueError:
        return jsonify({"error": "Invalid grade ID"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@classteacher_bp.route('/download_student_template', methods=['GET'])
@classteacher_required
def download_student_template():
    """Route to download the student upload template with enhanced options."""
    file_format = request.args.get('format', 'xlsx')
    template_type = request.args.get('type', 'full')  # full, minimal, headers_only

    # Determine template filename based on type and format
    if template_type == 'minimal':
        base_filename = 'student_upload_template_minimal'
    elif template_type == 'headers_only' and file_format == 'csv':
        base_filename = 'student_upload_template_headers_only'
    else:
        base_filename = 'student_upload_template'

    if file_format == 'csv':
        template_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'templates', f'{base_filename}.csv')
        mimetype = 'text/csv'
        filename = f'{base_filename}.csv'
    else:
        template_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'templates', f'{base_filename}.xlsx')
        mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        filename = f'{base_filename}.xlsx'

    # Check if the template file exists
    if not os.path.exists(template_path):
        # If not, create it
        try:
            from ..static.templates.student_upload_template import create_student_upload_template, create_minimal_student_template

            if template_type == 'minimal':
                create_minimal_student_template()
            else:
                create_student_upload_template()

            # Check again if file was created
            if not os.path.exists(template_path):
                flash("Template file could not be created. Please try again.", "error")
                return redirect(url_for('classteacher.manage_students'))

        except Exception as e:
            print(f"Error creating template: {str(e)}")
            flash("Error creating template file. Please try again.", "error")
            return redirect(url_for('classteacher.manage_students'))

    # Set appropriate download filename with timestamp
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d")
    download_filename = f"hillview_student_template_{template_type}_{timestamp}.{file_format}"

    return send_file(
        template_path,
        as_attachment=True,
        download_name=download_filename,
        mimetype=mimetype
    )

@classteacher_bp.route('/view_student_reports/<grade>/<stream>/<term>/<assessment_type>')
@classteacher_required
def view_student_reports(grade, stream, term, assessment_type):
    """Route for viewing a list of students with options to view their individual reports."""
    # Get the stream object
    stream_obj = Stream.query.join(Grade).filter(Grade.name == grade, Stream.name == stream[-1]).first()
    term_obj, _ = _resolve_term_object(term)
    assessment_type_obj, _ = _resolve_assessment_type_object(assessment_type)

    if not (stream_obj and term_obj and assessment_type_obj):
        flash("Invalid grade, stream, term, or assessment type", "error")
        return redirect(url_for('classteacher.dashboard'))

    # Get pagination parameters
    page = request.args.get('page', 1, type=int)
    per_page = 20  # Number of students per page
    search_query = request.args.get('search', '')

    # Get students in this stream with optional search filter
    query = Student.query.filter_by(stream_id=stream_obj.id)
    if search_query:
        query = query.filter(Student.name.ilike(f'%{search_query}%'))

    # Get paginated students
    students_pagination = query.order_by(Student.name).paginate(page=page, per_page=per_page, error_out=False)
    students = students_pagination.items

    # Get marks for each student to calculate averages
    for student in students:
        marks = Mark.query.filter_by(
            student_id=student.id,
            term_id=term_obj.id,
            assessment_type_id=assessment_type_obj.id
        ).all()

        if marks:
            total_marks = sum(mark.mark for mark in marks)
            total_possible = len(marks) * marks[0].total_marks if marks[0].total_marks else 100
            student.average = (total_marks / total_possible) * 100 if total_possible > 0 else 0
        else:
            student.average = None

    return render_template(
        'view_student_reports.html',
        grade=grade,
        stream=stream,
        term=term,
        assessment_type=assessment_type,
        students=students,
        page=page,
        per_page=per_page,
        total_pages=students_pagination.pages,
        search_query=search_query
    )

@classteacher_bp.route('/download_individual_report/<grade>/<stream>/<term>/<assessment_type>/<student_name>')
@enforce('individual_report', 'read', class_scope=True, grade_arg='grade', stream_arg='stream')
def download_individual_report(grade, stream, term, assessment_type, student_name):
    """Route for downloading an individual student report as PDF - uses unified generator."""
    print(f"\n🔍 DOWNLOAD INDIVIDUAL REPORT CALLED:")
    print(f"   Student: {student_name}")
    print(f"   Grade: {grade}, Stream: {stream}, Term: {term}, Assessment: {assessment_type}")
    print(f"   Session selected_subjects: {session.get('selected_subjects', [])}")
    
    # TEMPORARILY DISABLE CACHE to ensure fresh generation with fixes
    # Check if we have a cached PDF for this student
    cached_pdf = None  # Disable cache temporarily for debugging
    # cached_pdf = get_cached_pdf(grade, stream, term, assessment_type, f"student_{student_name.replace(' ', '_')}")
    if cached_pdf:
        # Check if cached_pdf is a file path (string) or bytes
        if isinstance(cached_pdf, str):
            # It's a file path - read the file
            return send_file(
                cached_pdf,
                as_attachment=True,
                download_name=f"Individual_Report_{grade}_{stream}_{student_name.replace(' ', '_')}.pdf",
                mimetype='application/pdf'
            )
        else:
            # It's bytes - wrap in BytesIO
            return send_file(
                BytesIO(cached_pdf),
                as_attachment=True,
                download_name=f"Individual_Report_{grade}_{stream}_{student_name.replace(' ', '_')}.pdf",
                mimetype='application/pdf'
            )

    # Resolve stream, term, and assessment type
    stream_obj = Stream.query.join(Grade).filter(Grade.name == grade, Stream.name == stream[-1]).first()
    term_obj, term_name = _resolve_term_object(term)
    assessment_type_obj, at_name = _resolve_assessment_type_object(assessment_type)

    if not (stream_obj and term_obj and assessment_type_obj):
        flash("Invalid grade, stream, term, or assessment type", "error")
        return redirect(url_for('classteacher.dashboard'))

    student = Student.query.filter_by(name=student_name, stream_id=stream_obj.id).first()
    if not student:
        flash(f"No data available for student {student_name}.", "error")
        return redirect(url_for('classteacher.dashboard'))

    print(f"   Calling generate_student_report_pdf_bytes with:")
    print(f"   - Original term: {term}, normalized: {term_name}")
    print(f"   - Original assessment: {assessment_type}, normalized: {at_name}")
    print(f"   - Will pass to builder: term={term}, assessment={assessment_type} (as-is, same as preview)")
    
    # 🔥 UNIFIED PDF GENERATION - Returns (pdf_bytes, filename) or (None, error_message)
    # IMPORTANT: Pass term/assessment_type as-is (not normalized) to match preview behavior
    result = generate_student_report_pdf_bytes(
        student, grade, stream, term, assessment_type,  # Pass original values, same as preview
        stream_obj, term_obj, assessment_type_obj
    )
    
    print(f"   PDF Generation result: {'SUCCESS' if result[0] is not None else 'FAILED'}")

    if result[0] is None:
        flash(f"Failed to generate report: {result[1]}", "error")
        return redirect(url_for('classteacher.view_student_reports', grade=grade, stream=stream, term=term, assessment_type=assessment_type))

    pdf_bytes, filename = result

    # Cache the PDF bytes
    cache_pdf(grade, stream, term, assessment_type, f"student_{student_name.replace(' ', '_')}", pdf_bytes)

    # Return the PDF file from bytes
    return send_file(
        BytesIO(pdf_bytes),
        as_attachment=True,
        download_name=filename,
        mimetype='application/pdf'
    )

def generate_student_report_pdf_bytes(student, grade, stream, term, assessment_type, stream_obj, term_obj, assessment_type_obj):
    """
    UNIFIED PDF GENERATOR - Single Source of Truth
    Generate individual report PDF bytes using standardized configuration.
    Called by both single download and batch ZIP routes to guarantee format parity.
    
    Returns: (pdf_bytes, filename) or (None, error_message)
    """
    try:
        import tempfile
        import os
        from datetime import datetime
        from flask import render_template_string, session
        import pdfkit

        # Get education level based on grade
        education_level = ""
        grade_num = int(grade.split()[1]) if len(grade.split()) > 1 else int(grade)
        if 1 <= grade_num <= 3:
            education_level = "lower primary"
        elif 4 <= grade_num <= 6:
            education_level = "upper primary"
        elif 7 <= grade_num <= 9:
            education_level = "junior secondary"

        # USE ClassReportBuilder ONLY - same as preview (SINGLE SOURCE OF TRUTH)
        # Pass parameters EXACTLY as preview does (no normalization - builder handles it)
        from ..services.class_report_builder import ClassReportBuilder
        
        # Get selected subjects from session (same as preview)
        selected_subjects = session.get('selected_subjects', [])
        
        # Use EXACT same parameters as preview - pass term/assessment_type as-is
        builder_ctx = ClassReportBuilder.build(
            grade, stream, term, assessment_type,  # Pass as-is, same as preview
            selected_subject_ids=selected_subjects,
            invalidate=True  # Match preview behavior - ensure fresh calculation
        )
        
        if builder_ctx.get("error"):
            return None, builder_ctx.get("error", "Failed to build report")
        
        # Extract student data from builder context (SAME AS PREVIEW)
        class_data_list = builder_ctx.get('class_data', []) or []
        print(f"📊 Class data has {len(class_data_list)} students")
        
        student_filtered = None
        for idx, sd in enumerate(class_data_list):
            if sd.get('student') == student.name:
                student_filtered = sd
                print(f"✅ Found student {student.name} at index {idx}")
                print(f"   Student rank: {sd.get('rank')}, index: {sd.get('index')}")
                print(f"   Student filtered_total: {sd.get('filtered_total')}")
                break
        
        if not student_filtered:
            # Debug: Print all student names to see what's available
            print(f"❌ Student {student.name} not found. Available students:")
            for sd in class_data_list[:5]:  # Print first 5
                print(f"   - {sd.get('student')} (rank: {sd.get('rank')}, total: {sd.get('filtered_total')})")
            return None, f"Student {student.name} not found in class report"
        
        # Use filtered data from builder (SAME AS PREVIEW)
        filtered_marks = student_filtered.get('filtered_marks', {})
        filtered_total = student_filtered.get('filtered_total', 0)
        filtered_average = student_filtered.get('filtered_average', 0)
        
        # Calculate mean grade and points using filtered_average (SAME AS PREVIEW)
        from ..utils import get_grade_and_points, get_performance_remarks
        mean_grade, mean_points = get_grade_and_points(filtered_average)
        
        # Extract other data from builder context
        composite_structure = builder_ctx.get('composite_structure', {}) or {}
        subject_names = builder_ctx.get('subject_names', [])
        report_data = builder_ctx.get('report_data', {})
        
        # Get total_marks per subject from report_data
        total_marks_per_subject = report_data.get("total_marks", 100) or 100

        # Local helper to expand remarks codes to full descriptive text (SAME AS PREVIEW)
        def _full_remarks(code: str) -> str:
            mapping = {
                'EE1': 'Exceeding Expectation 1',
                'EE2': 'Exceeding Expectation 2',
                'ME1': 'Meeting Expectation 1',
                'ME2': 'Meeting Expectation 2',
                'AE1': 'Approaching Expectation 1',
                'AE2': 'Approaching Expectation 2',
                'BE1': 'Below Expectation 1',
                'BE2': 'Below Expectation 2',
            }
            return mapping.get(str(code), str(code))

        # Build table data in builder order - CONSISTENT FOR ALL STUDENTS
        # Include ALL subjects from subject_names to ensure same layout for all students
        table_data = []
        visible_count = 0
        for subject_name in subject_names:
            mark_val = filtered_marks.get(subject_name, 0) or 0
            
            # Normalize display (even if 0, we still show the row for consistency)
            if isinstance(mark_val, float):
                mark_disp = int(round(mark_val)) if mark_val == int(mark_val) else round(mark_val, 1)
            else:
                mark_disp = int(mark_val) if mark_val else 0

            entrance_mark = mid_term_mark = end_term_mark = 0
            if assessment_type.lower() in ['end_term', 'endterm']:
                # For PDF, we don't requery; show same value across columns and compute avg accordingly
                entrance_mark = mid_term_mark = end_term_mark = mark_disp
                avg_mark = mark_disp
            else:
                if assessment_type.lower() in ['entrance', 'opener']:
                    entrance_mark = mark_disp
                elif assessment_type.lower() in ['mid_term', 'midterm']:
                    mid_term_mark = mark_disp
                elif assessment_type.lower() in ['end_term', 'endterm']:
                    end_term_mark = mark_disp
                avg_mark = mark_disp

            # ALWAYS add subject to table_data for consistent layout (even if mark is 0)
            table_data.append({
                "subject": subject_name,
                "entrance": entrance_mark,
                "mid_term": mid_term_mark,
                "end_term": end_term_mark,
                "current_assessment": mark_disp,
                "avg": avg_mark,
                "remarks": _full_remarks(get_performance_remarks(avg_mark if assessment_type.lower() in ['end_term', 'endterm'] else mark_disp, total_marks_per_subject)) if mark_disp > 0 else '-'
            })
            if mark_disp > 0:
                visible_count += 1

        # Build composite component breakdown using CLASS-LEVEL composite structure (CONSISTENT FOR ALL STUDENTS)
        # This ensures all students show the same composite structure, even if they don't have marks
        composite_data = {}
        for main_name, info in composite_structure.items():
            comp_names = info.get('component_names', [])
            display_labels = info.get('component_display', [])
            components_map = {}
            for idx, comp_name in enumerate(comp_names):
                label = display_labels[idx] if idx < len(display_labels) else comp_name
                # Get component mark for THIS student (may be 0)
                val = filtered_marks.get(comp_name, 0) or 0
                # Normalize
                if isinstance(val, float):
                    val_disp = int(round(val)) if val == int(val) else round(val, 1)
                else:
                    val_disp = int(val) if val else 0
                components_map[label] = {
                    'mark': val_disp,
                    'max_mark': 100,
                    'percentage': round(val, 1) if isinstance(val, (int, float)) and val > 0 else 0,
                    'remarks': _full_remarks(get_performance_remarks(val, 100)) if val > 0 else '-'
                }
            composite_data[main_name] = {
                'components': components_map,
                'total': filtered_marks.get(main_name, 0) or 0
            }

        # Use filtered_total from builder (SAME AS PREVIEW) - this ensures exact match with class report
        # DEBUG: Verify we're using the exact same data as preview
        print(f"🔍 PDF Generation Debug for {student.name}:")
        print(f"   filtered_total from builder: {filtered_total}")
        print(f"   filtered_average from builder: {filtered_average}")
        print(f"   filtered_marks keys: {list(filtered_marks.keys())}")
        print(f"   filtered_marks values: {list(filtered_marks.values())}")
        print(f"   subject_names: {subject_names}")
        print(f"   visible_count: {visible_count}")
        
        total_marks = filtered_total
        total_possible_marks = student_filtered.get('total_possible_marks', visible_count * total_marks_per_subject)
        
        # Get student's position/rank in class (from builder's sorted class_data)
        # Debug: Check what rank values are available
        student_rank = student_filtered.get('rank')
        student_index = student_filtered.get('index')
        print(f"🔍 Rank extraction for {student.name}:")
        print(f"   student_filtered keys: {list(student_filtered.keys())}")
        print(f"   rank value: {student_rank}")
        print(f"   index value: {student_index}")
        print(f"   filtered_total: {filtered_total}")
        
        # Verify rank is actually set (should be 1-based from builder)
        if student_rank is None and student_index is None:
            # Fallback: Calculate rank from sorted class_data (same logic as builder)
            print(f"⚠️ Rank not found in student_filtered, calculating from class_data...")
            # Sort exactly like builder does: by filtered_total descending
            sorted_class = sorted(class_data_list, key=lambda x: x.get("filtered_total", 0), reverse=True)
            # Assign ranks sequentially (1-based) like builder does
            for i, sd in enumerate(sorted_class, 1):
                if sd.get('student') == student.name:
                    student_position = i
                    print(f"   Calculated position: {student_position} (total: {sd.get('filtered_total')})")
                    # Also show some context - what rank do other students have?
                    print(f"   Sample ranks from sorted class:")
                    for j, other_sd in enumerate(sorted_class[:5], 1):
                        print(f"      {j}. {other_sd.get('student')[:20]}: total={other_sd.get('filtered_total')}, rank={other_sd.get('rank')}")
                    break
            else:
                student_position = 0
                print(f"   ERROR: Student not found in sorted class_data!")
        else:
            student_position = student_rank or student_index or 0
            # Verify the rank makes sense
            if student_position > 0:
                # Check if other students have the same rank (might indicate ties or issue)
                same_rank_count = sum(1 for sd in class_data_list if (sd.get('rank') == student_position or sd.get('index') == student_position))
                if same_rank_count > 1:
                    print(f"⚠️ Warning: {same_rank_count} students have rank {student_position} (possible ties)")
        
        # Keep total_points for backward compatibility but use position instead
        # Note: We'll pass student_position to template and update template to show "Position" instead of "Total Points"
        total_points = student_position  # This will be displayed as Position/Rank
        
        print(f"   Final total_marks: {total_marks}, avg_percentage: {filtered_average}, position: {student_position}")

        # Generate admission number (same as preview)
        admission_no = student.admission_number if hasattr(student, 'admission_number') and student.admission_number else f"KPS{grade}{stream[-1]}{student.id}"

        # Get academic year (same as preview)
        academic_year = term_obj.academic_year if hasattr(term_obj, 'academic_year') and term_obj.academic_year else "2023"

        # Get current date
        current_date = datetime.now().strftime("%Y-%m-%d")

        # Get school information for dynamic display
        from ..services.school_config_service import SchoolConfigService
        school_info = SchoolConfigService.get_school_info_dict()

        # Get dynamic logo URL from school setup
        logo_path = SchoolConfigService.get_school_logo_path()
        logo_url = url_for('static', filename=logo_path)

        # Read the NEW professional template file (not the Solar Theme preview template)
        template_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates', 'print_individual_report.html')
        with open(template_path, 'r', encoding='utf-8') as f:
            template_content = f.read()

        # This template is already optimized for PDF - no CSS injection needed!

        # Get staff information for dynamic teacher names
        from ..services.staff_assignment_service import StaffAssignmentService
        # Extract stream letter (e.g., "Stream B" -> "B")
        stream_letter = stream.replace("Stream ", "") if stream.startswith("Stream ") else (stream[-1] if len(stream) > 1 else stream)
        staff_info = StaffAssignmentService.get_report_staff_info(grade, stream_letter)

        # Get subject teachers mapping for the teacher column
        subject_teachers = StaffAssignmentService.get_subject_teachers(grade, stream_letter)
        
        # Also map composite (logical) subjects to teachers by aggregating component subject teachers (SAME AS PREVIEW)
        try:
            from ..models import Grade as GradeModel, TeacherSubjectAssignment
            from ..models.academic import Subject
            # Build once
            grade_obj = GradeModel.query.filter_by(name=grade).first()
            if grade_obj and stream_obj:
                # Determine candidate subject names that might be composites (appear in report subjects but not real Subject rows)
                candidate_names = subject_names  # list from builder
                for cname in candidate_names:
                    if cname in subject_teachers:
                        continue
                    # If no exact Subject exists, treat as composite name
                    subj_obj = Subject.query.filter_by(name=cname).first()
                    if subj_obj is None:
                        # Find component subjects bound to this composite name
                        component_subjects = Subject.query.filter_by(composite_parent=cname, is_component=True).all()
                        if component_subjects:
                            # Collect teacher names assigned to these components for this grade/stream
                            names = []
                            for cs in component_subjects:
                                assignments = TeacherSubjectAssignment.query.filter_by(
                                    grade_id=grade_obj.id,
                                    stream_id=stream_obj.id,
                                    subject_id=cs.id
                                ).all()
                                for a in assignments:
                                    if a.teacher:
                                        tname = getattr(a.teacher, 'full_name', None) or getattr(a.teacher, 'username', None) or ''
                                        if tname and tname not in names:
                                            names.append(tname)
                            if names:
                                label = ", ".join(names)
                                # Insert a dict compatible with template attribute access
                                subject_teachers[cname] = { 'full_name': label, 'username': label }
        except Exception as _e:
            # Non-fatal: continue without composite teacher aggregation
            pass

        # Get term information from report configuration (SAME AS PREVIEW)
        from ..services.report_config_service import ReportConfigService
        report_config_data = ReportConfigService.get_comprehensive_report_data(grade, stream_letter, term)
        if report_config_data and report_config_data.get('term_info'):
            term_config = report_config_data['term_info']
            next_term_opening = term_config.get('opening_date')
            if next_term_opening:
                next_term_opening_str = next_term_opening.strftime('%B %d, %Y') if hasattr(next_term_opening, 'strftime') else str(next_term_opening)
            else:
                next_term_opening_str = 'TBD'
        else:
            next_term_opening_str = 'TBD'

        term_info = {
            'next_term_opening_date': next_term_opening_str,
            'current_term': term,
            'academic_year': academic_year
        }
        
        # Legends for grading/weights/status (SAME AS PREVIEW)
        try:
            from ..services.mark_calculator_adapter import build_legends  # local import to avoid cycles
            _calc_legends = build_legends()
        except Exception:
            _calc_legends = None

        # Render the template with the same data as preview (using filtered data from builder)
        html_content = render_template_string(
            template_content,
            student=student,
            student_data=student_filtered,  # Use filtered student data from builder
            grade=grade,
            stream=stream,
            term=term,
            assessment_type=assessment_type,
            education_level=education_level,
            current_date=current_date,
            table_data=table_data,
            composite_data=composite_data,
            total=total_marks,  # Use filtered_total from builder
            avg_percentage=filtered_average,  # Use filtered_average from builder (SAME AS PREVIEW)
            mean_grade=mean_grade,
            mean_points=mean_points,
            total_possible_marks=total_possible_marks,
            total_points=student_position,  # Pass position/rank (will be displayed as Position)
            student_position=student_position,  # Also pass explicitly for clarity
            admission_no=admission_no,
            academic_year=academic_year,
            print_mode=True,
            school_info=school_info,  # Pass school information
            logo_url=logo_url,       # Pass dynamic logo URL
            staff_info=staff_info,   # Pass staff information
            term_info=term_info,     # Pass term information
            subject_teachers=subject_teachers,  # Pass subject teachers mapping
            calculator_legends=_calc_legends  # Optional legends for templates
        )

        # 🔥 UNIFIED PDF GENERATION using standardized utilities
        # This ensures 100% format parity between single and batch downloads
        static_folder = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static')
        
        # Generate PDF bytes - template is already print-ready, minimal processing needed
        from ..utils.pdf_generator import convert_static_urls_to_file_paths, get_standard_pdf_options
        
        # Convert static URLs to file paths
        html_content = convert_static_urls_to_file_paths(html_content, static_folder)
        
        # Generate PDF with minimal options (no CSS injection needed)
        config = pdfkit.configuration(wkhtmltopdf='C:\\Program Files\\wkhtmltopdf\\bin\\wkhtmltopdf.exe')
        options = {
            'enable-local-file-access': None,
            'quiet': '',
            'no-stop-slow-scripts': '',
            'javascript-delay': 1000,
            'encoding': 'UTF-8',
        }
        
        pdf_bytes = pdfkit.from_string(html_content, False, configuration=config, options=options)
        
        # Generate filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"Individual_Report_{grade}_{stream}_{student.name.replace(' ', '_')}_{timestamp}.pdf"
        
        return pdf_bytes, filename

    except Exception as e:
        error_msg = f"Error generating individual report PDF: {str(e)}"
        print(error_msg)
        return None, error_msg

def generate_simple_individual_report_pdf(student, grade, stream, term, assessment_type, stream_obj, term_obj, assessment_type_obj, pdf_available=False):
    """Generate individual report PDF using a simpler approach."""
    try:
        import tempfile
        import os
        from datetime import datetime

        # Get class report data first (same as preview)
        # Normalize names for resilience
        _, term_name = _resolve_term_object(term)
        _, at_name = _resolve_assessment_type_object(assessment_type)
        class_data_result = get_class_report_data(grade, stream, (term_name or term), (at_name or assessment_type))

        if class_data_result.get("error"):
            return None

        # Find student data in the class report
        student_data = None
        for data in class_data_result["class_data"]:
            if data["student"] == student.name:
                student_data = data
                break

        if not student_data:
            return None

        # Calculate mean grade and points
        avg_percentage = student_data.get("average_percentage", 0)
        from ..utils import get_grade_and_points, get_performance_remarks
        mean_grade, mean_points = get_grade_and_points(avg_percentage)

        # Get subjects with marks
        subjects_with_marks = class_data_result.get("subjects", [])

        # Calculate totals
        total_marks = student_data.get("total_marks", 0)
        total_possible_marks = len(subjects_with_marks) * class_data_result.get("total_marks", 100)
        total_points = mean_points * len(subjects_with_marks)

        # Generate admission number
        admission_no = student.admission_number if hasattr(student, 'admission_number') and student.admission_number else f"KPS{grade}{stream[-1]}{student.id}"

        # Get academic year
        academic_year = term_obj.academic_year if hasattr(term_obj, 'academic_year') and term_obj.academic_year else "2023"

        # Get current date
        current_date = datetime.now().strftime("%Y-%m-%d")

        # Create simple HTML content
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>Individual Report - {student.name}</title>
            <style>
                @page {{ size: A4; margin: 1cm; }}
                body {{ font-family: Arial, sans-serif; margin: 0; padding: 20px; }}
                .header {{ text-align: center; margin-bottom: 20px; }}
                .student-info {{ margin-bottom: 20px; }}
                .marks-table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; }}
                .marks-table th, .marks-table td {{ border: 1px solid #000; padding: 8px; text-align: center; }}
                .marks-table th {{ background-color: #f0f0f0; }}
                .summary {{ margin-top: 20px; }}
            </style>
        </head>
        <body>
            <div class="header">
                <h2>KIRIMA PRIMARY SCHOOL</h2>
                <h3>INDIVIDUAL STUDENT REPORT</h3>
                <p>{term} - {assessment_type} - Academic Year {academic_year}</p>
            </div>

            <div class="student-info">
                <p><strong>Student Name:</strong> {student.name}</p>
                <p><strong>Admission Number:</strong> {admission_no}</p>
                <p><strong>Grade:</strong> {grade}</p>
                <p><strong>Stream:</strong> {stream}</p>
                <p><strong>Date:</strong> {current_date}</p>
            </div>

            <table class="marks-table">
                <thead>
                    <tr>
                        <th>Subject</th>
                        <th>Marks</th>
                        <th>Grade</th>
                        <th>Remarks</th>
                    </tr>
                </thead>
                <tbody>
        """

        # Add subject marks
        for subject_name in subjects_with_marks:
            mark = student_data.get("marks", {}).get(subject_name, 0)
            if mark and mark > 0:
                # Clean up decimal precision
                if isinstance(mark, float):
                    mark = int(round(mark)) if mark == int(mark) else round(mark, 1)
                else:
                    mark = int(mark)

                grade_letter, points = get_grade_and_points(mark)
                remarks = get_performance_remarks(mark, 100)

                html_content += f"""
                    <tr>
                        <td>{subject_name}</td>
                        <td>{mark}</td>
                        <td>{grade_letter}</td>
                        <td>{remarks}</td>
                    </tr>
                """

        html_content += f"""
                </tbody>
            </table>

            <div class="summary">
                <p><strong>Total Marks:</strong> {total_marks}/{total_possible_marks}</p>
                <p><strong>Average Percentage:</strong> {avg_percentage:.1f}%</p>
                <p><strong>Mean Grade:</strong> {mean_grade}</p>
                <p><strong>Total Points:</strong> {total_points}</p>
            </div>
        </body>
        </html>
        """

        # Generate PDF
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"Individual_Report_{grade.replace(' ', '_')}_{stream}_{student.name.replace(' ', '_')}_{timestamp}.pdf"
        temp_dir = tempfile.gettempdir()
        pdf_path = os.path.join(temp_dir, filename)

        # Try to use pdfkit if available
        if pdf_available:
            try:
                import pdfkit
                options = {
                    'page-size': 'A4',
                    'orientation': 'Portrait',
                    'margin-top': '0.75in',
                    'margin-right': '0.75in',
                    'margin-bottom': '0.75in',
                    'margin-left': '0.75in',
                    'encoding': 'UTF-8',
                    'no-outline': None
                }
                pdfkit.from_string(html_content, pdf_path, options=options)
                return pdf_path
            except Exception as e:
                print(f"pdfkit failed: {e}")

        # Fallback: Create a simple text-based report
        try:
            text_content = f"""
KIRIMA PRIMARY SCHOOL
INDIVIDUAL STUDENT REPORT
{term} - {assessment_type} - Academic Year {academic_year}

Student Name: {student.name}
Admission Number: {admission_no}
Grade: {grade}
Stream: {stream}
Date: {current_date}

SUBJECT MARKS:
{'='*50}
"""

            # Add subject marks in text format
            for subject_name in subjects_with_marks:
                mark = student_data.get("marks", {}).get(subject_name, 0)
                if mark and mark > 0:
                    # Clean up decimal precision
                    if isinstance(mark, float):
                        mark = int(round(mark)) if mark == int(mark) else round(mark, 1)
                    else:
                        mark = int(mark)

                    grade_letter, points = get_grade_and_points(mark)
                    remarks = get_performance_remarks(mark, 100)

                    text_content += f"{subject_name:<30} {mark:>5} {grade_letter:>3} {remarks}\n"

            text_content += f"""
{'='*50}
SUMMARY:
Total Marks: {total_marks}/{total_possible_marks}
Average Percentage: {avg_percentage:.1f}%
Mean Grade: {mean_grade}
Total Points: {total_points}

Report generated on {current_date}
"""

            # Save as text file instead of PDF
            txt_path = pdf_path.replace('.pdf', '.txt')
            with open(txt_path, 'w', encoding='utf-8') as f:
                f.write(text_content)

            print(f"Created text report: {txt_path}")
            return txt_path

        except Exception as text_error:
            print(f"Text fallback also failed: {text_error}")
            return None

    except Exception as e:
        print(f"Error generating simple individual report PDF: {str(e)}")
        return None

def generate_individual_report_like_preview_for_zip(student, grade, stream, term, assessment_type, stream_obj, term_obj, assessment_type_obj, pdf_available=False):
    """Generate individual report using the exact same format as the preview template."""
    try:
        import tempfile
        import os
        from datetime import datetime
        from flask import render_template_string

        # Get class report data first (same as preview)
        print(f"🔍 Calling get_class_report_data with: grade='{grade}', stream='{stream}', term='{term}', assessment='{assessment_type}'")
        class_data_result = get_class_report_data(grade, stream, term, assessment_type)

        if class_data_result.get("error"):
            print(f"❌ get_class_report_data returned error: {class_data_result.get('error')}")
            return None

        # Find student data in the class report
        student_data = None
        class_data_list = class_data_result.get("class_data", [])
        print(f"📊 get_class_report_data returned {len(class_data_list)} students")
        
        for data in class_data_list:
            if data["student"] == student.name:
                student_data = data
                break

        if not student_data:
            print(f"⚠️ Student '{student.name}' not found in class_data")
            return None

        # Get education level based on grade (same as preview)
        education_level = ""
        grade_num = int(grade.split()[1]) if len(grade.split()) > 1 else int(grade)
        if 1 <= grade_num <= 3:
            education_level = "lower primary"
        elif 4 <= grade_num <= 6:
            education_level = "upper primary"
        elif 7 <= grade_num <= 9:
            education_level = "junior secondary"

        # Calculate mean grade and points (same as preview)
        avg_percentage = student_data.get("average_percentage", 0)
        from ..utils import get_grade_and_points, get_performance_remarks
        mean_grade, mean_points = get_grade_and_points(avg_percentage)

        # Prepare table data for the report with composite subject handling (same as preview)
        table_data = []
        composite_data = {}

        # Get only subjects that have marks in the class report data
        subjects_with_marks = class_data_result.get("subjects", [])

        for subject_name in subjects_with_marks:
            mark = student_data.get("marks", {}).get(subject_name, 0)
            if mark and mark > 0:
                # Clean up decimal precision
                if isinstance(mark, float):
                    mark = int(round(mark)) if mark == int(mark) else round(mark, 1)
                else:
                    mark = int(mark)

                grade_letter, points = get_grade_and_points(mark)
                remarks = get_performance_remarks(mark, 100)

                # Create table row data (same structure as preview)
                row_data = {
                    'subject': subject_name.upper(),
                    'entrance': 0,  # Not used for single assessments
                    'mid_term': 0,  # Not used for single assessments
                    'end_term': mark if assessment_type.lower() in ['end_term', 'endterm'] else 0,
                    'current_assessment': mark,
                    'avg': mark,
                    'remarks': remarks
                }
                table_data.append(row_data)

        # Calculate totals (same as preview)
        total_marks = student_data.get("total_marks", 0)
        total_possible_marks = len(subjects_with_marks) * class_data_result.get("total_marks", 100)
        total_points = mean_points * len(subjects_with_marks)

        # Generate admission number (same as preview)
        admission_no = student.admission_number if hasattr(student, 'admission_number') and student.admission_number else f"KPS{grade}{stream[-1]}{student.id}"

        # Get academic year (same as preview)
        academic_year = term_obj.academic_year if hasattr(term_obj, 'academic_year') and term_obj.academic_year else "2023"

        # Get current date
        current_date = datetime.now().strftime("%Y-%m-%d")

        # Get school information for dynamic display
        from ..services.school_config_service import SchoolConfigService
        school_info = SchoolConfigService.get_school_info_dict()

        # Get dynamic logo URL from school setup
        logo_path = SchoolConfigService.get_school_logo_path()
        logo_url = url_for('static', filename=logo_path)

        # Prepare term_info for template (required by preview_individual_report.html)
        term_info = {
            'next_term_opening_date': 'TBD'  # Default value since this is for individual reports
        }

        # Get staff info for template (required by preview_individual_report.html)
        staff_info = {
            'class_teacher': None,
            'headteacher': None
        }

        # Get subject teachers info (required by template)
        subject_teachers = {}

        # Read the template file and render it (same as preview)
        template_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates', 'preview_individual_report.html')
        with open(template_path, 'r', encoding='utf-8') as f:
            template_content = f.read()

        # Render the template with the same data as preview
        rendered_html = render_template_string(
            template_content,
            student=student,
            student_data=student_data,
            grade=grade,
            stream=stream,
            term=term,
            assessment_type=assessment_type,
            education_level=education_level,
            current_date=current_date,
            table_data=table_data,
            composite_data=composite_data,
            total=total_marks,
            avg_percentage=avg_percentage,
            mean_grade=mean_grade,
            mean_points=mean_points,
            total_possible_marks=total_possible_marks,
            total_points=total_points,
            admission_no=admission_no,
            academic_year=academic_year,
            term_info=term_info,  # Add missing term_info
            staff_info=staff_info,  # Add missing staff_info
            subject_teachers=subject_teachers,  # Add missing subject_teachers
            print_mode=True,  # Enable print mode for clean output
            school_info=school_info,  # Pass school information
            logo_url=logo_url  # Pass dynamic logo URL
        )

        # Generate file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        temp_dir = tempfile.gettempdir()

        if pdf_available:
            # Try to generate PDF
            try:
                filename = f"Individual_Report_{grade.replace(' ', '_')}_{stream}_{student.name.replace(' ', '_')}_{timestamp}.pdf"
                pdf_path = os.path.join(temp_dir, filename)
                
                # SECURITY: Sanitize HTML content before PDF generation (remove dangerous elements only, don't escape)
                def sanitize_html_for_pdf(html_content):
                    """Sanitize HTML content to prevent command injection in PDF generation."""
                    import re
                    
                    # Remove potentially dangerous HTML elements/attributes (but don't escape valid HTML)
                    dangerous_patterns = [
                        r'<script[^>]*>.*?</script>',
                        r'<iframe[^>]*>.*?</iframe>', 
                        r'<object[^>]*>.*?</object>',
                        r'<embed[^>]*>.*?</embed>',
                        r'on\w+\s*=\s*["\'][^"\']*["\']',  # onclick, onload, etc.
                        r'javascript\s*:',
                        r'vbscript\s*:',
                        r'data\s*:\s*text/html'
                    ]
                    
                    for pattern in dangerous_patterns:
                        html_content = re.sub(pattern, '', html_content, flags=re.IGNORECASE | re.DOTALL)
                    
                    return html_content
                
                # Sanitize the HTML (template already has @media print CSS, don't override it)
                html_with_css = sanitize_html_for_pdf(rendered_html)
                
                # Force print styles by adding explicit override CSS before </head>
                force_print_css = """
                <style>
                /* Force all print CSS to apply */
                @page { size: A4; margin: 0.4in; }
                body { -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; }
                * { -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; }
                
                /* Force grayscale/ink-saver */
                body, .report-container { background: white !important; color: black !important; }
                .marksheet-header, .assessment-info-section, .summary-section { 
                    background: white !important; 
                    color: black !important;
                    border-color: black !important;
                }
                .marksheet-title { background: white !important; color: black !important; border-color: black !important; }
                .info-value { background: white !important; color: black !important; border-color: black !important; }
                table, th, td { background: white !important; color: black !important; border-color: black !important; }
                .marks-table thead { background: #f0f0f0 !important; color: black !important; }
                .marks-table tbody tr:nth-child(even) { background: #f9f9f9 !important; }
                .subject-name { color: black !important; }
                h1, h2, h3, h4, h5, h6, p, span, div { color: black !important; }
                
                /* Hide non-print elements */
                .action-buttons, .print-controls, .delete-btn, .modal, button { display: none !important; }
                
                /* Ensure borders are visible */
                .marksheet-header, .assessment-info-section, .marksheet-container, .summary-section, .remarks {
                    border: 2px solid black !important;
                }
                </style>
                """
                html_with_css = html_with_css.replace('</head>', f'{force_print_css}</head>')
                
                # Convert relative /static/ URLs to absolute file:// paths for wkhtmltopdf
                import re as re_module
                static_folder = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static')
                
                def replace_static_url(match):
                    """Replace /static/... URLs with file:// paths"""
                    relative_path = match.group(1)
                    absolute_path = os.path.join(static_folder, relative_path).replace('\\', '/')
                    return f'file:///{absolute_path}'
                
                # Replace all /static/ references with absolute file paths
                html_with_css = re_module.sub(
                    r'["\']?/static/([^"\'>\s]+)["\']?',
                    lambda m: f'"{replace_static_url(m)}"',
                    html_with_css
                )
                
                # Also handle url() in CSS
                html_with_css = re_module.sub(
                    r'url\(["\']?/static/([^"\')\s]+)["\']?\)',
                    lambda m: f'url("{replace_static_url(m)}")',
                    html_with_css
                )

                # Use pdfkit (same as class reports) for better Windows compatibility
                try:
                    import pdfkit
                    import os
                    
                    # Configure pdfkit with explicit wkhtmltopdf path for Windows
                    WKHTMLTOPDF_PATH = r"C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe"
                    
                    if os.path.exists(WKHTMLTOPDF_PATH):
                        config = pdfkit.configuration(wkhtmltopdf=WKHTMLTOPDF_PATH)
                        print(f"Using wkhtmltopdf at: {WKHTMLTOPDF_PATH}")
                    else:
                        # Fallback to default configuration
                        config = pdfkit.configuration()
                        print("Using default pdfkit configuration")
                    
                    options = {
                        'page-size': 'A4',
                        'orientation': 'Portrait',
                        'margin-top': '0.4in',
                        'margin-right': '0.4in',
                        'margin-bottom': '0.4in',
                        'margin-left': '0.4in',
                        'encoding': 'UTF-8',
                        'no-outline': None,
                        'enable-local-file-access': None,
                        # Force print mode to apply @media print CSS
                        'print-media-type': None,
                        # SECURITY: Disable JavaScript execution in PDF generation
                        'disable-javascript': None,
                        # Be tolerant of missing assets
                        'load-error-handling': 'ignore',
                        'load-media-error-handling': 'ignore',
                    }
                    
                    # SECURITY: Use secure PDF generation with input validation
                    def secure_pdf_generation(html_content, output_path, pdf_options, pdf_config):
                        """Securely generate PDF with additional validation."""
                        import tempfile
                        import os
                        
                        # Create temporary HTML file with restricted permissions
                        with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, encoding='utf-8') as temp_html:
                            temp_html.write(html_content)
                            temp_html_path = temp_html.name
                        
                        try:
                            # Set restrictive file permissions (Windows compatible)
                            if hasattr(os, 'chmod'):
                                os.chmod(temp_html_path, 0o600)  # Read/write for owner only
                            
                            # Generate PDF from temporary file (safer than from_string)
                            pdfkit.from_file(temp_html_path, output_path, options=pdf_options, configuration=pdf_config)
                            
                        finally:
                            # Always clean up temporary file
                            try:
                                os.unlink(temp_html_path)
                            except OSError:
                                pass
                    
                    # Use secure PDF generation with a light retry in case wkhtmltopdf crashes intermittently
                    last_error = None
                    for attempt in range(1, 3):
                        try:
                            secure_pdf_generation(html_with_css, pdf_path, options, config)
                            print(f"Created PDF report (pdfkit): {pdf_path} (attempt {attempt})")
                            return pdf_path
                        except Exception as attempt_err:
                            last_error = attempt_err
                            print(f"wkhtmltopdf attempt {attempt} failed for {student.name}: {attempt_err}")
                            try:
                                import time as _t
                                _t.sleep(0.5)
                            except Exception:
                                pass
                    # After attempts failed, raise to trigger fallback
                    raise last_error
                except Exception as pdfkit_error:
                    print(f"pdfkit failed: {pdfkit_error}")
                    # Fallback to WeasyPrint if pdfkit fails
                    try:
                        import weasyprint
                        weasyprint.HTML(string=html_with_css).write_pdf(pdf_path)
                        print(f"Created PDF report (WeasyPrint fallback): {pdf_path}")
                        return pdf_path
                    except Exception as weasy_error:
                        print(f"WeasyPrint also failed: {weasy_error}. Falling back to HTML output for {student.name}.")
            except Exception as e:
                print(f"PDF generation failed: {e}")
                # Fall through to HTML generation

        # Generate HTML file as fallback
        filename = f"Individual_Report_{grade.replace(' ', '_')}_{stream}_{student.name.replace(' ', '_')}_{timestamp}.html"
        html_path = os.path.join(temp_dir, filename)

        # Add some basic styling for standalone HTML
        standalone_css = """
        <style>
        body { font-family: Arial, sans-serif; margin: 20px; }
        .action-buttons, .print-controls, .delete-btn, .modal { display: none !important; }
        .report-container { max-width: 800px; margin: 0 auto; }
        table { border-collapse: collapse; width: 100%; margin: 20px 0; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: center; }
        th { background-color: #f2f2f2; }
        .header { text-align: center; margin-bottom: 20px; }
        .student-details { margin: 20px 0; }
        .remarks { margin: 20px 0; }
        .footer { margin-top: 20px; text-align: center; font-size: 12px; color: #666; }
        </style>
        """
        html_with_css = rendered_html.replace('</head>', f'{standalone_css}</head>')

        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html_with_css)

        print(f"Created HTML report: {html_path}")
        return html_path

    except Exception as e:
        print(f"Error generating individual report like preview: {str(e)}")
        return None

@classteacher_bp.route('/get_zip_generation_progress/<grade>/<stream>/<term>/<assessment_type>')
@classteacher_required
@limiter.exempt
def get_zip_generation_progress(grade, stream, term, assessment_type):
    """Get the current progress of ZIP generation for real-time updates."""
    request_key = f"zip_generation_{grade}_{stream}_{term}_{assessment_type}"
    progress = _get_zip_progress(request_key)

    if not progress:
        progress = {
            'current': 0,
            'total': 0,
            'status': 'pending'
        }

    return jsonify(progress)

@classteacher_bp.route('/download_selected_reports/<grade>/<stream>/<term>/<assessment_type>', methods=['POST'])
@classteacher_required
def download_selected_reports(grade, stream, term, assessment_type):
    """
    Generate and return a ZIP containing individual reports for SELECTED students only.
    This uses the exact same PDF generation as single downloads, guaranteeing format parity.
    """
    print(f"🎯 Selective ZIP Generation Started: {grade} {stream} {term} {assessment_type}")
    
    try:
        # Get selected student names from form data
        student_names = request.form.getlist('student_names')
        
        if not student_names:
            flash("No students selected", 'error')
            return redirect(url_for('classteacher.view_student_reports', grade=grade, stream=stream, term=term, assessment_type=assessment_type))
        
        print(f"📋 Selected {len(student_names)} students: {student_names}")
        
        # Resolve stream, term, and assessment type
        stream_candidates = {stream}
        parts = stream.split()
        if len(parts) > 1:
            stream_candidates.add(parts[-1])
        
        stream_obj = Stream.query.join(Grade).filter(
            Grade.name == grade,
            Stream.name.in_(stream_candidates)
        ).first()
        
        term_obj, term_name = _resolve_term_object(term)
        assessment_type_obj, at_name = _resolve_assessment_type_object(assessment_type)
        
        if not (stream_obj and term_obj and assessment_type_obj):
            flash("Invalid grade, stream, term, or assessment type", 'error')
            return redirect(url_for('classteacher.view_student_reports', grade=grade, stream=stream, term=term, assessment_type=assessment_type))
        
        # Get student objects for selected names
        students = Student.query.filter(
            Student.stream_id == stream_obj.id,
            Student.name.in_(student_names)
        ).all()
        
        print(f"📊 Found {len(students)} matching student records")
        
        if not students:
            flash("No matching students found", 'error')
            return redirect(url_for('classteacher.view_student_reports', grade=grade, stream=stream, term=term, assessment_type=assessment_type))
        
        # Create ZIP in memory
        import zipfile
        import tempfile
        from datetime import datetime
        
        temp_dir = tempfile.mkdtemp()
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_grade = grade.replace(' ', '_')
        zip_filename = f"Selected_Reports_{safe_grade}_{stream}_{len(students)}students_{timestamp}.zip"
        zip_path = os.path.join(temp_dir, zip_filename)
        
        successful_reports = 0
        failed_reports = 0
        grade_for_service = grade
        stream_for_service = stream_obj.name
        
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            for i, student in enumerate(students, start=1):
                try:
                    print(f"📄 ({i}/{len(students)}) Generating report for: {student.name}")
                    
                    # 🔥 UNIFIED PDF GENERATION - Same function as single download
                    result = generate_student_report_pdf_bytes(
                        student,
                        grade_for_service,
                        stream_for_service,
                        (term_name or term),
                        (at_name or assessment_type),
                        stream_obj,
                        term_obj,
                        assessment_type_obj
                    )
                    
                    if result[0] is None:
                        failed_reports += 1
                        print(f"⚠️ PDF generation failed for {student.name}: {result[1]}")
                        continue
                    
                    pdf_bytes, filename = result
                    zipf.writestr(filename, pdf_bytes)
                    successful_reports += 1
                    print(f"✅ Added {filename}")
                    
                except Exception as e:
                    failed_reports += 1
                    print(f"❌ Exception building report for {student.name}: {e}")
                    import traceback
                    traceback.print_exc()
                    continue
        
        print(f"📊 Summary: success={successful_reports}, failed={failed_reports}")
        
        if successful_reports == 0:
            flash(f"No reports could be generated", 'error')
            return redirect(url_for('classteacher.view_student_reports', grade=grade, stream=stream, term=term, assessment_type=assessment_type))
        
        # Read ZIP into memory and send
        with open(zip_path, 'rb') as f:
            zip_data = f.read()
        
        # Clean up
        try:
            os.remove(zip_path)
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
            print(f"🗑️ Cleaned up temp files")
        except Exception as cleanup_error:
            print(f"⚠️ Warning: Could not cleanup temp files: {cleanup_error}")
        
        print(f"📤 Sending ZIP: {zip_filename} ({len(zip_data)} bytes)")
        
        # Create response
        response = send_file(
            BytesIO(zip_data),
            as_attachment=True,
            download_name=zip_filename,
            mimetype='application/zip'
        )
        
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Content-Type'] = 'application/zip'
        response.headers['Content-Disposition'] = f'attachment; filename="{zip_filename}"'
        
        flash(f"Successfully generated {successful_reports} report(s)!", 'success')
        return response
        
    except Exception as e:
        print(f"💥 Critical error in selective ZIP generation: {e}")
        import traceback
        traceback.print_exc()
        flash(f"Error generating reports: {str(e)}", 'error')
        return redirect(url_for('classteacher.view_student_reports', grade=grade, stream=stream, term=term, assessment_type=assessment_type))


@classteacher_bp.route('/generate_all_individual_reports/<grade>/<stream>/<term>/<assessment_type>')
@classteacher_required
def generate_all_individual_reports(grade, stream, term, assessment_type):
    """Generate and return a ZIP containing all individual reports for a stream - SIMPLIFIED VERSION."""
    print(f"🎯 ZIP Generation Started: {grade} {stream} {term} {assessment_type}")
    
    try:
        print(
            f"🚀 Starting ZIP generation for grade='{grade}', stream='{stream}', term='{term}', assessment='{assessment_type}'"
        )

        # Attempt to resolve the stream more intelligently instead of using stream[-1]
        raw_stream = stream
        stream_name_candidates = {raw_stream}
        parts = raw_stream.split()
        if len(parts) > 1:
            stream_name_candidates.add(parts[-1])
        if parts:
            stream_name_candidates.add(parts[-1].upper())
            stream_name_candidates.add(parts[-1].lower())

        print(f"🔍 Stream name candidates: {stream_name_candidates}")

        stream_obj = (
            Stream.query.join(Grade)
            .filter(Grade.name == grade, Stream.name.in_(stream_name_candidates))
            .first()
        )

        # Resolve term and assessment type flexibly
        term_obj, term_name = _resolve_term_object(term)
        assessment_type_obj, at_name = _resolve_assessment_type_object(assessment_type)

        if not (stream_obj and term_obj and assessment_type_obj):
            flash("Invalid grade, stream, term, or assessment type", 'error')
            return redirect(url_for('classteacher.view_student_reports', grade=grade, stream=stream, term=term, assessment_type=assessment_type))

        students = Student.query.filter_by(stream_id=stream_obj.id).all()
        print(f"📊 Found {len(students)} students for stream id={stream_obj.id} ({stream_obj.name})")

        if not students:
            flash(f"No students found for {grade} Stream {stream_obj.name}", 'error')
            return redirect(url_for('classteacher.view_student_reports', grade=grade, stream=stream, term=term, assessment_type=assessment_type))

        import zipfile
        import tempfile as tmp_module2  # Use different alias for second import
        from datetime import datetime

        temp_dir = tmp_module2.mkdtemp()
        print(f"📁 Using temp directory: {temp_dir}")

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_grade = grade.replace(' ', '_')
        zip_filename = f"Individual_Reports_{safe_grade}_{stream}_{term}_{assessment_type}_{timestamp}.zip"
        zip_path = os.path.join(temp_dir, zip_filename)
        print(f"📦 Will create ZIP at: {zip_path}")

        successful_reports = 0
        failed_reports = 0

        # For get_class_report_data(), we need the FULL grade name ("Grade 9") and stream letter ("G")
        # grade already comes as "Grade 9" from the route parameter
        # stream_obj.name is the single letter "G"
        grade_for_service = grade  # Keep full "Grade 9" format
        stream_for_service = stream_obj.name  # Use actual stream letter "G" from database

        with zipfile.ZipFile(zip_path, 'w') as zipf:
            for i, student in enumerate(students, start=1):
                try:
                    print(f"📄 ({i}/{len(students)}) Generating report for: {student.name}")

                    # 🔥 UNIFIED PDF GENERATION - Call the EXACT SAME function as single download
                    # This guarantees 100% format parity between single and batch downloads
                    result = generate_student_report_pdf_bytes(
                        student,
                        grade_for_service,
                        stream_for_service,
                        (term_name or term),
                        (at_name or assessment_type),
                        stream_obj,
                        term_obj,
                        assessment_type_obj
                    )

                    if result[0] is None:
                        failed_reports += 1
                        print(f"⚠️ PDF generation failed for {student.name}: {result[1]}")
                        continue

                    pdf_bytes, filename = result
                    
                    # Write PDF bytes directly to ZIP
                    zipf.writestr(filename, pdf_bytes)
                    successful_reports += 1
                    print(f"✅ Added {filename}")
                except Exception as e:
                    failed_reports += 1
                    print(f"❌ Exception building report for {student.name}: {e}")
                    continue

        print(f"📊 Summary: success={successful_reports}, failed={failed_reports}")

        if successful_reports == 0:
            flash(f"No reports could be generated. Please ensure students have marks for {term} {assessment_type}.", 'error')
            return redirect(url_for('classteacher.view_student_reports', grade=grade, stream=stream, term=term, assessment_type=assessment_type))

        if not os.path.exists(zip_path):
            flash("ZIP file was not created due to an internal error.", 'error')
            return redirect(url_for('classteacher.view_student_reports', grade=grade, stream=stream, term=term, assessment_type=assessment_type))

        print(f"✅ ZIP file created successfully at: {zip_path}, size: {os.path.getsize(zip_path)} bytes")
        flash(f"Successfully generated {successful_reports} individual reports in ZIP format!", 'success')

        # Read the ZIP file into memory to avoid file locking issues
        with open(zip_path, 'rb') as f:
            zip_data = f.read()
        
        # Clean up temp files immediately after reading
        try:
            os.remove(zip_path)
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
            print(f"🗑️ Cleaned up temp files")
        except Exception as cleanup_error:
            print(f"⚠️ Warning: Could not cleanup temp files: {cleanup_error}")
        
        # Create response from in-memory data
        from io import BytesIO
        response = send_file(
            BytesIO(zip_data),
            as_attachment=True,
            download_name=zip_filename,
            mimetype='application/zip'
        )
        
        # Set comprehensive headers for ZIP download
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Type'] = 'application/zip'
        response.headers['Content-Disposition'] = f'attachment; filename="{zip_filename}"'
        response.headers['Content-Length'] = str(len(zip_data))
        
        print(f"📤 Sending ZIP response: {zip_filename} ({len(zip_data)} bytes)")
        
        return response

    except Exception as e:
        print(f"💥 Critical error in ZIP generation: {e}")
        import traceback
        traceback.print_exc()
        flash(f"Error generating reports: {str(e)}", 'error')
        return redirect(url_for('classteacher.view_student_reports', grade=grade, stream=stream, term=term, assessment_type=assessment_type))

@classteacher_bp.route('/download_class_list', methods=['GET'])
@classteacher_required
def download_class_list():
    """Route to download class lists as CSV files."""

    # Use centralized canonical mapping
    from ..utils.constants import educational_level_mapping as educational_level_mapping_ui

    # Get filter parameters
    educational_level = request.args.get('educational_level', '')
    grade_id = request.args.get('grade_id', '')
    stream_id = request.args.get('stream_id', '')
    all_grades = request.args.get('all_grades', '')
    search_query = request.args.get('search', '').strip()

    # Build the query based on filters
    students_query = Student.query

    # Apply search if provided
    if search_query:
        # Search by name or admission number
        students_query = students_query.filter(
            (Student.name.ilike(f'%{search_query}%')) |
            (Student.admission_number.ilike(f'%{search_query}%'))
        )

    # Filter by stream if specified
    if stream_id:
        students_query = students_query.filter_by(stream_id=stream_id)
        stream = Stream.query.get(stream_id)
        grade = Grade.query.get(stream.grade_id) if stream else None
        filename = f"Class_List_{grade.name if grade else 'Unknown'}_{stream.name if stream else 'Unknown'}.csv"

    # Filter by grade if specified (and not already filtered by stream)
    elif grade_id:
        grade = Grade.query.get(grade_id)
        # Get all streams for this grade
        grade_streams = Stream.query.filter_by(grade_id=grade_id).all()
        stream_ids = [s.id for s in grade_streams]
        if stream_ids:
            students_query = students_query.filter(Student.stream_id.in_(stream_ids))
        filename = f"Class_List_{grade.name if grade else 'Unknown'}_All_Streams.csv"

    # Filter by educational level if specified
    elif educational_level:
        # Get all grades for this educational level
        allowed_grades = educational_level_mapping_ui.get(educational_level, [])
        grades = Grade.query.filter(Grade.name.in_(allowed_grades)).all()
        grade_ids = [g.id for g in grades]

        # Get all streams for these grades
        streams = Stream.query.filter(Stream.grade_id.in_(grade_ids)).all()
        stream_ids = [s.id for s in streams]

        if stream_ids:
            students_query = students_query.filter(Student.stream_id.in_(stream_ids))

        filename = f"Class_List_{educational_level.replace('_', ' ').title()}.csv"

    # Download all students if specified
    elif all_grades:
        filename = "All_Students.csv"

    # Default filename if no filters are applied
    else:
        filename = "Class_List.csv"

    # Get all students matching the filters
    students = students_query.order_by(Student.name).all()

    # Create a CSV string
    csv_data = "ID,Name,Admission Number,Grade,Stream,Gender\n"

    for student in students:
        grade_name = student.stream.grade.name if student.stream else "N/A"
        stream_name = student.stream.name if student.stream else "No Stream"
        gender = student.gender.capitalize() if student.gender else "Not Set"

        csv_data += f"{student.id},{student.name},{student.admission_number},{grade_name},{stream_name},{gender}\n"

    # Create a response with the CSV data
    response = make_response(csv_data)
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    response.headers["Content-Type"] = "text/csv"

    return response



@classteacher_bp.route('/download_subject_template', methods=['GET'])
@classteacher_required
def download_subject_template():
    """Route to download the subject upload template."""
    file_format = request.args.get('format', 'xlsx')

    if file_format == 'csv':
        template_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'templates', 'subject_upload_template.csv')
        mimetype = 'text/csv'
        filename = 'subject_upload_template.csv'
    else:
        template_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'templates', 'subject_upload_template.xlsx')
        mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        filename = 'subject_upload_template.xlsx'

    # Check if the template file exists
    if not os.path.exists(template_path):
        # If not, create it
        try:
            from ..static.templates.subject_upload_template import create_subject_upload_template
            create_subject_upload_template()
        except Exception as e:
            print(f"Error creating template: {str(e)}")
            flash("Error creating template file. Please try again.", "error")
            return redirect(url_for('classteacher.manage_subjects'))

    return send_file(
        template_path,
        as_attachment=True,
        download_name=filename,
        mimetype=mimetype
    )

@classteacher_bp.route('/export_subjects', methods=['GET'])
@classteacher_required
def export_subjects():
    """Route to export all subjects as CSV or Excel."""
    file_format = request.args.get('format', 'xlsx')
    education_level = request.args.get('education_level', '')

    # Build the query based on filters
    subjects_query = Subject.query

    # Apply education level filter if specified
    if education_level and education_level != 'all':
        subjects_query = subjects_query.filter_by(education_level=education_level)
        filename_prefix = f"subjects_{education_level}"
    else:
        filename_prefix = "all_subjects"

    # Order by education level and name
    subjects = subjects_query.order_by(Subject.education_level, Subject.name).all()

    # Create a DataFrame with the subjects
    data = []
    for subject in subjects:
        data.append({
            'id': subject.id,
            'name': subject.name,
            'education_level': subject.education_level
        })

    df = pd.DataFrame(data)

    # Create a BytesIO object to store the Excel file
    output = BytesIO()

    if file_format == 'csv':
        # Write the DataFrame to a CSV file
        df.to_csv(output, index=False)
        mimetype = 'text/csv'
        filename = f"{filename_prefix}.csv"
    else:
        # Write the DataFrame to an Excel file
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, sheet_name='Subjects', index=False)

            # Get the xlsxwriter workbook and worksheet objects
            workbook = writer.book
            worksheet = writer.sheets['Subjects']

            # Add some formatting
            header_format = workbook.add_format({
                'bold': True,
                'text_wrap': True,
                'valign': 'top',
                'fg_color': '#D7E4BC',
                'border': 1
            })

            # Write the column headers with the defined format
            for col_num, value in enumerate(df.columns.values):
                worksheet.write(0, col_num, value, header_format)

            # Set the column widths
            worksheet.set_column('A:A', 5)  # ID column
            worksheet.set_column('B:B', 30)  # Name column
            worksheet.set_column('C:C', 20)  # Education level column

        mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        filename = f"{filename_prefix}.xlsx"

    # Seek to the beginning of the BytesIO object
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype=mimetype
    )

def _validate_bulk_subject_import():
    from flask import current_app
    cfg = current_app.config
    allowed = cfg.get('FILE_ALLOWED_DATA_EXTENSIONS', {'.csv', '.xlsx', '.xls'})
    max_bytes = cfg.get('FILE_UPLOAD_MAX_BYTES', 5 * 1024 * 1024)
    meta = validate_uploaded_file('subject_file', allowed_exts=allowed, max_bytes=max_bytes)
    return {'filename': meta['filename'], 'ext': meta['ext'], 'size': meta['size']}

@classteacher_bp.route('/bulk_import_subjects', methods=['POST'])
@secure_endpoint(roles=['classteacher','headteacher'], rate=(5,300), validator=_validate_bulk_subject_import, audit_event='subjects.bulk_import')
def bulk_import_subjects(_validated):
    file = request.files['subject_file']
    ext = _validated['ext']
    try:
        if ext == '.csv':
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)
        required_columns = ['name', 'education_level']
        missing = [c for c in required_columns if c not in df.columns]
        if missing:
            raise ValidationError('Missing required columns', {'missing': missing})
        added_count = 0
        skipped_count = 0
        # Accept only canonical levels; normalize simple variants
        from ..utils.constants import EDUCATION_LEVELS_ORDER
        canonical_levels = set(EDUCATION_LEVELS_ORDER)  # includes pre_primary and senior_secondary too
        for _, row in df.iterrows():
            name = str(row['name']).strip()
            edu_raw = str(row['education_level']).strip()
            edu = edu_raw.replace(' ', '_').lower()
            if edu not in canonical_levels:
                skipped_count += 1
                continue
            existing = Subject.query.filter_by(name=name, education_level=edu).first()
            if existing:
                skipped_count += 1
                continue
            db.session.add(Subject(name=name, education_level=edu))
            added_count += 1
        db.session.commit()
        if wants_json(request):
            return jsonify({'status':'ok','added':added_count,'skipped':skipped_count,'file':_validated['filename']}), 200
        if added_count:
            flash(f"Added {added_count} subject(s); skipped {skipped_count}.", 'success')
        else:
            flash(f"No new subjects added; skipped {skipped_count}.", 'info')
        return redirect(url_for('classteacher.manage_subjects'))
    except ValidationError as ve:
        if wants_json(request):
            return jsonify({'error':{'code':'INVALID_REQUEST','message':str(ve),'details':ve.details}}), 422
        flash(str(ve), 'error')
        return redirect(url_for('classteacher.manage_subjects'))
    except Exception as e:  # pragma: no cover - unexpected path
        try:
            from flask import current_app
            current_app.logger.exception('Bulk import failure')
        except Exception:
            pass
        if wants_json(request):
            return jsonify({'error':{'code':'SERVER_ERROR','message':'Unexpected error'}}), 500
        flash('Unexpected error during import', 'error')
        return redirect(url_for('classteacher.manage_subjects'))

@classteacher_bp.route('/manage_subjects', methods=['GET', 'POST'])
@classteacher_required
def manage_subjects():
    """Route for managing subjects."""
    error_message = None
    success_message = None

    # Get filter and pagination parameters
    education_level = request.args.get('education_level', '')
    search_query = request.args.get('search', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 10  # Number of subjects per page

    # Build the query based on filters
    subjects_query = Subject.query

    # Apply search if provided
    if search_query:
        subjects_query = subjects_query.filter(Subject.name.ilike(f'%{search_query}%'))

    # Apply education level filter if specified
    if education_level and education_level != 'all':
        subjects_query = subjects_query.filter_by(education_level=education_level)

    # Order by education level and name
    subjects_query = subjects_query.order_by(Subject.education_level, Subject.name)

    # Paginate the results
    pagination = subjects_query.paginate(page=page, per_page=per_page, error_out=False)
    subjects = pagination.items

    # Get all unique education levels for the filter dropdown (ordered canonically)
    all_education_levels = db.session.query(Subject.education_level).distinct().all()
    education_levels = [level[0] for level in all_education_levels if level[0]]
    try:
        from ..utils.constants import order_levels
        education_levels = order_levels(education_levels)
    except Exception:
        pass

    # Calculate statistics
    total_subjects = Subject.query.count()
    lower_primary_count = Subject.query.filter_by(education_level='lower_primary').count()
    upper_primary_count = Subject.query.filter_by(education_level='upper_primary').count()
    junior_secondary_count = Subject.query.filter_by(education_level='junior_secondary').count()

    if request.method == 'POST':
        action = request.form.get("action")

        if "add_subject" in request.form:
            subject_name = request.form.get("name")
            education_level = request.form.get("education_level")

            if not subject_name:
                error_message = "Please fill in the subject name."
            elif not education_level:
                error_message = "Please select an education level."
            else:
                # Clean and validate subject name
                subject_name = subject_name.strip()
                if not subject_name:
                    error_message = "Subject name cannot be empty."
                else:
                    try:
                        # Check if subject with same name and education level already exists
                        existing_subject = Subject.query.filter_by(name=subject_name, education_level=education_level).first()
                        if existing_subject:
                            error_message = f"Subject '{subject_name}' already exists for {education_level.replace('_', ' ').title()}."
                        else:
                            new_subject = Subject(name=subject_name, education_level=education_level)
                            db.session.add(new_subject)
                            db.session.commit()
                            success_message = f"Subject '{subject_name}' added successfully for {education_level.replace('_', ' ').title()}!"
                    except Exception as e:
                        db.session.rollback()
                        if "UNIQUE constraint failed" in str(e):
                            error_message = f"Subject '{subject_name}' already exists. Please choose a different name."
                        else:
                            error_message = f"Error adding subject: {str(e)}"
                        print(f"Subject creation error: {e}")

        elif "delete_subject" in request.form:
            subject_id = request.form.get("subject_id")
            subject = Subject.query.get(subject_id)
            if subject:
                try:
                    # Check if subject has marks
                    marks = Mark.query.filter_by(subject_id=subject.id).all()
                    if marks:
                        error_message = f"Cannot delete subject '{subject.name}' because it has marks associated with it."
                    else:
                        # Clean up related data before deletion
                        cleanup_count = 0

                        # 1. Remove teacher-subject assignments
                        try:
                            from ..models.assignment import TeacherSubjectAssignment
                            assignments = TeacherSubjectAssignment.query.filter_by(subject_id=subject.id).all()
                            for assignment in assignments:
                                db.session.delete(assignment)
                                cleanup_count += 1
                        except Exception as cleanup_error:
                            print(f"Warning: Could not clean up assignments for subject {subject.id}: {cleanup_error}")

                        # 2. Remove subject components (if table exists)
                        try:
                            from ..models.academic import SubjectComponent
                            components = SubjectComponent.query.filter_by(subject_id=subject.id).all()
                            for component in components:
                                # Remove component marks first
                                try:
                                    from ..models.academic import ComponentMark
                                    component_marks = ComponentMark.query.filter_by(component_id=component.id).all()
                                    for mark in component_marks:
                                        db.session.delete(mark)
                                        cleanup_count += 1
                                except Exception as component_mark_error:
                                    print(f"Warning: Could not clean up component marks for component {component.id}: {component_mark_error}")

                                db.session.delete(component)
                                cleanup_count += 1
                        except Exception as component_error:
                            print(f"Warning: Could not clean up components for subject {subject.id}: {component_error}")

                        # 3. Remove teacher-subject relationships (many-to-many)
                        try:
                            # Clear the many-to-many relationship
                            subject.teachers.clear()
                            cleanup_count += len(subject.teachers)
                        except Exception as teacher_rel_error:
                            print(f"Warning: Could not clear teacher relationships for subject {subject.id}: {teacher_rel_error}")

                        if cleanup_count > 0:
                            print(f"Cleaned up {cleanup_count} related records for subject {subject.id}")

                        # Store subject name before deletion
                        subject_name = subject.name
                        db.session.delete(subject)
                        db.session.commit()
                        success_message = f"Subject '{subject_name}' deleted successfully!"

                except Exception as e:
                    db.session.rollback()
                    error_message = f"Error deleting subject: {str(e)}"
                    print(f"Subject deletion error: {e}")
            else:
                error_message = "Subject not found."

        elif action == "edit_subject":
            subject_id = request.form.get("subject_id")
            subject_name = request.form.get("name")
            education_level = request.form.get("education_level")

            if not subject_id or not subject_name or not education_level:
                error_message = "Please fill in all fields."
            else:
                subject = Subject.query.get(subject_id)
                if not subject:
                    error_message = "Subject not found."
                else:
                    # Check if another subject with the same name and education level already exists
                    existing_subject = Subject.query.filter(
                        Subject.name == subject_name,
                        Subject.education_level == education_level,
                        Subject.id != subject.id
                    ).first()

                    if existing_subject:
                        error_message = f"Subject '{subject_name}' already exists for {education_level.replace('_', ' ').title()}."
                    else:
                        # Update the subject
                        subject.name = subject_name
                        subject.education_level = education_level
                        db.session.commit()
                        success_message = f"Subject '{subject_name}' updated successfully!"

        elif action == "delete_selected_subjects":
            subject_ids = request.form.getlist("subject_ids")
            if not subject_ids:
                error_message = "No subjects selected for deletion."
            else:
                deleted_count = 0
                not_deleted_count = 0

                try:
                    for subject_id in subject_ids:
                        subject = Subject.query.get(subject_id)
                        if subject:
                            # Check if subject has marks
                            marks = Mark.query.filter_by(subject_id=subject.id).all()
                            if marks:
                                not_deleted_count += 1
                            else:
                                # Clean up related data before deletion
                                try:
                                    # Remove teacher-subject assignments
                                    from ..models.assignment import TeacherSubjectAssignment
                                    assignments = TeacherSubjectAssignment.query.filter_by(subject_id=subject.id).all()
                                    for assignment in assignments:
                                        db.session.delete(assignment)

                                    # Remove subject components
                                    try:
                                        from ..models.academic import SubjectComponent
                                        components = SubjectComponent.query.filter_by(subject_id=subject.id).all()
                                        for component in components:
                                            # Remove component marks first
                                            try:
                                                from ..models.academic import ComponentMark
                                                component_marks = ComponentMark.query.filter_by(component_id=component.id).all()
                                                for mark in component_marks:
                                                    db.session.delete(mark)
                                            except Exception:
                                                pass  # Component marks table might not exist
                                            db.session.delete(component)
                                    except Exception:
                                        pass  # Component table might not exist

                                    # Clear teacher relationships
                                    try:
                                        subject.teachers.clear()
                                    except Exception:
                                        pass  # Relationship might not work if tables missing

                                except Exception as cleanup_error:
                                    print(f"Warning: Could not clean up data for subject {subject.id}: {cleanup_error}")

                                db.session.delete(subject)
                                deleted_count += 1

                    if deleted_count > 0:
                        db.session.commit()
                        success_message = f"Successfully deleted {deleted_count} subject(s)."

                    if not_deleted_count > 0:
                        if success_message:
                            success_message += f" {not_deleted_count} subject(s) could not be deleted because they have marks associated with them."
                        else:
                            error_message = f"Could not delete {not_deleted_count} subject(s) because they have marks associated with them."

                except Exception as e:
                    db.session.rollback()
                    error_message = f"Error during bulk deletion: {str(e)}"
                    print(f"Bulk subject deletion error: {e}")

    # Mark newly added subjects
    for subject in subjects:
        subject.is_new = False
        if request.args.get('highlight') and int(request.args.get('highlight')) == 1:
            if 'new_subject_ids' in session:
                if subject.id in session['new_subject_ids']:
                    subject.is_new = True

    # Clear session data after use
    if 'new_subject_ids' in session:
        session.pop('new_subject_ids')

    return render_template(
        'manage_subjects.html',
        subjects=subjects,
        pagination=pagination,
        education_levels=education_levels,
        current_education_level=education_level,
        search_query=search_query,
        total_subjects=total_subjects,
        lower_primary_count=lower_primary_count,
        upper_primary_count=upper_primary_count,
        junior_secondary_count=junior_secondary_count,
        error_message=error_message,
        success_message=success_message
    )

@classteacher_bp.route('/manage_grades_streams', methods=['GET', 'POST'])
@classteacher_required
def manage_grades_streams():
    """Route for managing grades and streams."""
    error_message = None
    success_message = None
    page = request.args.get('page', 1, type=int)
    per_page = 10  # Number of grades per page

    if request.method == 'POST':
        if "add_grade" in request.form:
            grade_level = request.form.get("grade_level")
            if grade_level:
                existing_grade = Grade.query.filter_by(name=grade_level).first()
                if existing_grade:
                    error_message = f"Grade '{grade_level}' already exists."
                else:
                    # Determine education level based on grade
                    education_level = "lower_primary"
                    if "4" in grade_level or "5" in grade_level or "6" in grade_level:
                        education_level = "upper_primary"
                    elif "7" in grade_level or "8" in grade_level or "9" in grade_level:
                        education_level = "junior_secondary"

                    new_grade = Grade(name=grade_level, education_level=education_level)
                    db.session.add(new_grade)
                    db.session.commit()
                    success_message = f"Grade '{grade_level}' added successfully!"
            else:
                error_message = "Please fill in the grade level."

        elif "add_stream" in request.form:
            stream_name = request.form.get("stream_name")
            grade_id = request.form.get("grade_id")
            if stream_name and grade_id:
                grade = Grade.query.get(grade_id)
                if not grade:
                    error_message = "Selected grade not found."
                else:
                    existing_stream = Stream.query.filter_by(name=stream_name, grade_id=grade_id).first()
                    if existing_stream:
                        error_message = f"Stream '{stream_name}' already exists for Grade {grade.name}."
                    else:
                        new_stream = Stream(name=stream_name, grade_id=grade_id)
                        db.session.add(new_stream)
                        db.session.commit()
                        success_message = f"Stream '{stream_name}' added to Grade {grade.name} successfully!"
            else:
                error_message = "Please fill in all fields."

        elif "delete_grade" in request.form:
            grade_id = request.form.get("grade_id")
            grade = Grade.query.get(grade_id)
            if grade:
                try:
                    # Check if grade has streams
                    if grade.streams:
                        # Delete all streams associated with this grade
                        for stream in grade.streams:
                            # Check if stream has students
                            students = Student.query.filter_by(stream_id=stream.id).all()
                            if students:
                                # Unassign all students from this stream
                                for student in students:
                                    student.stream_id = None

                            # Clean up permissions and assignments for this stream
                            try:
                                from ..models.permission import ClassTeacherPermission
                                from ..models.function_permission import FunctionPermission
                                from ..models.assignment import TeacherSubjectAssignment

                                # Remove permissions for this stream
                                class_permissions = ClassTeacherPermission.query.filter_by(stream_id=stream.id).all()
                                for permission in class_permissions:
                                    db.session.delete(permission)

                                function_permissions = FunctionPermission.query.filter_by(stream_id=stream.id).all()
                                for permission in function_permissions:
                                    db.session.delete(permission)

                                # Remove assignments for this stream
                                assignments = TeacherSubjectAssignment.query.filter_by(stream_id=stream.id).all()
                                for assignment in assignments:
                                    db.session.delete(assignment)

                            except Exception as cleanup_error:
                                print(f"Warning: Could not clean up data for stream {stream.id}: {cleanup_error}")

                            # Delete the stream
                            db.session.delete(stream)

                    # Clean up grade-level permissions and assignments
                    try:
                        from ..models.permission import ClassTeacherPermission
                        from ..models.function_permission import FunctionPermission
                        from ..models.assignment import TeacherSubjectAssignment

                        # Remove grade-level permissions
                        grade_class_permissions = ClassTeacherPermission.query.filter_by(grade_id=grade.id).all()
                        for permission in grade_class_permissions:
                            db.session.delete(permission)

                        grade_function_permissions = FunctionPermission.query.filter_by(grade_id=grade.id).all()
                        for permission in grade_function_permissions:
                            db.session.delete(permission)

                        # Remove grade-level assignments
                        grade_assignments = TeacherSubjectAssignment.query.filter_by(grade_id=grade.id).all()
                        for assignment in grade_assignments:
                            db.session.delete(assignment)

                    except Exception as grade_cleanup_error:
                        print(f"Warning: Could not clean up grade-level data for grade {grade.id}: {grade_cleanup_error}")

                    # Now delete the grade
                    grade_name = grade.name
                    db.session.delete(grade)
                    db.session.commit()
                    success_message = f"Grade '{grade_name}' and all its streams deleted successfully!"

                except Exception as e:
                    db.session.rollback()
                    error_message = f"Error deleting grade: {str(e)}"
                    print(f"Grade deletion error: {e}")
            else:
                error_message = "Grade not found."

        elif "delete_stream" in request.form:
            stream_id = request.form.get("stream_id")
            stream = Stream.query.get(stream_id)
            if stream:
                try:
                    # Check if stream has students
                    students = Student.query.filter_by(stream_id=stream.id).all()
                    if students:
                        # Unassign all students from this stream
                        for student in students:
                            student.stream_id = None

                    # Check and clean up related permissions (if tables exist)
                    permissions_cleaned = 0
                    try:
                        from ..models.permission import ClassTeacherPermission
                        from ..models.function_permission import FunctionPermission

                        # Remove class teacher permissions for this stream
                        class_permissions = ClassTeacherPermission.query.filter_by(stream_id=stream.id).all()
                        for permission in class_permissions:
                            db.session.delete(permission)
                            permissions_cleaned += 1

                        # Remove function permissions for this stream
                        function_permissions = FunctionPermission.query.filter_by(stream_id=stream.id).all()
                        for permission in function_permissions:
                            db.session.delete(permission)
                            permissions_cleaned += 1

                        if permissions_cleaned > 0:
                            print(f"Cleaned up {permissions_cleaned} permissions for stream {stream.id}")

                    except Exception as perm_error:
                        # Permission tables might not exist or have issues, continue anyway
                        print(f"Warning: Could not clean up permissions for stream {stream.id}: {perm_error}")

                    # Check and clean up teacher assignments
                    try:
                        from ..models.assignment import TeacherSubjectAssignment
                        assignments = TeacherSubjectAssignment.query.filter_by(stream_id=stream.id).all()
                        for assignment in assignments:
                            db.session.delete(assignment)
                    except Exception as assign_error:
                        print(f"Warning: Could not clean up assignments for stream {stream.id}: {assign_error}")

                    # Delete the stream
                    stream_name = stream.name
                    grade_name = stream.grade.name if stream.grade else "Unknown"
                    db.session.delete(stream)
                    db.session.commit()
                    success_message = f"Stream '{stream_name}' from Grade {grade_name} deleted successfully!"

                except Exception as e:
                    db.session.rollback()
                    error_message = f"Error deleting stream: {str(e)}"
                    print(f"Stream deletion error: {e}")
            else:
                error_message = "Stream not found."

    # Get all grades with pagination
    grades_query = Grade.query
    grades_page = grades_query.paginate(page=page, per_page=per_page, error_out=False)

    # Get all grades for the form dropdowns (no pagination)
    all_grades = Grade.query.all()

    # Calculate statistics
    total_grades = Grade.query.count()
    total_streams = Stream.query.count()
    avg_streams_per_grade = total_streams / total_grades if total_grades > 0 else 0
    total_students = Student.query.count()

    # Add education level to each grade
    for grade in grades_page.items:
        grade_num = 0
        try:
            # Extract grade number
            if grade.name.startswith("Grade "):
                grade_num = int(grade.name[6:])
            else:
                # Try to extract just the number
                grade_num = int(''.join(filter(str.isdigit, grade.name)))

            # Assign education level
            if 1 <= grade_num <= 3:
                grade.education_level = "lower_primary"
            elif 4 <= grade_num <= 6:
                grade.education_level = "upper_primary"
            elif 7 <= grade_num <= 9:
                grade.education_level = "junior_secondary"
            else:
                grade.education_level = "unknown"
        except:
            grade.education_level = "unknown"

    return render_template(
        "manage_grades_streams.html",
        grades=all_grades,
        grades_page=grades_page,
        total_grades=total_grades,
        total_streams=total_streams,
        avg_streams_per_grade=avg_streams_per_grade,
        total_students=total_students,
        error_message=error_message,
        success_message=success_message
    )

@classteacher_bp.route('/edit_grade', methods=['POST'])
@classteacher_required
def edit_grade():
    """Route for editing a grade."""
    grade_id = request.form.get("grade_id")
    grade_level = request.form.get("grade_level")

    if not grade_id or not grade_level:
        flash("Missing required information.", "error")
        return redirect(url_for('classteacher.manage_grades_streams'))

    grade = Grade.query.get(grade_id)
    if not grade:
        flash("Grade not found.", "error")
        return redirect(url_for('classteacher.manage_grades_streams'))

    # Check if another grade with this level already exists
    existing = Grade.query.filter(Grade.name == grade_level, Grade.id != grade_id).first()
    if existing:
        flash(f"Grade '{grade_level}' already exists.", "error")
        return redirect(url_for('classteacher.manage_grades_streams'))

    old_level = grade.name
    grade.name = grade_level
    db.session.commit()

    flash(f"Grade updated from '{old_level}' to '{grade_level}'.", "success")
    return redirect(url_for('classteacher.manage_grades_streams'))

@classteacher_bp.route('/edit_stream', methods=['POST'])
@classteacher_required
def edit_stream():
    """Route for editing a stream."""
    stream_id = request.form.get("stream_id")
    stream_name = request.form.get("stream_name")
    grade_id = request.form.get("grade_id")

    if not stream_id or not stream_name or not grade_id:
        flash("Missing required information.", "error")
        return redirect(url_for('classteacher.manage_grades_streams'))

    stream = Stream.query.get(stream_id)
    if not stream:
        flash("Stream not found.", "error")
        return redirect(url_for('classteacher.manage_grades_streams'))

    grade = Grade.query.get(grade_id)
    if not grade:
        flash("Grade not found.", "error")
        return redirect(url_for('classteacher.manage_grades_streams'))

    # Check if another stream with this name already exists in the selected grade
    existing = Stream.query.filter(Stream.name == stream_name, Stream.grade_id == grade_id, Stream.id != stream_id).first()
    if existing:
        flash(f"Stream '{stream_name}' already exists for Grade {grade.name}.", "error")
        return redirect(url_for('classteacher.manage_grades_streams'))

    old_name = stream.name
    old_grade = Grade.query.get(stream.grade_id)

    stream.name = stream_name
    stream.grade_id = grade_id
    db.session.commit()

    flash(f"Stream updated from '{old_name}' in {old_grade.name} to '{stream_name}' in {grade.name}.", "success")
    return redirect(url_for('classteacher.manage_grades_streams'))

def cleanup_duplicate_assignments():
    """Utility function to remove duplicate teacher assignments."""
    try:
        from ..models.assignment import TeacherSubjectAssignment
        from ..extensions import db

        # Find and remove duplicate class teacher assignments
        # Group by teacher_id, grade_id, stream_id, is_class_teacher
        duplicates = db.session.query(TeacherSubjectAssignment).filter_by(is_class_teacher=True).all()

        seen_combinations = set()
        to_delete = []

        for assignment in duplicates:
            combination_key = (assignment.teacher_id, assignment.grade_id, assignment.stream_id, assignment.is_class_teacher)

            if combination_key in seen_combinations:
                to_delete.append(assignment)
            else:
                seen_combinations.add(combination_key)

        # Delete duplicates
        for assignment in to_delete:
            db.session.delete(assignment)

        # Find and remove duplicate subject assignments
        subject_duplicates = db.session.query(TeacherSubjectAssignment).filter_by(is_class_teacher=False).all()

        seen_subject_combinations = set()
        subject_to_delete = []

        for assignment in subject_duplicates:
            subject_combination_key = (assignment.teacher_id, assignment.subject_id, assignment.grade_id, assignment.stream_id)

            if subject_combination_key in seen_subject_combinations:
                subject_to_delete.append(assignment)
            else:
                seen_subject_combinations.add(subject_combination_key)

        # Delete subject duplicates
        for assignment in subject_to_delete:
            db.session.delete(assignment)

        db.session.commit()

        total_deleted = len(to_delete) + len(subject_to_delete)
        print(f"Cleaned up {total_deleted} duplicate assignments")
        return total_deleted

    except Exception as e:
        print(f"Error cleaning up duplicates: {str(e)}")
        db.session.rollback()
        return 0

@classteacher_bp.route('/manage_teacher_assignments', methods=['GET', 'POST'])
@classteacher_required(allowed_roles=("classteacher","headteacher","teacher"))
def manage_teacher_assignments():
    """Route for managing teacher assignments, transfers, and reassignments."""
    error_message = None
    success_message = None

    # Clean up any duplicate assignments first
    try:
        duplicates_removed = cleanup_duplicate_assignments()
        if duplicates_removed > 0:
            print(f"Cleaned up {duplicates_removed} duplicate assignments")
    except Exception as e:
        print(f"Error during cleanup: {str(e)}")

    # Get all teachers
    teachers = Teacher.query.all()

    # Get all subjects and grades
    subjects = Subject.query.all()
    grades = Grade.query.all()

    # Determine scope (mine | all). Default is 'mine' for safety.
    scope = request.args.get('scope', 'mine').lower()
    if scope not in ('mine', 'all'):
        scope = 'mine'
    # Authorize access to global scope; only allow privileged roles
    requested_scope = scope
    try:
        user_role = get_role(session)
    except Exception:
        user_role = None
    # Roles allowed to view ALL assignments (add or remove as policy evolves)
    allowed_global_roles = {'classteacher', 'admin', 'superadmin', 'headteacher'}
    if requested_scope == 'all' and user_role not in allowed_global_roles:
        # Fallback silently to 'mine' to avoid information disclosure
        scope = 'mine'

    # Get current teacher context (always needed for 'mine')
    current_teacher_id = session.get('teacher_id')
    current_teacher = Teacher.query.get(current_teacher_id) if current_teacher_id else None

    # Get class teacher assignments respecting scope
    class_teacher_assignments = []
    try:
        if scope == 'all':
            assignments = TeacherSubjectAssignment.query.filter_by(
                is_class_teacher=True
            ).all()
        else:
            assignments = TeacherSubjectAssignment.query.filter_by(
                teacher_id=current_teacher_id,
                is_class_teacher=True
            ).all()

        # Use a set to track unique grade-stream combinations to avoid duplicates
        seen_combinations = set()

        for assignment in assignments:
            teacher = Teacher.query.get(assignment.teacher_id)
            grade = Grade.query.get(assignment.grade_id)
            stream = Stream.query.get(assignment.stream_id) if assignment.stream_id else None

            # Create a unique key for this grade-stream combination
            combination_key = (assignment.grade_id, assignment.stream_id)

            # Skip if we've already seen this combination
            if combination_key in seen_combinations:
                continue

            seen_combinations.add(combination_key)

            # Determine education level based on grade
            education_level = ""
            grade_num = 0
            try:
                # Extract grade number
                if grade.name.startswith("Grade "):
                    grade_num = int(grade.name[6:])
                else:
                    # Try to extract just the number
                    grade_num = int(''.join(filter(str.isdigit, grade.name)))

                # Assign education level
                if 1 <= grade_num <= 3:
                    education_level = "lower_primary"
                elif 4 <= grade_num <= 6:
                    education_level = "upper_primary"
                elif 7 <= grade_num <= 9:
                    education_level = "junior_secondary"
            except:
                education_level = ""

            class_teacher_assignments.append({
                "id": assignment.id,
                "teacher_id": assignment.teacher_id,
                "teacher_username": teacher.username if teacher else "Unknown",
                "grade_id": assignment.grade_id,
                "grade_level": grade.name if grade else "Unknown",
                "stream_id": assignment.stream_id,
                "stream_name": stream.name if stream else None,
                "education_level": education_level
            })
    except Exception as e:
        print(f"Error fetching class teacher assignments: {str(e)}")

    # Get subject assignments respecting scope
    subject_assignments = []
    try:
        if scope == 'all':
            assignments = TeacherSubjectAssignment.query.filter_by(
                is_class_teacher=False
            ).all()
        else:
            assignments = TeacherSubjectAssignment.query.filter_by(
                teacher_id=current_teacher_id,
                is_class_teacher=False
            ).all()

        # Use a set to track unique subject-grade-stream combinations to avoid duplicates
        seen_subject_combinations = set()
        allow_dup_flag = request.args.get('allow_duplicates_for_pagination') == '1'

        for assignment in assignments:
            teacher = Teacher.query.get(assignment.teacher_id)
            subject = Subject.query.get(assignment.subject_id)
            grade = Grade.query.get(assignment.grade_id)
            stream = Stream.query.get(assignment.stream_id) if assignment.stream_id else None

            # Create a unique key for this subject-grade-stream combination
            subject_combination_key = (assignment.subject_id, assignment.grade_id, assignment.stream_id)

            # Skip if we've already seen this combination (unless testing flag bypasses it)
            if not allow_dup_flag:
                if subject_combination_key in seen_subject_combinations:
                    continue
                seen_subject_combinations.add(subject_combination_key)

            subject_assignments.append({
                "id": assignment.id,
                "teacher_id": assignment.teacher_id,
                "teacher_username": teacher.username if teacher else "Unknown",
                "subject_id": assignment.subject_id,
                "subject_name": subject.name if subject else "Unknown",
                "grade_id": assignment.grade_id,
                "grade_level": grade.name if grade else "Unknown",
                "stream_id": assignment.stream_id,
                "stream_name": stream.name if stream else None,
                "education_level": subject.education_level if subject else ""
            })
    except Exception as e:
        print(f"Error fetching subject assignments: {str(e)}")

    # Check if we need to clear session variables after displaying them
    clear_session = False
    if 'assignment_success' in session:
        clear_session = True

    # ---- Pagination Logic ----
    # Query params for pages / per-page (independent for class & subject tables)
    try:
        class_page = int(request.args.get('class_page', 1))
    except ValueError:
        class_page = 1
    try:
        subject_page = int(request.args.get('subject_page', 1))
    except ValueError:
        subject_page = 1
    try:
        per_page_class = int(request.args.get('per_page_class', 10))
    except ValueError:
        per_page_class = 10
    try:
        per_page_subject = int(request.args.get('per_page_subject', 25))
    except ValueError:
        per_page_subject = 25

    # Enforce sensible bounds
    per_page_class = max(1, min(per_page_class, 100))
    per_page_subject = max(1, min(per_page_subject, 200))
    class_page = max(1, class_page)
    subject_page = max(1, subject_page)

    # Sort deterministically (grade, stream, subject, teacher) before slicing
    def natural_teacher_key(username: str):
        if not username:
            return (username, -1)
        if username.startswith('t_'):
            try:
                return ('t_', int(username.split('_', 1)[1]))
            except ValueError:
                return (username, -1)
        return (username, -1)
    def natural_stream_key(stream_name: str):
        if not stream_name:
            return (stream_name, -1)
        # Pattern S<number>
        if stream_name.startswith('S'):
            try:
                return ('S', int(stream_name[1:]))
            except ValueError:
                return (stream_name, -1)
        return (stream_name, -1)

    class_teacher_assignments.sort(key=lambda a: (
        a.get('grade_level') or '',
        natural_stream_key(a.get('stream_name') or ''),
        natural_teacher_key(a.get('teacher_username'))
    ))
    subject_assignments.sort(key=lambda a: (
        a.get('subject_name') or '',
        a.get('grade_level') or '',
        natural_stream_key(a.get('stream_name') or ''),
        natural_teacher_key(a.get('teacher_username'))
    ))

    total_class_assignment_count = len(class_teacher_assignments)
    total_subject_assignment_count = len(subject_assignments)
    total_combined_assignment_count = total_class_assignment_count + total_subject_assignment_count

    def paginate(list_, page, per_page):
        total = len(list_)
        start = (page - 1) * per_page
        end = start + per_page
        items = list_[start:end]
        total_pages = (total + per_page - 1) // per_page if per_page else 1
        return items, {
            'page': page,
            'per_page': per_page,
            'total': total,
            'total_pages': total_pages,
            'has_prev': page > 1,
            'has_next': page < total_pages,
            'prev_page': page - 1 if page > 1 else None,
            'next_page': page + 1 if page < total_pages else None
        }

    class_teacher_assignments_page, class_pagination = paginate(class_teacher_assignments, class_page, per_page_class)
    subject_assignments_page, subject_pagination = paginate(subject_assignments, subject_page, per_page_subject)

    # Global (unfiltered) totals for badge/tooltips (server-side authoritative counts)
    agg_class_total = TeacherSubjectAssignment.query.filter_by(is_class_teacher=True).count()
    agg_subject_total = TeacherSubjectAssignment.query.filter_by(is_class_teacher=False).count()

    response = render_template(
        'manage_teacher_assignments.html',
        teachers=teachers,
        subjects=subjects,
        grades=grades,
        class_teacher_assignments=class_teacher_assignments_page,
        subject_assignments=subject_assignments_page,
        error_message=error_message,
        success_message=success_message,
        view_scope=scope,
        total_class_assignment_count=total_class_assignment_count,
        total_subject_assignment_count=total_subject_assignment_count,
        total_combined_assignment_count=total_combined_assignment_count,
        aggregate_class_count=agg_class_total,
        aggregate_subject_count=agg_subject_total,
        requested_scope=requested_scope,
        user_role=user_role,
        class_pagination=class_pagination,
        subject_pagination=subject_pagination
    )

    # Clear session variables after rendering the template
    if clear_session:
        session.pop('assignment_success', None)
        session.pop('assignment_message', None)
        session.pop('assigned_teacher_id', None)
        session.pop('assignment_count', None)
        session.pop('assignment_timestamp', None)

    return response

@classteacher_bp.route('/reassign_class_teacher', methods=['POST'])
@classteacher_required
def reassign_class_teacher():
    """Route for reassigning a class teacher."""
    assignment_id = request.form.get('assignment_id')
    new_teacher_id = request.form.get('new_teacher_id')

    if not assignment_id or not new_teacher_id:
        flash("Missing required information for reassignment.", "error")
        return redirect(url_for('classteacher.manage_teacher_assignments'))

    try:
        # Get the assignment
        assignment = TeacherSubjectAssignment.query.get(assignment_id)
        if not assignment:
            flash("Assignment not found.", "error")
            return redirect(url_for('classteacher.manage_teacher_assignments'))

        # Get the old and new teachers
        old_teacher = Teacher.query.get(assignment.teacher_id)
        new_teacher = Teacher.query.get(new_teacher_id)

        if not new_teacher:
            flash("New teacher not found.", "error")
            return redirect(url_for('classteacher.manage_teacher_assignments'))

        # Get grade and stream info for the message
        grade = Grade.query.get(assignment.grade_id)
        stream_text = ""
        if assignment.stream_id:
            stream = Stream.query.get(assignment.stream_id)
            stream_text = f" Stream {stream.name}"

        # Update the assignment
        assignment.teacher_id = new_teacher_id
        db.session.commit()

        flash(f"Class teacher for Grade {grade.name}{stream_text} changed from {old_teacher.username} to {new_teacher.username}.", "success")
    except Exception as e:
        print(f"Error reassigning class teacher: {str(e)}")
        flash("Error reassigning class teacher. Please try again.", "error")

    return redirect(url_for('classteacher.manage_teacher_assignments'))

@classteacher_bp.route('/reassign_subject_teacher', methods=['POST'])
@classteacher_required
def reassign_subject_teacher():
    """Route for reassigning a subject teacher."""
    assignment_id = request.form.get('assignment_id')
    new_teacher_id = request.form.get('new_teacher_id')

    if not assignment_id or not new_teacher_id:
        flash("Missing required information for reassignment.", "error")
        return redirect(url_for('classteacher.manage_teacher_assignments'))

    try:
        # Get the assignment
        assignment = TeacherSubjectAssignment.query.get(assignment_id)
        if not assignment:
            flash("Assignment not found.", "error")
            return redirect(url_for('classteacher.manage_teacher_assignments'))

        # Get the old and new teachers
        old_teacher = Teacher.query.get(assignment.teacher_id)
        new_teacher = Teacher.query.get(new_teacher_id)

        if not new_teacher:
            flash("New teacher not found.", "error")
            return redirect(url_for('classteacher.manage_teacher_assignments'))

        # Get subject, grade and stream info for the message
        subject = Subject.query.get(assignment.subject_id)
        grade = Grade.query.get(assignment.grade_id)
        stream_text = ""
        if assignment.stream_id:
            stream = Stream.query.get(assignment.stream_id)
            stream_text = f" Stream {stream.name}"

        # Update the assignment
        assignment.teacher_id = new_teacher_id
        db.session.commit()

        flash(f"Teacher for {subject.name} in Grade {grade.name}{stream_text} changed from {old_teacher.username} to {new_teacher.username}.", "success")
    except Exception as e:
        print(f"Error reassigning subject teacher: {str(e)}")
        flash("Error reassigning subject teacher. Please try again.", "error")

    return redirect(url_for('classteacher.manage_teacher_assignments'))

@classteacher_bp.route('/clear_assignment_session', methods=['POST'])
@classteacher_required
def clear_assignment_session():
    """Clear the assignment session variables."""
    if 'assignment_success' in session:
        session.pop('assignment_success', None)
    if 'assignment_message' in session:
        session.pop('assignment_message', None)
    if 'assigned_teacher_id' in session:
        session.pop('assigned_teacher_id', None)
    return '', 204  # No content response

def _validate_add_term_ajax():
    term_name = request.form.get("term_name")
    if not term_name:
        raise ValidationError('Missing required fields', {'term_name':'required'})
    if Term.query.filter_by(name=term_name).first():
        raise ValidationError('Term name already exists')
    return {
        'term_name': term_name,
        'term_start_date': request.form.get("term_start_date"),
        'term_end_date': request.form.get("term_end_date"),
        'academic_year': request.form.get("academic_year"),
        'is_current_term': "is_current_term" in request.form
    }

@classteacher_bp.route('/add_term_ajax', methods=['POST'])
@secure_endpoint(roles=['headteacher','classteacher'], rate=(25,60), validator=_validate_add_term_ajax, audit_event='term.add')
def add_term_ajax(_validated):
    term_name = _validated['term_name']
    term_start_date = _validated['term_start_date']
    term_end_date = _validated['term_end_date']
    academic_year = _validated['academic_year']
    is_current_term = _validated['is_current_term']

    # Create new term with additional fields if they exist in the model
    new_term = Term(name=term_name)

    # Add additional fields if they exist in the model
    if hasattr(Term, 'start_date') and term_start_date:
        new_term.start_date = term_start_date

    if hasattr(Term, 'end_date') and term_end_date:
        new_term.end_date = term_end_date

    if hasattr(Term, 'academic_year') and academic_year:
        new_term.academic_year = academic_year

    if hasattr(Term, 'is_current'):
        # If setting this term as current, unset any other current terms
        if is_current_term:
            current_terms = Term.query.filter_by(is_current=True).all()
            for term in current_terms:
                term.is_current = False
            new_term.is_current = True

    db.session.add(new_term)
    db.session.commit()

    # Get updated statistics
    terms = Term.query.all()
    assessment_types = AssessmentType.query.all()
    current_term = "None"

    # Find the current term if any
    for term in terms:
        if hasattr(term, 'is_current') and term.is_current:
            current_term = term.name
            break

    # Prepare term data for JSON response
    term_data = {
        "id": new_term.id,
        "name": new_term.name,
        "is_current": True if hasattr(new_term, 'is_current') and new_term.is_current else False,
        "academic_year": new_term.academic_year if hasattr(new_term, 'academic_year') and new_term.academic_year else None
    }

    # Find the current academic year
    current_academic_year = "None"
    for t in terms:
        if hasattr(t, 'is_current') and t.is_current and hasattr(t, 'academic_year') and t.academic_year:
            current_academic_year = t.academic_year
            break

    # If no current term has an academic year, try to get the academic year from any term
    if current_academic_year == "None":
        for t in terms:
            if hasattr(t, 'academic_year') and t.academic_year:
                current_academic_year = t.academic_year
                break

    # Prepare statistics for JSON response
    stats = {
        "total_terms": len(terms),
        "total_assessments": len(assessment_types),
        "current_term": current_term,
        "current_academic_year": current_academic_year
    }

    return jsonify({
        "success": True,
        "message": f"Term '{term_name}' added successfully!",
        "term": term_data,
        "stats": stats
    })

def _validate_delete_term_ajax():
    term_id = request.form.get("term_id")
    if not term_id:
        raise ValidationError('Missing required fields', {'term_id': 'required'})
    term = Term.query.get(term_id)
    if not term:
        raise ValidationError('Term not found')
    # Check if term has marks
    marks = Mark.query.filter_by(term_id=term.id).all()
    if marks:
        raise ValidationError("Term has marks and cannot be deleted", {'blocked': True})
    return {'term': term}

@classteacher_bp.route('/delete_term_ajax', methods=['POST'])
@secure_endpoint(roles=['headteacher','classteacher'], rate=(30,60), validator=_validate_delete_term_ajax, audit_event='term.delete')
def delete_term_ajax(_validated):
    term = _validated['term']
    term_name = term.name
    db.session.delete(term)
    db.session.commit()

    terms = Term.query.all()
    assessment_types = AssessmentType.query.all()
    current_term = "None"
    for t in terms:
        if hasattr(t, 'is_current') and t.is_current:
            current_term = t.name
            break
    stats = {
        "total_terms": len(terms),
        "total_assessments": len(assessment_types),
        "current_term": current_term
    }
    return jsonify({
        "success": True,
        "message": f"Term '{term_name}' deleted successfully!",
        "stats": stats
    })

def _validate_delete_assessment_ajax():
    assessment_id = request.form.get("assessment_id")
    if not assessment_id:
        raise ValidationError('Missing required fields', {'assessment_id':'required'})
    return {
        'assessment_id': assessment_id,
        'force_delete': request.form.get("force_delete") == "true"
    }

@classteacher_bp.route('/delete_assessment_ajax', methods=['POST'])
@secure_endpoint(roles=['headteacher','classteacher'], rate=(30,60), validator=_validate_delete_assessment_ajax, audit_event='assessment.delete')
def delete_assessment_ajax(_validated):
    assessment_id = _validated['assessment_id']
    force_delete = _validated['force_delete']
    assessment = AssessmentType.query.get(assessment_id)
    if not assessment:
        return jsonify({"success": False, "message": "Assessment type not found."})

    # Check if assessment type has marks
    marks = Mark.query.filter_by(assessment_type_id=assessment.id).all()

    # Check if user is headteacher (has universal access)
    is_headteacher = session.get('headteacher_universal_access') or session.get('role') == 'headteacher'

    if marks and not (is_headteacher and force_delete):
        if is_headteacher:
            return jsonify({
                "success": False,
                "message": f"Assessment type '{assessment.name}' has {len(marks)} marks associated with it. Use force delete to remove it along with all marks.",
                "has_marks": True,
                "marks_count": len(marks)
            })
        else:
            return jsonify({"success": False, "message": f"Cannot delete assessment type '{assessment.name}' because it has marks associated with it."})

    try:
        # Store the name for the success message
        assessment_name = assessment.name
        marks_count = len(marks)

        # If force delete and has marks, delete all associated marks first
        if marks and is_headteacher and force_delete:
            for mark in marks:
                db.session.delete(mark)

        # Delete the assessment type
        db.session.delete(assessment)
        db.session.commit()

        # Prepare success message
        if marks and force_delete:
            success_message = f"Assessment type '{assessment_name}' and {marks_count} associated marks deleted successfully!"
        else:
            success_message = f"Assessment type '{assessment_name}' deleted successfully!"

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error deleting assessment type: {str(e)}"})

    # Get updated statistics
    terms = Term.query.all()
    assessment_types = AssessmentType.query.all()
    current_term = "None"

    # Find the current term if any
    for term in terms:
        if hasattr(term, 'is_current') and term.is_current:
            current_term = term.name
            break

    # Prepare statistics for JSON response
    stats = {
        "total_terms": len(terms),
        "total_assessments": len(assessment_types),
        "current_term": current_term
    }

    return jsonify({
        "success": True,
        "message": success_message,
        "stats": stats
    })

def _validate_edit_term_ajax():
    term_id = request.form.get("term_id")
    term_name = request.form.get("term_name")
    if not term_id or not term_name:
        missing = {}
        if not term_id: missing['term_id'] = 'required'
        if not term_name: missing['term_name'] = 'required'
        raise ValidationError('Missing required fields', missing)
    term = Term.query.get(term_id)
    if not term:
        raise ValidationError('Term not found')
    existing_term = Term.query.filter(
        Term.name == term_name,
        Term.id != term.id
    ).first()
    if existing_term:
        raise ValidationError('Term name already exists')
    return {
        'term': term,
        'term_name': term_name,
        'term_start_date': request.form.get("term_start_date"),
        'term_end_date': request.form.get("term_end_date"),
        'academic_year': request.form.get("academic_year"),
        'is_current_term': "is_current_term" in request.form
    }

@classteacher_bp.route('/edit_term_ajax', methods=['POST'])
@secure_endpoint(roles=['headteacher','classteacher'], rate=(30,60), validator=_validate_edit_term_ajax, audit_event='term.edit')
def edit_term_ajax(_validated):
    term = _validated['term']
    term_name = _validated['term_name']
    term_start_date = _validated['term_start_date']
    term_end_date = _validated['term_end_date']
    academic_year = _validated['academic_year']
    is_current_term = _validated['is_current_term']

    # Update the term
    term.name = term_name

    # Update additional fields if they exist in the model
    if hasattr(Term, 'start_date'):
        term.start_date = term_start_date if term_start_date else None

    if hasattr(Term, 'end_date'):
        term.end_date = term_end_date if term_end_date else None

    if hasattr(Term, 'academic_year'):
        term.academic_year = academic_year if academic_year else None

    if hasattr(Term, 'is_current'):
        # If setting this term as current, unset any other current terms
        if is_current_term:
            current_terms = Term.query.filter(Term.id != term.id).all()
            for t in current_terms:
                if hasattr(t, 'is_current'):
                    t.is_current = False
            term.is_current = True
        else:
            term.is_current = False

    db.session.commit()

    # Get updated statistics
    terms = Term.query.all()
    assessment_types = AssessmentType.query.all()
    current_term = "None"

    # Find the current term if any
    for t in terms:
        if hasattr(t, 'is_current') and t.is_current:
            current_term = t.name
            break

    # Prepare term data for JSON response
    term_data = {
        "id": term.id,
        "name": term.name,
        "is_current": True if hasattr(term, 'is_current') and term.is_current else False,
        "academic_year": term.academic_year if hasattr(term, 'academic_year') and term.academic_year else None
    }

    # Find the current academic year
    current_academic_year = "None"
    for t in terms:
        if hasattr(t, 'is_current') and t.is_current and hasattr(t, 'academic_year') and t.academic_year:
            current_academic_year = t.academic_year
            break

    # If no current term has an academic year, try to get the academic year from any term
    if current_academic_year == "None":
        for t in terms:
            if hasattr(t, 'academic_year') and t.academic_year:
                current_academic_year = t.academic_year
                break

    # Prepare statistics for JSON response
    stats = {
        "total_terms": len(terms),
        "total_assessments": len(assessment_types),
        "current_term": current_term,
        "current_academic_year": current_academic_year
    }

    return jsonify({
        "success": True,
        "message": f"Term '{term_name}' updated successfully!",
        "term": term_data,
        "stats": stats
    })

def _validate_edit_assessment_ajax():
    assessment_id = request.form.get("assessment_id")
    assessment_name = request.form.get("assessment_name")
    if not assessment_id or not assessment_name:
        missing = {}
        if not assessment_id: missing['assessment_id'] = 'required'
        if not assessment_name: missing['assessment_name'] = 'required'
        raise ValidationError('Missing required fields', missing)
    assessment = AssessmentType.query.get(assessment_id)
    if not assessment:
        raise ValidationError('Assessment type not found')
    existing_assessment = AssessmentType.query.filter(
        AssessmentType.name == assessment_name,
        AssessmentType.id != assessment.id
    ).first()
    if existing_assessment:
        raise ValidationError('Assessment type already exists')
    assessment_weight = request.form.get("assessment_weight")
    assessment_group = request.form.get("assessment_group")
    show_on_reports = "show_on_reports" in request.form
    return {
        'assessment': assessment,
        'assessment_name': assessment_name,
        'assessment_weight': assessment_weight,
        'assessment_group': assessment_group,
        'show_on_reports': show_on_reports
    }

@classteacher_bp.route('/edit_assessment_ajax', methods=['POST'])
@secure_endpoint(roles=['headteacher','classteacher'], rate=(30,60), validator=_validate_edit_assessment_ajax, audit_event='assessment.edit')
def edit_assessment_ajax(_validated):
    assessment = _validated['assessment']
    assessment_name = _validated['assessment_name']
    assessment_weight = _validated['assessment_weight']
    assessment_group = _validated['assessment_group']
    show_on_reports = _validated['show_on_reports']
    assessment.name = assessment_name
    if hasattr(AssessmentType, 'weight'):
        assessment.weight = assessment_weight if assessment_weight else None
    if hasattr(AssessmentType, 'group'):
        assessment.group = assessment_group if assessment_group else None
    if hasattr(AssessmentType, 'show_on_reports'):
        assessment.show_on_reports = show_on_reports
    db.session.commit()
    terms = Term.query.all()
    assessment_types = AssessmentType.query.all()
    current_term = "None"
    for term in terms:
        if hasattr(term, 'is_current') and term.is_current:
            current_term = term.name
            break
    assessment_data = {
        "id": assessment.id,
        "name": assessment.name,
        "weight": assessment.weight if hasattr(assessment, 'weight') and assessment.weight else None
    }
    stats = {
        "total_terms": len(terms),
        "total_assessments": len(assessment_types),
        "current_term": current_term
    }
    return jsonify({
        "success": True,
        "message": f"Assessment type '{assessment_name}' updated successfully!",
        "assessment": assessment_data,
        "stats": stats
    })

def _validate_add_assessment_ajax():
    assessment_name = request.form.get("assessment_name")
    if not assessment_name:
        raise ValidationError('Missing required fields', {'assessment_name':'required'})
    existing_assessment = AssessmentType.query.filter_by(name=assessment_name).first()
    if existing_assessment:
        raise ValidationError('Assessment type already exists')
    assessment_weight = request.form.get("assessment_weight")
    weight_value = 100.0
    if assessment_weight:
        try:
            weight_value = float(assessment_weight)
        except (ValueError, TypeError):
            weight_value = 100.0
    return {
        'assessment_name': assessment_name,
        'weight_value': weight_value,
        'assessment_group': request.form.get("assessment_group"),
        'show_on_reports': "show_on_reports" in request.form
    }

@classteacher_bp.route('/add_assessment_ajax', methods=['POST'])
@secure_endpoint(roles=['headteacher','classteacher'], rate=(30,60), validator=_validate_add_assessment_ajax, audit_event='assessment.add')
def add_assessment_ajax(_validated):
    assessment_name = _validated['assessment_name']
    weight_value = _validated['weight_value']
    assessment_group = _validated['assessment_group']
    show_on_reports = _validated['show_on_reports']
    try:
        new_assessment = AssessmentType(
            name=assessment_name,
            weight=weight_value,
            category=assessment_group if assessment_group else 'General'
        )
        if hasattr(AssessmentType, 'show_on_reports'):
            new_assessment.show_on_reports = show_on_reports
        db.session.add(new_assessment)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error creating assessment type: {str(e)}"})

    terms = Term.query.all()
    assessment_types = AssessmentType.query.all()
    current_term = "None"
    for term in terms:
        if hasattr(term, 'is_current') and term.is_current:
            current_term = term.name
            break
    assessment_data = {
        "id": new_assessment.id,
        "name": new_assessment.name,
        "weight": new_assessment.weight if hasattr(new_assessment, 'weight') and new_assessment.weight else None
    }
    stats = {
        "total_terms": len(terms),
        "total_assessments": len(assessment_types),
        "current_term": current_term
    }
    return jsonify({
        "success": True,
        "message": f"Assessment type '{assessment_name}' added successfully!",
        "assessment": assessment_data,
        "stats": stats
    })

@classteacher_bp.route('/manage_terms_assessments', methods=['GET', 'POST'])
@classteacher_required
def manage_terms_assessments():
    """Route for managing terms and assessment types."""
    error_message = None
    success_message = None

    # Get all terms and assessment types
    terms = Term.query.order_by(Term.id).all()
    assessment_types = AssessmentType.query.order_by(AssessmentType.id).all()

    # Set default values for statistics
    current_academic_year = "None"
    current_term = "None"

    # Find the current term and academic year if any
    for term in terms:
        if hasattr(term, 'is_current') and term.is_current:
            current_term = term.name
            if hasattr(term, 'academic_year') and term.academic_year:
                current_academic_year = term.academic_year
            break

    # If no current term has an academic year, try to get the academic year from any term
    if current_academic_year == "None":
        for term in terms:
            if hasattr(term, 'academic_year') and term.academic_year:
                current_academic_year = term.academic_year
                break

    if request.method == 'POST':
        action = request.form.get("action")

        if "add_term" in request.form:
            term_name = request.form.get("term_name")
            term_start_date = request.form.get("term_start_date")
            term_end_date = request.form.get("term_end_date")
            academic_year = request.form.get("academic_year")
            is_current_term = "is_current_term" in request.form

            if term_name:
                existing_term = Term.query.filter_by(name=term_name).first()
                if existing_term:
                    error_message = f"Term '{term_name}' already exists."
                else:
                    # Create new term with additional fields if they exist in the model
                    new_term = Term(name=term_name)

                    # Add additional fields if they exist in the model
                    if hasattr(Term, 'start_date') and term_start_date:
                        new_term.start_date = term_start_date

                    if hasattr(Term, 'end_date') and term_end_date:
                        new_term.end_date = term_end_date

                    if hasattr(Term, 'academic_year') and academic_year:
                        new_term.academic_year = academic_year

                    if hasattr(Term, 'is_current'):
                        # If setting this term as current, unset any other current terms
                        if is_current_term:
                            for term in terms:
                                if hasattr(term, 'is_current'):
                                    term.is_current = False
                            new_term.is_current = True

                    db.session.add(new_term)
                    db.session.commit()
                    success_message = f"Term '{term_name}' added successfully!"
            else:
                error_message = "Please fill in the term name."

        elif "add_assessment" in request.form:
            assessment_name = request.form.get("assessment_name")
            assessment_weight = request.form.get("assessment_weight")
            assessment_group = request.form.get("assessment_group")
            show_on_reports = "show_on_reports" in request.form

            if assessment_name:
                existing_assessment = AssessmentType.query.filter_by(name=assessment_name).first()
                if existing_assessment:
                    error_message = f"Assessment type '{assessment_name}' already exists."
                else:
                    # Create new assessment with proper field handling
                    try:
                        # Convert weight to float if provided, otherwise use default
                        weight_value = None
                        if assessment_weight:
                            try:
                                weight_value = float(assessment_weight)
                            except (ValueError, TypeError):
                                weight_value = 100.0  # Default weight
                        else:
                            weight_value = 100.0  # Default weight if not provided

                        new_assessment = AssessmentType(
                            name=assessment_name,
                            weight=weight_value,
                            category=assessment_group if assessment_group else 'General'
                        )

                        # Add additional fields if they exist in the model
                        if hasattr(AssessmentType, 'show_on_reports'):
                            new_assessment.show_on_reports = show_on_reports

                        db.session.add(new_assessment)
                        db.session.commit()
                        success_message = f"Assessment type '{assessment_name}' added successfully!"
                    except Exception as e:
                        db.session.rollback()
                        error_message = f"Error creating assessment type: {str(e)}"
            else:
                error_message = "Please fill in the assessment type name."

        elif "delete_term" in request.form:
            term_id = request.form.get("term_id")
            term = Term.query.get(term_id)
            if term:
                # Check if term has marks
                marks = Mark.query.filter_by(term_id=term.id).all()
                if marks:
                    error_message = f"Cannot delete term '{term.name}' because it has marks associated with it."
                else:
                    db.session.delete(term)
                    db.session.commit()
                    success_message = f"Term '{term.name}' deleted successfully!"
            else:
                error_message = "Term not found."

        elif "delete_assessment" in request.form:
            assessment_id = request.form.get("assessment_id")
            force_delete = request.form.get("force_delete") == "true"
            assessment = AssessmentType.query.get(assessment_id)
            if assessment:
                # Check if assessment type has marks
                marks = Mark.query.filter_by(assessment_type_id=assessment.id).all()

                # Check if user is headteacher (has universal access)
                is_headteacher = session.get('headteacher_universal_access') or session.get('role') == 'headteacher'

                if marks and not (is_headteacher and force_delete):
                    if is_headteacher:
                        error_message = f"Assessment type '{assessment.name}' has {len(marks)} marks associated with it. Use force delete to remove it along with all marks."
                    else:
                        error_message = f"Cannot delete assessment type '{assessment.name}' because it has marks associated with it."
                else:
                    try:
                        # If force delete and has marks, delete all associated marks first
                        if marks and is_headteacher and force_delete:
                            for mark in marks:
                                db.session.delete(mark)

                        db.session.delete(assessment)
                        db.session.commit()

                        if marks and force_delete:
                            success_message = f"Assessment type '{assessment.name}' and {len(marks)} associated marks deleted successfully!"
                        else:
                            success_message = f"Assessment type '{assessment.name}' deleted successfully!"
                    except Exception as e:
                        db.session.rollback()
                        error_message = f"Error deleting assessment type: {str(e)}"
            else:
                error_message = "Assessment type not found."

        elif action == "edit_term":
            term_id = request.form.get("term_id")
            term_name = request.form.get("term_name")
            term_start_date = request.form.get("term_start_date")
            term_end_date = request.form.get("term_end_date")
            academic_year = request.form.get("academic_year")
            is_current_term = "is_current_term" in request.form

            if not term_id or not term_name:
                error_message = "Missing required information."
            else:
                term = Term.query.get(term_id)
                if not term:
                    error_message = "Term not found."
                else:
                    # Check if another term with the same name already exists
                    existing_term = Term.query.filter(
                        Term.name == term_name,
                        Term.id != term.id
                    ).first()

                    if existing_term:
                        error_message = f"Term '{term_name}' already exists."
                    else:
                        # Update the term
                        term.name = term_name

                        # Update additional fields if they exist in the model
                        if hasattr(Term, 'start_date'):
                            term.start_date = term_start_date if term_start_date else None

                        if hasattr(Term, 'end_date'):
                            term.end_date = term_end_date if term_end_date else None

                        if hasattr(Term, 'academic_year'):
                            term.academic_year = academic_year if academic_year else None

                        if hasattr(Term, 'is_current'):
                            # If setting this term as current, unset any other current terms
                            if is_current_term:
                                for t in terms:
                                    if hasattr(t, 'is_current'):
                                        t.is_current = (t.id == term.id)
                            else:
                                term.is_current = False

                        db.session.commit()
                        success_message = f"Term '{term_name}' updated successfully!"

        elif action == "edit_assessment":
            assessment_id = request.form.get("assessment_id")
            assessment_name = request.form.get("assessment_name")
            assessment_weight = request.form.get("assessment_weight")
            assessment_group = request.form.get("assessment_group")
            show_on_reports = "show_on_reports" in request.form

            if not assessment_id or not assessment_name:
                error_message = "Missing required information."
            else:
                assessment = AssessmentType.query.get(assessment_id)
                if not assessment:
                    error_message = "Assessment type not found."
                else:
                    # Check if another assessment with the same name already exists
                    existing_assessment = AssessmentType.query.filter(
                        AssessmentType.name == assessment_name,
                        AssessmentType.id != assessment.id
                    ).first()

                    if existing_assessment:
                        error_message = f"Assessment type '{assessment_name}' already exists."
                    else:
                        # Update the assessment
                        assessment.name = assessment_name

                        # Update additional fields if they exist in the model
                        if hasattr(AssessmentType, 'weight'):
                            assessment.weight = assessment_weight if assessment_weight else None

                        if hasattr(AssessmentType, 'group'):
                            assessment.group = assessment_group if assessment_group else None

                        if hasattr(AssessmentType, 'show_on_reports'):
                            assessment.show_on_reports = show_on_reports

                        db.session.commit()
                        success_message = f"Assessment type '{assessment_name}' updated successfully!"

    # Add is_current attribute to terms if it doesn't exist in the model
    for term in terms:
        if not hasattr(term, 'is_current'):
            # Check if this is the only term or if it's the latest term
            if len(terms) == 1 or term.id == max(t.id for t in terms):
                term.is_current = True
            else:
                term.is_current = False

    # Add weight attribute to assessment types if it doesn't exist in the model
    for assessment in assessment_types:
        if not hasattr(assessment, 'weight'):
            assessment.weight = None

    return render_template(
        'manage_terms_assessments.html',
        terms=terms,
        assessment_types=assessment_types,
        current_academic_year=current_academic_year,
        current_term=current_term,
        error_message=error_message,
        success_message=success_message
    )

@classteacher_bp.route('/manage_teacher_subjects/<int:teacher_id>', methods=['GET', 'POST'])
@classteacher_required
def manage_teacher_subjects(teacher_id):
    """Route for managing a teacher's subject assignments."""
    # Check for highlight parameter
    highlight = request.args.get('highlight', '0') == '1'
    error_message = None
    success_message = None

    # Get the teacher
    teacher = Teacher.query.get(teacher_id)
    if not teacher:
        flash("Teacher not found.", "error")
        return redirect(url_for('classteacher.manage_teacher_assignments'))

    # Get all subjects, grades, and streams
    subjects = Subject.query.all()
    grades = Grade.query.all()
    streams = Stream.query.all()

    # Get the teacher's subject assignments
    subject_assignments = []
    try:
        assignments = TeacherSubjectAssignment.query.filter_by(
            teacher_id=teacher_id,
            is_class_teacher=False
        ).all()

        # Check if we're coming from a bulk assignment or if highlight parameter is set
        highlight_new = False
        if (session.get('assignment_success') and str(session.get('assigned_teacher_id')) == str(teacher_id)) or highlight:
            highlight_new = True

        for assignment in assignments:
            subject = Subject.query.get(assignment.subject_id)
            grade = Grade.query.get(assignment.grade_id)
            stream = Stream.query.get(assignment.stream_id) if assignment.stream_id else None

            # Determine if this is a new assignment to highlight
            is_new = False
            if highlight_new:
                # In a real application, you would check the creation timestamp
                # Here we'll just mark all as potentially new
                is_new = True

            subject_assignments.append({
                "id": assignment.id,
                "subject_id": assignment.subject_id,
                "subject_name": subject.name if subject else "Unknown",
                "grade_id": assignment.grade_id,
                "grade_level": grade.name if grade else "Unknown",
                "stream_id": assignment.stream_id,
                "stream_name": stream.name if stream else None,
                "education_level": subject.education_level if subject else "",
                "is_new": is_new
            })
    except Exception as e:
        print(f"Error fetching subject assignments: {str(e)}")

    # Handle form submissions
    if request.method == 'POST':
        # Remove subject assignment
        if "remove_subject" in request.form:
            assignment_id = request.form.get("assignment_id")

            try:
                assignment = TeacherSubjectAssignment.query.get(assignment_id)
                if assignment and assignment.teacher_id == teacher_id:
                    subject = Subject.query.get(assignment.subject_id)
                    grade = Grade.query.get(assignment.grade_id)
                    stream = Stream.query.get(assignment.stream_id) if assignment.stream_id else None

                    stream_text = f" Stream {stream.name}" if stream else ""

                    db.session.delete(assignment)
                    db.session.commit()

                    success_message = f"Removed {subject.name} assignment for Grade {grade.name}{stream_text}."
                else:
                    error_message = "Assignment not found or does not belong to this teacher."
            except Exception as e:
                print(f"Error removing subject assignment: {str(e)}")
                error_message = "Error removing subject assignment."

        # Change subject assignment
        elif "change_subject" in request.form:
            assignment_id = request.form.get("change_assignment_id")
            new_subject_id = request.form.get("new_subject_id")

            if not assignment_id or not new_subject_id:
                error_message = "Missing required information."
            else:
                try:
                    assignment = TeacherSubjectAssignment.query.get(assignment_id)
                    if assignment and assignment.teacher_id == teacher_id:
                        old_subject = Subject.query.get(assignment.subject_id)
                        new_subject = Subject.query.get(new_subject_id)
                        grade = Grade.query.get(assignment.grade_id)
                        stream = Stream.query.get(assignment.stream_id) if assignment.stream_id else None

                        stream_text = f" Stream {stream.name}" if stream else ""

                        assignment.subject_id = new_subject_id
                        db.session.commit()

                        success_message = f"Changed subject from {old_subject.name} to {new_subject.name} for Grade {grade.name}{stream_text}."
                    else:
                        error_message = "Assignment not found or does not belong to this teacher."
                except Exception as e:
                    print(f"Error changing subject assignment: {str(e)}")
                    error_message = "Error changing subject assignment."

        # Add new subject assignments
        elif "add_subjects" in request.form:
            education_level = request.form.get("education_level")
            grade_id = request.form.get("grade_id")
            stream_id = request.form.get("stream_id") or None
            subject_ids = request.form.getlist("subject_ids")

            if not education_level or not grade_id or not subject_ids:
                error_message = "Please select education level, grade, and at least one subject."
            else:
                try:
                    grade = Grade.query.get(grade_id)
                    stream = Stream.query.get(stream_id) if stream_id else None

                    stream_text = f" Stream {stream.name}" if stream else ""
                    added_count = 0

                    for subject_id in subject_ids:
                        # Check if assignment already exists
                        existing = TeacherSubjectAssignment.query.filter_by(
                            teacher_id=teacher_id,
                            subject_id=subject_id,
                            grade_id=grade_id,
                            stream_id=stream_id,
                            is_class_teacher=False
                        ).first()

                        if not existing:
                            new_assignment = TeacherSubjectAssignment(
                                teacher_id=teacher_id,
                                subject_id=subject_id,
                                grade_id=grade_id,
                                stream_id=stream_id,
                                is_class_teacher=False
                            )
                            db.session.add(new_assignment)
                            added_count += 1

                    if added_count > 0:
                        db.session.commit()
                        success_message = f"Added {added_count} subject assignments for Grade {grade.name}{stream_text}."
                    else:
                        error_message = "No new assignments added. They may already exist."
                except Exception as e:
                    print(f"Error adding subject assignments: {str(e)}")
                    error_message = "Error adding subject assignments."

    return render_template(
        'manage_teacher_subjects.html',
        teacher=teacher,
        subjects=subjects,
        grades=grades,
        streams=streams,
        subject_assignments=subject_assignments,
        error_message=error_message,
        success_message=success_message
    )

@classteacher_bp.route('/bulk_transfer_assignments', methods=['POST'])
@classteacher_required
def bulk_transfer_assignments():
    """Route for bulk transferring assignments from one teacher to another."""
    from_teacher_id = request.form.get('from_teacher_id')
    to_teacher_id = request.form.get('to_teacher_id')
    transfer_class_teacher = 'transfer_class_teacher' in request.form
    transfer_subject_assignments = 'transfer_subject_assignments' in request.form
    # New: optional explicit selection list (comma separated IDs) for granular transfer
    raw_selected_ids = request.form.get('selected_assignment_ids', '').strip()
    selected_ids = []
    if raw_selected_ids:
        for part in raw_selected_ids.split(','):
            part = part.strip()
            if part.isdigit():
                selected_ids.append(int(part))

    if not from_teacher_id or not to_teacher_id:
        flash("Please select both source and destination teachers.", "error")
        return redirect(url_for('classteacher.manage_teacher_assignments'))

    if from_teacher_id == to_teacher_id:
        flash("Source and destination teachers cannot be the same.", "error")
        return redirect(url_for('classteacher.manage_teacher_assignments'))

    # If no broad category flags and no explicit IDs, reject
    if not transfer_class_teacher and not transfer_subject_assignments and not selected_ids:
        flash("Please select at least one type of assignment or specific assignments to transfer.", "error")
        return redirect(url_for('classteacher.manage_teacher_assignments'))

    try:
        # Get the teachers
        from_teacher = Teacher.query.get(from_teacher_id)
        to_teacher = Teacher.query.get(to_teacher_id)

        if not from_teacher or not to_teacher:
            flash("One or both teachers not found.", "error")
            return redirect(url_for('classteacher.manage_teacher_assignments'))

        # Track statistics
        class_teacher_count = 0
        subject_count = 0
        skipped_duplicates = 0

        # Helper: determine if duplicate exists already for destination
        def already_exists(a: TeacherSubjectAssignment):
            return TeacherSubjectAssignment.query.filter_by(
                teacher_id=to_teacher_id,
                grade_id=a.grade_id,
                stream_id=a.stream_id,
                subject_id=a.subject_id,
                is_class_teacher=a.is_class_teacher
            ).first() is not None

        # If explicit selection provided, ignore broad flags and move only those
        if selected_ids:
            assignments = TeacherSubjectAssignment.query.filter(
                TeacherSubjectAssignment.teacher_id==from_teacher_id,
                TeacherSubjectAssignment.id.in_(selected_ids)
            ).all()
            for assignment in assignments:
                if already_exists(assignment):
                    skipped_duplicates += 1
                    continue
                if assignment.is_class_teacher:
                    class_teacher_count += 1
                else:
                    subject_count += 1
                assignment.teacher_id = to_teacher_id
        else:
            # Transfer class teacher assignments if selected
            if transfer_class_teacher:
                class_teacher_assignments = TeacherSubjectAssignment.query.filter_by(
                    teacher_id=from_teacher_id,
                    is_class_teacher=True
                ).all()
                for assignment in class_teacher_assignments:
                    if already_exists(assignment):
                        skipped_duplicates += 1
                        continue
                    assignment.teacher_id = to_teacher_id
                    class_teacher_count += 1
            # Transfer subject assignments if selected
            if transfer_subject_assignments:
                subject_assignments = TeacherSubjectAssignment.query.filter_by(
                    teacher_id=from_teacher_id,
                    is_class_teacher=False
                ).all()
                for assignment in subject_assignments:
                    if already_exists(assignment):
                        skipped_duplicates += 1
                        continue
                    assignment.teacher_id = to_teacher_id
                    subject_count += 1

        # Commit the changes
        db.session.commit()

        # Create success message
        message = f"Transferred from {from_teacher.username} to {to_teacher.username}: "
        parts = []
        if class_teacher_count:
            parts.append(f"{class_teacher_count} class teacher")
        if subject_count:
            parts.append(f"{subject_count} subject")
        if not parts:
            parts.append("0 assignments")
        message += ", ".join(parts)
        if skipped_duplicates:
            message += f" (skipped {skipped_duplicates} duplicates)"

        flash(message, "success")
    except Exception as e:
        print(f"Error transferring assignments: {str(e)}")
        flash("Error transferring assignments. Please try again.", "error")

    return redirect(url_for('classteacher.manage_teacher_assignments'))

@classteacher_bp.route('/api/teacher_assignments/<int:teacher_id>')
@classteacher_required
def api_teacher_assignments(teacher_id):
    """Return JSON of a teacher's assignments for granular bulk transfer UI."""
    assignments = TeacherSubjectAssignment.query.filter_by(teacher_id=teacher_id).all()
    data = []
    for a in assignments:
        grade = Grade.query.get(a.grade_id) if a.grade_id else None
        stream = Stream.query.get(a.stream_id) if a.stream_id else None
        subject = Subject.query.get(a.subject_id) if a.subject_id else None
        data.append({
            'id': a.id,
            'grade_id': a.grade_id,
            'grade_name': grade.name if grade else None,
            'stream_id': a.stream_id,
            'stream_name': stream.name if stream else None,
            'subject_id': a.subject_id,
            'subject_name': subject.name if subject else None,
            'is_class_teacher': a.is_class_teacher
        })
    return jsonify(data)

@classteacher_bp.route('/teacher_management_hub', methods=['GET'])
@classteacher_required
def teacher_management_hub():
    """Route for the Teacher Management Hub - central entry point."""
    from ..models.assignment import TeacherSubjectAssignment

    # Get statistics
    total_teachers = Teacher.query.count()
    total_assignments = TeacherSubjectAssignment.query.count()
    class_teachers = TeacherSubjectAssignment.query.filter_by(is_class_teacher=True).count()
    subject_assignments = TeacherSubjectAssignment.query.filter_by(is_class_teacher=False).count()

    # Sample recent activities (you can expand this with real data)
    recent_activities = [
        {
            'icon': '👨‍🏫',
            'text': 'New teacher profile created',
            'time': '2 hours ago'
        },
        {
            'icon': '📚',
            'text': 'Subject assignment updated',
            'time': '4 hours ago'
        },
        {
            'icon': '🔄',
            'text': 'Teacher reassignment completed',
            'time': '1 day ago'
        }
    ]

    return render_template(
        'teacher_management_hub.html',
        total_teachers=total_teachers,
        total_assignments=total_assignments,
        class_teachers=class_teachers,
        subject_assignments=subject_assignments,
        recent_activities=recent_activities
    )

@classteacher_bp.route('/manage_teachers', methods=['GET', 'POST'])
@classteacher_required
def manage_teachers():
    """Route for managing teachers."""
    # Import TeacherSubjectAssignment at the beginning
    from ..models.assignment import TeacherSubjectAssignment

    error_message = None
    success_message = None

    # Get the current teacher
    teacher_id = session.get('teacher_id')
    current_teacher = Teacher.query.get(teacher_id)

    # Get teacher's subject assignments and class teacher status
    try:
        teacher_assignments = TeacherSubjectAssignment.query.filter_by(teacher_id=current_teacher.id).all()

        # Get class teacher assignments
        class_teacher_assignments = [assignment for assignment in teacher_assignments if assignment.is_class_teacher]

        # Get subject assignments
        subject_assignments = []
        for assignment in teacher_assignments:
            subject_name = assignment.subject.name
            grade_level = assignment.grade.name
            stream_name = assignment.stream.name if assignment.stream else "All Streams"
            subject_assignments.append({
                "subject": subject_name,
                "grade": grade_level,
                "stream": stream_name
            })
    except Exception as e:
        # If there's an error (like the table doesn't exist), use empty lists
        print(f"Error fetching teacher assignments: {str(e)}")
        teacher_assignments = []
        class_teacher_assignments = []
        subject_assignments = []

    # Get all teachers with their assignment data
    teachers = []
    all_teachers = Teacher.query.all()

    for teacher in all_teachers:
        # Get teacher's assignments
        teacher_assignments = TeacherSubjectAssignment.query.filter_by(teacher_id=teacher.id).all()

        # Get subjects taught by this teacher
        subjects_taught = []
        class_assignments = []
        subject_assignments = []

        for assignment in teacher_assignments:
            if assignment.is_class_teacher:
                # Class teacher assignment
                grade = Grade.query.get(assignment.grade_id)
                stream = Stream.query.get(assignment.stream_id) if assignment.stream_id else None
                class_assignments.append({
                    'grade': grade.name if grade else 'Unknown',
                    'stream': stream.name if stream else 'All Streams'
                })
            else:
                # Subject assignment
                subject = Subject.query.get(assignment.subject_id)
                grade = Grade.query.get(assignment.grade_id)
                stream = Stream.query.get(assignment.stream_id) if assignment.stream_id else None

                if subject and subject.name not in subjects_taught:
                    subjects_taught.append(subject.name)

                subject_assignments.append({
                    'subject': subject.name if subject else 'Unknown',
                    'grade': grade.name if grade else 'Unknown',
                    'stream': stream.name if stream else 'All Streams'
                })

        # Create enhanced teacher object
        enhanced_teacher = {
            'id': teacher.id,
            'username': teacher.username,
            'role': teacher.role,
            'subjects_taught': subjects_taught,
            'class_assignments': class_assignments,
            'subject_assignments': subject_assignments,
            'total_assignments': len(teacher_assignments),
            'is_class_teacher': len(class_assignments) > 0
        }

        teachers.append(enhanced_teacher)

    # Get all subjects and grades for the form
    subjects = Subject.query.all()
    grades = Grade.query.all()

    if request.method == 'POST':
        if "add_teacher" in request.form:
            username = request.form.get("username")
            password = request.form.get("password")
            role = request.form.get("role")
            first_name = request.form.get("first_name", "").strip()
            last_name = request.form.get("last_name", "").strip()
            email = request.form.get("email", "").strip()
            phone = request.form.get("phone", "").strip()
            qualification = request.form.get("qualification", "").strip()
            specialization = request.form.get("specialization", "").strip()

            if not username or not password or not role:
                error_message = "Please fill in all required fields (username, password, role)."
            else:
                # Check if teacher with same username already exists
                if Teacher.query.filter_by(username=username).first():
                    error_message = f"A teacher with username '{username}' already exists."
                else:
                    try:
                        # Generate employee ID
                        cursor = db.session.execute(db.text("SELECT MAX(id) FROM teacher"))
                        max_id = cursor.scalar() or 0
                        employee_id = f"EMP{max_id + 1:03d}"

                        new_teacher = Teacher(
                            username=username,
                            role=role,
                            first_name=first_name if first_name else None,
                            last_name=last_name if last_name else None,
                            email=email if email else None,
                            phone=phone if phone else None,
                            employee_id=employee_id,
                            qualification=qualification if qualification else None,
                            specialization=specialization if specialization else None,
                            date_joined=db.func.current_date(),
                            is_active=True
                        )
                        # Use set_password() to properly set both password and password_hash columns
                        new_teacher.set_password(password)
                        db.session.add(new_teacher)
                        db.session.commit()
                        success_message = f"Teacher '{username}' added successfully with Employee ID: {employee_id}! You can now assign subjects to this teacher using the Bulk Assignments feature."
                    except Exception as e:
                        db.session.rollback()
                        error_message = f"Error adding teacher: {str(e)}"

        elif "update_teacher" in request.form:
            teacher_id = request.form.get("edit_teacher_id")
            new_role = request.form.get("edit_role")
            new_password = request.form.get("edit_password")
            first_name = request.form.get("edit_first_name", "").strip()
            last_name = request.form.get("edit_last_name", "").strip()
            email = request.form.get("edit_email", "").strip()
            phone = request.form.get("edit_phone", "").strip()
            qualification = request.form.get("edit_qualification", "").strip()
            specialization = request.form.get("edit_specialization", "").strip()

            teacher = Teacher.query.get(teacher_id)

            if teacher:
                try:
                    # Update basic fields
                    teacher.role = new_role
                    teacher.first_name = first_name if first_name else None
                    teacher.last_name = last_name if last_name else None
                    teacher.email = email if email else None
                    teacher.phone = phone if phone else None
                    teacher.qualification = qualification if qualification else None
                    teacher.specialization = specialization if specialization else None

                    # Update password if provided
                    if new_password and new_password.strip():
                        teacher.set_password(new_password)

                    db.session.commit()
                    success_message = f"Teacher '{teacher.username}' updated successfully!"
                except Exception as e:
                    db.session.rollback()
                    error_message = f"Error updating teacher: {str(e)}"
            else:
                error_message = "Teacher not found."

        elif "delete_teacher" in request.form:
            teacher_id = request.form.get("teacher_id")

            if teacher_id:
                try:
                    from ..utils.database_utils import safe_delete_teacher

                    result = safe_delete_teacher(teacher_id)

                    if result['success']:
                        success_message = result['message']
                    else:
                        error_message = result['message']

                except ImportError:
                    # Fallback to original method if utils not available
                    teacher = Teacher.query.get(teacher_id)
                    if teacher:
                        try:
                            # First, manually delete related records to avoid cascade issues
                            from sqlalchemy import text

                            # Delete from teacher_subjects table if it exists
                            try:
                                db.session.execute(text("DELETE FROM teacher_subjects WHERE teacher_id = :teacher_id"),
                                                 {"teacher_id": teacher_id})
                            except Exception as e:
                                print(f"Note: teacher_subjects table cleanup: {e}")

                            # Delete from teacher_subject_assignment table
                            try:
                                db.session.execute(text("DELETE FROM teacher_subject_assignment WHERE teacher_id = :teacher_id"),
                                                 {"teacher_id": teacher_id})
                            except Exception as e:
                                print(f"Note: teacher_subject_assignment table cleanup: {e}")

                            # Now delete the teacher
                            db.session.delete(teacher)
                            db.session.commit()
                            success_message = f"Teacher '{teacher.username}' deleted successfully!"

                        except Exception as e:
                            db.session.rollback()
                            print(f"Error deleting teacher: {e}")
                            error_message = f"Error deleting teacher: {str(e)}"
                    else:
                        error_message = "Teacher not found."
            else:
                error_message = "Teacher ID is required."

        elif "delete_assignment" in request.form:
            assignment_id = request.form.get("assignment_id")
            assignment_type = request.form.get("assignment_type")

            if assignment_id:
                try:
                    assignment = TeacherSubjectAssignment.query.get(assignment_id)
                    if assignment:
                        teacher = Teacher.query.get(assignment.teacher_id)

                        if assignment_type == "class_teacher":
                            grade = Grade.query.get(assignment.grade_id)
                            stream_text = ""
                            if assignment.stream_id:
                                stream = Stream.query.get(assignment.stream_id)
                                stream_text = f" Stream {stream.name}"

                            message = f"Class teacher assignment for {teacher.username} in Grade {grade.name}{stream_text} removed successfully."
                        else:
                            subject = Subject.query.get(assignment.subject_id)
                            grade = Grade.query.get(assignment.grade_id)
                            stream_text = ""
                            if assignment.stream_id:
                                stream = Stream.query.get(assignment.stream_id)
                                stream_text = f" Stream {stream.name}"

                            message = f"Subject assignment of {subject.name} to {teacher.username} for Grade {grade.name}{stream_text} removed successfully."

                        db.session.delete(assignment)
                        db.session.commit()
                        success_message = message
                    else:
                        error_message = "Assignment not found."
                except Exception as e:
                    print(f"Error removing assignment: {str(e)}")
                    error_message = "Error removing assignment."

    # Get current teacher's assignments for display
    try:
        # Get class teacher assignments for current teacher
        class_teacher_assignments = TeacherSubjectAssignment.query.filter_by(
            teacher_id=current_teacher.id,
            is_class_teacher=True
        ).all()

        # Get subject assignments for current teacher
        subject_assignments = TeacherSubjectAssignment.query.filter_by(
            teacher_id=current_teacher.id,
            is_class_teacher=False
        ).all()

    except Exception as e:
        print(f"Error fetching assignments: {str(e)}")
        class_teacher_assignments = []
        subject_assignments = []

    return render_template(
        'manage_teachers.html',
        teachers=teachers,
        class_teacher_assignments=class_teacher_assignments,
        subject_assignments=subject_assignments,
        error_message=error_message,
        success_message=success_message
    )

@classteacher_bp.route('/assign_subjects', methods=['GET', 'POST'])
@classteacher_required
def assign_subjects():
    """Route for assigning subjects to teachers."""
    error_message = None
    success_message = None

    # Get all teachers, subjects, grades
    teachers = Teacher.query.all()
    subjects = Subject.query.all()
    grades = Grade.query.all()

    # Get all current assignments
    try:
        assignments = TeacherSubjectAssignment.query.join(Teacher).join(Subject).join(Grade).all()
    except Exception as e:
        print(f"Error fetching teacher assignments: {str(e)}")
        assignments = []

    if request.method == 'POST':
        teacher_id = request.form.get('teacher_id')
        subject_id = request.form.get('subject_id')
        grade_id = request.form.get('grade_id')
        stream_id = request.form.get('stream_id') or None  # Handle empty string
        is_class_teacher = 'is_class_teacher' in request.form

        if not teacher_id or not subject_id or not grade_id:
            error_message = "Please fill in all required fields."
        else:
            # First, check if the table exists and create it if it doesn't
            try:
                # Try to access the table to see if it exists
                TeacherSubjectAssignment.query.first()
            except Exception as e:
                print(f"Table check error: {str(e)}")
                # Table doesn't exist, create it
                try:
                    # SQL to create the teacher_subject_assignment table
                    from sqlalchemy import text
                    create_table_sql = text('''
                    CREATE TABLE IF NOT EXISTS teacher_subject_assignment (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        teacher_id INTEGER NOT NULL,
                        subject_id INTEGER NOT NULL,
                        grade_id INTEGER NOT NULL,
                        stream_id INTEGER,
                        is_class_teacher BOOLEAN DEFAULT 0,
                        FOREIGN KEY (teacher_id) REFERENCES teacher (id),
                        FOREIGN KEY (subject_id) REFERENCES subject (id),
                        FOREIGN KEY (grade_id) REFERENCES grade (id),
                        FOREIGN KEY (stream_id) REFERENCES stream (id)
                    );
                    ''')

                    # Execute the SQL
                    db.session.execute(create_table_sql)
                    db.session.commit()

                    success_message = "Created teacher_subject_assignment table."
                except Exception as table_error:
                    print(f"Error creating table: {str(table_error)}")
                    error_message = "Error creating teacher_subject_assignment table."
                    return render_template(
                        'teacher_assignments.html',
                        teachers=teachers,
                        subjects=subjects,
                        grades=grades,
                        assignments=[],
                        error_message=error_message,
                        success_message=success_message
                    )

            # Now check if this assignment already exists
            try:
                existing = TeacherSubjectAssignment.query.filter_by(
                    teacher_id=teacher_id,
                    subject_id=subject_id,
                    grade_id=grade_id,
                    stream_id=stream_id
                ).first()

                if existing:
                    error_message = "This assignment already exists."
                    return render_template(
                        'teacher_assignments.html',
                        teachers=teachers,
                        subjects=subjects,
                        grades=grades,
                        assignments=assignments,
                        error_message=error_message,
                        success_message=success_message
                    )
            except Exception as e:
                print(f"Error checking for existing assignment: {str(e)}")
                # Continue since we've already tried to create the table

            # If this is a class teacher, check if there's already one for this grade/stream
            if is_class_teacher and stream_id:
                try:
                    existing_class_teacher = TeacherSubjectAssignment.query.filter_by(
                        grade_id=grade_id,
                        stream_id=stream_id,
                        is_class_teacher=True
                    ).first()

                    if existing_class_teacher:
                        teacher = Teacher.query.get(existing_class_teacher.teacher_id)
                        error_message = f"There is already a class teacher assigned to this class: {teacher.username}"
                        return render_template(
                            'teacher_assignments.html',
                            teachers=teachers,
                            subjects=subjects,
                            grades=grades,
                            assignments=assignments,
                            error_message=error_message,
                            success_message=success_message
                        )
                except Exception as e:
                    print(f"Error checking for existing class teacher: {str(e)}")
                    # Continue with the assignment since we've already tried to create the table

            # Create the new assignment
            try:
                new_assignment = TeacherSubjectAssignment(
                    teacher_id=teacher_id,
                    subject_id=subject_id,
                    grade_id=grade_id,
                    stream_id=stream_id,
                    is_class_teacher=is_class_teacher
                )

                db.session.add(new_assignment)
                db.session.commit()

                teacher = Teacher.query.get(teacher_id)
                subject = Subject.query.get(subject_id)
                grade = Grade.query.get(grade_id)

                success_message = f"Successfully assigned {subject.name} to {teacher.username} for Grade {grade.name}"
                if stream_id:
                    stream = Stream.query.get(stream_id)
                    success_message += f" Stream {stream.name}"

                # Refresh assignments list
                try:
                    assignments = TeacherSubjectAssignment.query.join(Teacher).join(Subject).join(Grade).all()
                except Exception as e:
                    print(f"Error refreshing assignments: {str(e)}")
                    assignments = []
            except Exception as e:
                print(f"Error creating assignment: {str(e)}")
                error_message = "Error creating assignment. The teacher_subject_assignment table may not exist. Please run the migration script to create it."

    return render_template(
        'teacher_assignments.html',
        teachers=teachers,
        subjects=subjects,
        grades=grades,
        assignments=assignments,
        error_message=error_message,
        success_message=success_message
    )

@classteacher_bp.route('/bulk_assign_subjects', methods=['POST'])
@classteacher_required
def bulk_assign_subjects():
    """Route for bulk assigning subjects to teachers."""
    error_message = None
    success_message = None

    # Get form data
    teacher_id = request.form.get('bulk_teacher_id')
    subject_ids = request.form.getlist('bulk_subjects')
    grade_ids = request.form.getlist('bulk_grades')
    is_class_teacher = 'bulk_is_class_teacher' in request.form

    # Validate input
    if not teacher_id or not subject_ids or not grade_ids:
        flash("Please select a teacher, at least one subject, and at least one grade.", "error")
        return redirect(url_for('classteacher.assign_subjects'))

    # First, check if the table exists and create it if it doesn't
    try:
        # Try to access the table to see if it exists
        TeacherSubjectAssignment.query.first()
    except Exception as e:
        print(f"Table check error: {str(e)}")
        # Table doesn't exist, create it
        try:
            # SQL to create the teacher_subject_assignment table
            from sqlalchemy import text
            create_table_sql = text('''
            CREATE TABLE IF NOT EXISTS teacher_subject_assignment (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                teacher_id INTEGER NOT NULL,
                subject_id INTEGER NOT NULL,
                grade_id INTEGER NOT NULL,
                stream_id INTEGER,
                is_class_teacher BOOLEAN DEFAULT 0,
                FOREIGN KEY (teacher_id) REFERENCES teacher (id),
                FOREIGN KEY (subject_id) REFERENCES subject (id),
                FOREIGN KEY (grade_id) REFERENCES grade (id),
                FOREIGN KEY (stream_id) REFERENCES stream (id)
            );
            ''')

            # Execute the SQL
            db.session.execute(create_table_sql)
            db.session.commit()

            flash("Created teacher_subject_assignment table.", "success")
        except Exception as table_error:
            print(f"Error creating table: {str(table_error)}")
            flash("Error creating teacher_subject_assignment table.", "error")
            return redirect(url_for('classteacher.assign_subjects'))

    # Get the teacher
    teacher = Teacher.query.get(teacher_id)
    if not teacher:
        flash("Teacher not found.", "error")
        return redirect(url_for('classteacher.assign_subjects'))

    # Track statistics for the flash message
    assignments_created = 0
    assignments_skipped = 0
    class_teacher_conflicts = 0

    # Create assignments for each subject and grade combination
    for subject_id in subject_ids:
        subject = Subject.query.get(subject_id)
        if not subject:
            continue

        for grade_id in grade_ids:
            grade = Grade.query.get(grade_id)
            if not grade:
                continue

            # Get streams for this grade
            streams = Stream.query.filter_by(grade_id=grade_id).all()

            # If there are no streams, create an assignment for the whole grade
            if not streams:
                try:
                    # Check if assignment already exists
                    existing = TeacherSubjectAssignment.query.filter_by(
                        teacher_id=teacher_id,
                        subject_id=subject_id,
                        grade_id=grade_id,
                        stream_id=None
                    ).first()

                    if existing:
                        assignments_skipped += 1
                        continue

                    # Create the assignment
                    new_assignment = TeacherSubjectAssignment(
                        teacher_id=teacher_id,
                        subject_id=subject_id,
                        grade_id=grade_id,
                        stream_id=None,
                        is_class_teacher=is_class_teacher
                    )

                    db.session.add(new_assignment)
                    assignments_created += 1

                except Exception as e:
                    print(f"Error creating assignment: {str(e)}")
                    continue
            else:
                # Create an assignment for each stream
                for stream in streams:
                    try:
                        # Check if assignment already exists
                        existing = TeacherSubjectAssignment.query.filter_by(
                            teacher_id=teacher_id,
                            subject_id=subject_id,
                            grade_id=grade_id,
                            stream_id=stream.id
                        ).first()

                        if existing:
                            assignments_skipped += 1
                            continue

                        # If this is a class teacher, check if there's already one for this grade/stream
                        if is_class_teacher:
                            existing_class_teacher = TeacherSubjectAssignment.query.filter_by(
                                grade_id=grade_id,
                                stream_id=stream.id,
                                is_class_teacher=True
                            ).first()

                            if existing_class_teacher and int(existing_class_teacher.teacher_id) != int(teacher_id):
                                class_teacher_conflicts += 1
                                continue

                        # Create the assignment
                        new_assignment = TeacherSubjectAssignment(
                            teacher_id=teacher_id,
                            subject_id=subject_id,
                            grade_id=grade_id,
                            stream_id=stream.id,
                            is_class_teacher=is_class_teacher
                        )

                        db.session.add(new_assignment)
                        assignments_created += 1

                    except Exception as e:
                        print(f"Error creating assignment: {str(e)}")
                        continue

    # Commit all the new assignments
    try:
        db.session.commit()

        # Create a summary message
        message = f"Created {assignments_created} new assignments for {teacher.username}. "
        if assignments_skipped > 0:
            message += f"Skipped {assignments_skipped} existing assignments. "
        if class_teacher_conflicts > 0:
            message += f"Skipped {class_teacher_conflicts} class teacher assignments due to conflicts."

        flash(message, "success")
    except Exception as e:
        db.session.rollback()
        print(f"Error committing assignments: {str(e)}")
        flash("Error creating assignments. Please try again.", "error")

    return redirect(url_for('classteacher.assign_subjects'))

@classteacher_bp.route('/enhanced_bulk_assign_subjects', methods=['POST'])
@classteacher_required
def enhanced_bulk_assign_subjects():
    """Enhanced route for bulk assignment of subjects to teachers with advanced features."""
    from ..models.assignment import TeacherSubjectAssignment

    teacher_id = request.form.get('bulk_teacher_id')
    assignment_mode = request.form.get('assignment_mode', 'simple')

    if not teacher_id:
        flash("Please select a teacher.", "error")
        return redirect(url_for('classteacher.assign_subjects'))

    # Get the teacher
    teacher = Teacher.query.get(teacher_id)
    if not teacher:
        flash("Teacher not found.", "error")
        return redirect(url_for('classteacher.assign_subjects'))

    # Track statistics for the flash message
    assignments_created = 0
    assignments_skipped = 0
    class_teacher_conflicts = 0
    errors = []

    try:
        if assignment_mode == 'simple':
            # Handle enhanced simple bulk assignment with dynamic stream selection
            subject_ids = request.form.getlist('bulk_subjects')
            grade_ids = request.form.getlist('bulk_grades')
            class_teacher_grades = request.form.getlist('class_teacher_grades')

            if not subject_ids or not grade_ids:
                flash("Please select at least one subject and one grade.", "error")
                return redirect(url_for('classteacher.assign_subjects'))

            # Create assignments for each subject and grade combination
            for subject_id in subject_ids:
                subject = Subject.query.get(subject_id)
                if not subject:
                    continue

                for grade_id in grade_ids:
                    grade = Grade.query.get(grade_id)
                    if not grade:
                        continue

                    # Check if this grade should have class teacher designation
                    is_class_teacher_for_grade = grade_id in class_teacher_grades

                    # Check stream selection for this grade
                    all_streams_option = request.form.get(f'stream_option_{grade_id}', 'all')

                    if all_streams_option == 'all':
                        # Assign to all streams or whole grade if no streams exist
                        streams = Stream.query.filter_by(grade_id=grade_id).all()

                        if not streams:
                            # No streams - create assignment for whole grade
                            result = create_single_assignment(
                                teacher_id, subject_id, grade_id, None, is_class_teacher_for_grade
                            )
                            assignments_created += result['created']
                            assignments_skipped += result['skipped']
                            class_teacher_conflicts += result['conflicts']
                            if result['error']:
                                errors.append(result['error'])
                        else:
                            # Has streams - create assignment for each stream
                            for stream in streams:
                                result = create_single_assignment(
                                    teacher_id, subject_id, grade_id, stream.id, is_class_teacher_for_grade
                                )
                                assignments_created += result['created']
                                assignments_skipped += result['skipped']
                                class_teacher_conflicts += result['conflicts']
                                if result['error']:
                                    errors.append(result['error'])
                    else:
                        # Assign to specific streams only
                        specific_stream_ids = request.form.getlist(f'specific_streams_{grade_id}')

                        if specific_stream_ids:
                            for stream_id in specific_stream_ids:
                                stream = Stream.query.get(stream_id)
                                if stream and stream.grade_id == int(grade_id):
                                    result = create_single_assignment(
                                        teacher_id, subject_id, grade_id, stream.id, is_class_teacher_for_grade
                                    )
                                    assignments_created += result['created']
                                    assignments_skipped += result['skipped']
                                    class_teacher_conflicts += result['conflicts']
                                    if result['error']:
                                        errors.append(result['error'])
                        else:
                            # No specific streams selected but specific mode chosen - skip this grade
                            errors.append(f"No specific streams selected for Grade {grade.name}")
                            continue

        else:  # advanced mode
            # Handle advanced precise assignment
            advanced_subjects = request.form.getlist('advanced_subjects[]')
            advanced_grades = request.form.getlist('advanced_grades[]')
            advanced_streams = request.form.getlist('advanced_streams[]')
            advanced_class_teachers = request.form.getlist('advanced_class_teacher[]')

            if not advanced_subjects or not advanced_grades:
                flash("Please add at least one assignment combination.", "error")
                return redirect(url_for('classteacher.assign_subjects'))

            # Process each combination
            for i in range(len(advanced_subjects)):
                if i < len(advanced_subjects) and i < len(advanced_grades):
                    subject_id = advanced_subjects[i]
                    grade_id = advanced_grades[i]
                    stream_id = advanced_streams[i] if i < len(advanced_streams) and advanced_streams[i] else None
                    is_class_teacher = str(i + 1) in advanced_class_teachers

                    if subject_id and grade_id:
                        result = create_single_assignment(
                            teacher_id, subject_id, grade_id, stream_id, is_class_teacher
                        )
                        assignments_created += result['created']
                        assignments_skipped += result['skipped']
                        class_teacher_conflicts += result['conflicts']
                        if result['error']:
                            errors.append(result['error'])

        # Commit all the new assignments
        db.session.commit()

        # Create a comprehensive summary message
        message_parts = []

        if assignments_created > 0:
            message_parts.append(f"✅ Created {assignments_created} new assignments for {teacher.username}")

        if assignments_skipped > 0:
            message_parts.append(f"⏭️ Skipped {assignments_skipped} existing assignments")

        if class_teacher_conflicts > 0:
            message_parts.append(f"⚠️ Skipped {class_teacher_conflicts} class teacher assignments due to conflicts")

        if errors:
            message_parts.append(f"❌ {len(errors)} errors occurred")

        if assignments_created > 0:
            flash(". ".join(message_parts), "success")
        else:
            flash("No new assignments were created. " + ". ".join(message_parts[1:]), "warning")

    except Exception as e:
        db.session.rollback()
        print(f"Error in enhanced bulk assignment: {str(e)}")
        flash(f"Error creating assignments: {str(e)}", "error")

    # If this is an AJAX (fetch) request expecting JSON, return structured response instead of redirect
    wants_json = (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or 'application/json' in (request.headers.get('Accept') or '')
    )
    if wants_json:
        status = 'success' if assignments_created > 0 else 'warning'
        return jsonify({
            'status': status,
            'created': assignments_created,
            'skipped': assignments_skipped,
            'class_teacher_conflicts': class_teacher_conflicts,
            'errors': errors,
            'message': "; ".join(message_parts) if assignments_created > 0 else ("; ".join(message_parts[1:]) if message_parts else "No changes applied")
        })

    return redirect(url_for('classteacher.assign_subjects'))

def create_single_assignment(teacher_id, subject_id, grade_id, stream_id, is_class_teacher):
    """Helper function to create a single assignment with error handling."""
    from ..models.assignment import TeacherSubjectAssignment

    result = {
        'created': 0,
        'skipped': 0,
        'conflicts': 0,
        'error': None
    }

    try:
        # Check if assignment already exists
        existing = TeacherSubjectAssignment.query.filter_by(
            teacher_id=teacher_id,
            subject_id=subject_id,
            grade_id=grade_id,
            stream_id=stream_id
        ).first()

        if existing:
            result['skipped'] = 1
            return result

        # If this is a class teacher, check if there's already one for this grade/stream
        if is_class_teacher:
            existing_class_teacher = TeacherSubjectAssignment.query.filter_by(
                grade_id=grade_id,
                stream_id=stream_id,
                is_class_teacher=True
            ).first()

            if existing_class_teacher and int(existing_class_teacher.teacher_id) != int(teacher_id):
                result['conflicts'] = 1
                return result

        # Create the assignment
        new_assignment = TeacherSubjectAssignment(
            teacher_id=teacher_id,
            subject_id=subject_id,
            grade_id=grade_id,
            stream_id=stream_id,
            is_class_teacher=is_class_teacher
        )

        db.session.add(new_assignment)
        result['created'] = 1

    except Exception as e:
        result['error'] = f"Error creating assignment: {str(e)}"

    return result

@classteacher_bp.route('/advanced_assignments', methods=['GET'])
@classteacher_required
def advanced_assignments():
    """Route for the advanced teacher assignments page."""
    from ..models.assignment import TeacherSubjectAssignment

    # Get all teachers, subjects, and grades
    teachers = Teacher.query.all()
    subjects = Subject.query.all()
    grades = Grade.query.all()

    # Get all current assignments
    try:
        assignments = TeacherSubjectAssignment.query.join(Teacher).join(Subject).join(Grade).all()
    except Exception as e:
        print(f"Error fetching teacher assignments: {str(e)}")
        assignments = []

    return render_template(
        'teacher_assignments.html',
        teachers=teachers,
        subjects=subjects,
        grades=grades,
        assignments=assignments,
        error_message=None,
        success_message=None
    )

@classteacher_bp.route('/remove_assignment', methods=['POST'])
@classteacher_required
def remove_assignment():
    """Route for removing a teacher subject assignment."""
    assignment_id = request.form.get('assignment_id')

    if assignment_id:
        try:
            assignment = TeacherSubjectAssignment.query.get(assignment_id)
            if assignment:
                teacher = Teacher.query.get(assignment.teacher_id)
                subject = Subject.query.get(assignment.subject_id)
                grade = Grade.query.get(assignment.grade_id)

                message = f"Assignment of {subject.name} to {teacher.username} for Grade {grade.name}"
                if assignment.stream_id:
                    stream = Stream.query.get(assignment.stream_id)
                    message += f" Stream {stream.name}"
                message += " removed successfully."

                db.session.delete(assignment)
                db.session.commit()

                flash(message, "success")
        except Exception as e:
            print(f"Error removing assignment: {str(e)}")
            flash("Error removing assignment. The teacher_subject_assignment table may not exist.", "error")

    return redirect(url_for('classteacher.assign_subjects'))


@classteacher_bp.route('/get_grade_streams/<int:grade_id>', methods=['GET'])
@classteacher_required
def get_grade_streams(grade_id):
    """API endpoint to get streams for a grade."""
    streams = Stream.query.filter_by(grade_id=grade_id).all()
    return jsonify({
        'streams': [{'id': stream.id, 'name': stream.name} for stream in streams]
    })

@classteacher_bp.route('/teacher_streams/<int:grade_id>', methods=['GET'])
@classteacher_required
def teacher_streams(grade_id):
    """API endpoint to get streams for a grade (for manage_teachers page)."""
    streams = Stream.query.filter_by(grade_id=grade_id).all()
    return jsonify({
        'streams': [{'id': stream.id, 'name': stream.name} for stream in streams]
    })

@classteacher_bp.route('/get_teacher_assignments/<int:teacher_id>', methods=['GET'])
@classteacher_required
def get_teacher_assignments(teacher_id):
    """API endpoint to get all assignments for a specific teacher."""
    try:
        # Check if the teacher exists
        teacher = Teacher.query.get(teacher_id)
        if not teacher:
            return jsonify({'error': 'Teacher not found'}), 404

        # Get all assignments for the teacher
        assignments = TeacherSubjectAssignment.query.filter_by(teacher_id=teacher_id).all()

        # Format the assignments for the response
        formatted_assignments = []
        for assignment in assignments:
            subject = Subject.query.get(assignment.subject_id)
            grade = Grade.query.get(assignment.grade_id)
            stream = Stream.query.get(assignment.stream_id) if assignment.stream_id else None

            formatted_assignments.append({
                'id': assignment.id,
                'subject': subject.name if subject else 'Unknown Subject',
                'grade': grade.name if grade else 'Unknown Grade',
                'stream': stream.name if stream else None,
                'is_class_teacher': assignment.is_class_teacher
            })

        return jsonify({
            'teacher': teacher.username,
            'assignments': formatted_assignments
        })
    except Exception as e:
        print(f"Error fetching teacher assignments: {str(e)}")
        return jsonify({'error': 'Failed to fetch assignments'}), 500

@classteacher_bp.route('/api/streams/<grade>')
@classteacher_required
def api_get_streams(grade):
    """API endpoint to get streams for a grade level."""
    grade_obj = Grade.query.filter_by(name=grade).first()
    if grade_obj:
        streams = Stream.query.filter_by(grade_id=grade_obj.id).all()
        stream_names = [stream.name for stream in streams]
        return jsonify({"streams": [f"Stream {name}" for name in stream_names]})
    return jsonify({"streams": []})


@classteacher_bp.route('/api/streams_by_id/<int:grade_id>')
@classteacher_required
def api_get_streams_by_id(grade_id):
    """API endpoint to get streams for a grade by grade ID."""
    try:
        grade_obj = Grade.query.get(grade_id)
        if grade_obj:
            streams = Stream.query.filter_by(grade_id=grade_id).all()
            stream_data = []
            for stream in streams:
                stream_data.append({
                    "id": stream.id,
                    "name": stream.name,
                    "display_name": f"Stream {stream.name}"
                })
            return jsonify({"streams": stream_data})
        return jsonify({"streams": []})
    except Exception as e:
        return jsonify({"streams": [], "error": str(e)})


@classteacher_bp.route('/api/test_streams')
@dev_only
def api_test_streams():
    """Simple test endpoint to check API connectivity."""
    return jsonify({"status": "ok", "message": "API is working", "test_streams": ["Stream A", "Stream B", "Stream C"]})


@classteacher_bp.route('/api/assessment_types')
@classteacher_required
def api_get_assessment_types():
    """API endpoint to get all active assessment types."""
    try:
        assessment_types = AssessmentType.query.filter_by(is_active=True).all()
        assessment_data = []
        for assessment in assessment_types:
            assessment_data.append({
                "id": assessment.id,
                "name": assessment.name,
                "weight": assessment.weight,
                "category": assessment.category
            })
        return jsonify({"assessment_types": assessment_data})
    except Exception as e:
        return jsonify({"assessment_types": [], "error": str(e)})


@classteacher_bp.route('/view_all_reports')
@classteacher_required
def view_all_reports():
    """Route for viewing all reports with pagination and filtering."""
    # Get filter parameters
    sort_by = request.args.get('sort', 'date')
    filter_grade = request.args.get('filter_grade', '')
    filter_term = request.args.get('filter_term', '')
    filter_assessment = request.args.get('filter_assessment', '')
    page = request.args.get('page', 1, type=int)
    per_page = 20  # Number of reports per page

    # Build the query with joins
    marks_query = Mark.query.join(Student).join(Stream).join(Grade).join(Term).join(AssessmentType)

    # Apply filters if provided
    if filter_grade:
        marks_query = marks_query.filter(Grade.name == filter_grade)
    if filter_term:
        marks_query = marks_query.filter(Term.name == filter_term)
    if filter_assessment:
        marks_query = marks_query.filter(AssessmentType.name == filter_assessment)

    # Apply sorting
    if sort_by == 'grade':
        marks_query = marks_query.order_by(Grade.name)
    elif sort_by == 'term':
        marks_query = marks_query.order_by(Term.name)
    else:  # Default to date
        marks_query = marks_query.order_by(Mark.created_at.desc())

    # Get unique combinations of grade, stream, term, assessment_type
    from sqlalchemy import func
        # --- CLASS-LEVEL REPORTS (existing logic) ---
    unique_combinations = db.session.query(
        Grade.name,
        Stream.name,
        Term.name,
        AssessmentType.name,
        func.count(Mark.id).label('mark_count'),
        func.max(Mark.created_at).label('latest_date')
    ).join(Student, Mark.student_id == Student.id)\
     .join(Stream, Student.stream_id == Stream.id)\
     .join(Grade, Stream.grade_id == Grade.id)\
     .join(Term, Mark.term_id == Term.id)\
     .join(AssessmentType, Mark.assessment_type_id == AssessmentType.id)\
     .group_by(Grade.name, Stream.name, Term.name, AssessmentType.name)

    # Apply filters to the unique combinations query
    if filter_grade:
        unique_combinations = unique_combinations.filter(Grade.name == filter_grade)
    if filter_term:
        unique_combinations = unique_combinations.filter(Term.name == filter_term)
    if filter_assessment:
        unique_combinations = unique_combinations.filter(AssessmentType.name == filter_assessment)

    # Apply sorting to the unique combinations query
    if sort_by == 'grade':
        unique_combinations = unique_combinations.order_by(Grade.name)
    elif sort_by == 'term':
        unique_combinations = unique_combinations.order_by(Term.name)
    else:  # Default to date
        unique_combinations = unique_combinations.order_by(func.max(Mark.created_at).desc())

    # Paginate the results
    pagination = unique_combinations.paginate(page=page, per_page=per_page, error_out=False)

    # Format the results
    reports = []
    for grade, stream, term, assessment_type, mark_count, latest_date in pagination.items:
        # Get the actual database objects to get their IDs
        grade_obj = Grade.query.filter_by(name=grade).first()
        stream_obj = Stream.query.join(Grade).filter(Grade.name == grade, Stream.name == stream).first()
        term_obj = Term.query.filter_by(name=term).first()
        assessment_type_obj = AssessmentType.query.filter_by(name=assessment_type).first()

        # Create download URL for class report (since these are class-level reports, not subject-specific)
        # The stream parameter should match the format expected by the route
        stream_param = f"Stream {stream}"

        # Construct URL manually since url_for might have issues in this context
        from urllib.parse import quote
        download_url = f"/classteacher/preview_class_report/{quote(grade)}/{quote(stream_param)}/{quote(term)}/{quote(assessment_type)}"

        reports.append({
            'id': len(reports) + 1,
            'grade': grade,
            'stream': f"Stream {stream}",
            'term': term,
            'assessment_type': assessment_type,
            'date': latest_date.strftime('%Y-%m-%d') if latest_date else 'N/A',
            'mark_count': mark_count,
            'grade_id': grade_obj.id if grade_obj else None,
            'stream_id': stream_obj.id if stream_obj else None,
            'term_id': term_obj.id if term_obj else None,
            'assessment_type_id': assessment_type_obj.id if assessment_type_obj else None,
            'subject_id': None,  # These are class reports, not subject-specific
            'download_url': download_url
        })

    # Get all grades, terms, and assessment types for filters
    grades = [grade.name for grade in Grade.query.all()]
    terms = [term.name for term in Term.query.all()]
    assessment_types = [assessment_type.name for assessment_type in AssessmentType.query.all()]

    # --- SUBJECT-LEVEL REPORTS (new) ---
    # Build a query to group by subject as well so we can offer Subject Report buttons
    from ..models import Subject  # local import to avoid circulars higher up
    subject_combos = db.session.query(
        Grade.name.label('grade_name'),
        Stream.name.label('stream_name'),
        Term.name.label('term_name'),
        AssessmentType.name.label('assessment_type_name'),
        Subject.name.label('subject_name'),
        func.count(Mark.id).label('mark_count'),
        func.max(Mark.created_at).label('latest_date')
    ).join(Student, Mark.student_id == Student.id) \
     .join(Stream, Student.stream_id == Stream.id) \
     .join(Grade, Stream.grade_id == Grade.id) \
     .join(Term, Mark.term_id == Term.id) \
     .join(AssessmentType, Mark.assessment_type_id == AssessmentType.id) \
     .join(Subject, Mark.subject_id == Subject.id) \
     .group_by(Grade.name, Stream.name, Term.name, AssessmentType.name, Subject.name)

    if filter_grade:
        subject_combos = subject_combos.filter(Grade.name == filter_grade)
    if filter_term:
        subject_combos = subject_combos.filter(Term.name == filter_term)
    if filter_assessment:
        subject_combos = subject_combos.filter(AssessmentType.name == filter_assessment)

    # Use same sort preference
    if sort_by == 'grade':
        subject_combos = subject_combos.order_by(Grade.name, Subject.name)
    elif sort_by == 'term':
        subject_combos = subject_combos.order_by(Term.name, Subject.name)
    else:
        subject_combos = subject_combos.order_by(func.max(Mark.created_at).desc())

    subject_reports = []
    for grade_name, stream_name, term_name, assessment_type_name, subject_name, mark_count, latest_date in subject_combos.limit(200):  # safety cap
        grade_obj = Grade.query.filter_by(name=grade_name).first()
        stream_obj = Stream.query.join(Grade).filter(Grade.name == grade_name, Stream.name == stream_name).first()
        term_obj = Term.query.filter_by(name=term_name).first()
        assessment_type_obj = AssessmentType.query.filter_by(name=assessment_type_name).first()
        subject_obj = Subject.query.filter_by(name=subject_name).first()
        if not all([grade_obj, stream_obj, term_obj, assessment_type_obj, subject_obj]):
            continue
        subject_reports.append({
            'id': len(subject_reports) + 1,
            'grade': grade_name,
            'stream': f"Stream {stream_name}",
            'term': term_name,
            'assessment_type': assessment_type_name,
            'subject': subject_name,
            'mark_count': mark_count,
            'date': latest_date.strftime('%Y-%m-%d') if latest_date else 'N/A',
            'grade_id': grade_obj.id,
            'stream_id': stream_obj.id,
            'term_id': term_obj.id,
            'assessment_type_id': assessment_type_obj.id,
            'subject_id': subject_obj.id,
            'download_url': url_for('classteacher.subject_report', grade_id=grade_obj.id, stream_id=stream_obj.id, subject_id=subject_obj.id, term_id=term_obj.id, assessment_type_id=assessment_type_obj.id)
        })

    return render_template(
        'all_reports.html',
        reports=reports,
        subject_reports=subject_reports,
        pagination=pagination,
        sort_by=sort_by,
        filter_grade=filter_grade,
        filter_term=filter_term,
        filter_assessment=filter_assessment,
        grades=grades,
        terms=terms,
        assessment_types=assessment_types
    )

@classteacher_bp.route('/delete_report/<grade>/<stream>/<term>/<assessment_type>', methods=['GET', 'POST'])
@classteacher_required
def delete_report(grade, stream, term, assessment_type):
    """Route for deleting a marksheet and all associated marks."""
    # Extract stream letter from "Stream X" format
    stream_letter = stream.replace("Stream ", "") if stream.startswith("Stream ") else stream

    # Get the stream, term, and assessment type objects
    stream_obj = Stream.query.join(Grade).filter(Grade.name == grade, Stream.name == stream_letter).first()
    term_obj = Term.query.filter_by(name=term).first()
    assessment_type_obj = AssessmentType.query.filter_by(name=assessment_type).first()

    if not (stream_obj and term_obj and assessment_type_obj):
        flash("Invalid stream, term, or assessment type.", "error")
        return redirect(url_for('classteacher.dashboard'))

    try:
        # Get all students in this stream
        students = Student.query.filter_by(stream_id=stream_obj.id).all()
        student_ids = [student.id for student in students]

        # Get marks to delete first (to handle component marks)
        marks_to_delete = Mark.query.filter(
            Mark.student_id.in_(student_ids),
            Mark.term_id == term_obj.id,
            Mark.assessment_type_id == assessment_type_obj.id
        ).all()

        deleted_count = len(marks_to_delete)

        # Delete component marks first, then marks
        for mark in marks_to_delete:
            # Delete component marks first
            ComponentMark.query.filter_by(mark_id=mark.id).delete()
            # Delete the mark
            db.session.delete(mark)

        # Commit the changes
        db.session.commit()

        # Invalidate any existing cache for this grade/stream/term/assessment combination
        invalidate_cache(grade, stream, term, assessment_type)

        flash(f"Successfully deleted {deleted_count} marks for {grade} {stream} in {term} {assessment_type}.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting marks: {str(e)}", "error")

    # Redirect back to the referring page or dashboard
    referrer = request.referrer
    if referrer and ('all_reports' in referrer or 'dashboard' in referrer):
        return redirect(referrer)
    else:
        return redirect(url_for('classteacher.dashboard'))

@classteacher_bp.route('/delete_subject_report', methods=['POST'])
@enforce('marks', 'delete', class_scope=True, roles=(
    'headteacher', 'admin'
))
def delete_subject_report():
    """Route for deleting marks for a specific subject in a grade/stream/term/assessment combination."""
    try:
        # Get parameters from form
        grade = request.form.get('grade')
        stream = request.form.get('stream')
        subject = request.form.get('subject')
        term = request.form.get('term')
        assessment_type = request.form.get('assessment_type')

        if not all([grade, stream, subject, term, assessment_type]):
            flash("Missing required parameters for deletion.", "error")
            return redirect(url_for('classteacher.dashboard'))

        # Extract stream letter from "Stream X" format if needed
        stream_letter = stream.replace("Stream ", "") if stream.startswith("Stream ") else stream

        # Get the objects
        stream_obj = Stream.query.join(Grade).filter(Grade.name == grade, Stream.name == stream_letter).first()
        subject_obj = Subject.query.filter_by(name=subject).first()
        term_obj = Term.query.filter_by(name=term).first()
        assessment_type_obj = AssessmentType.query.filter_by(name=assessment_type).first()

        if not all([stream_obj, subject_obj, term_obj, assessment_type_obj]):
            flash("Invalid parameters provided.", "error")
            return redirect(url_for('classteacher.dashboard'))

        # Get all students in this stream
        students = Student.query.filter_by(stream_id=stream_obj.id).all()
        student_ids = [student.id for student in students]

        # Get marks to delete for this specific subject
        marks_to_delete = Mark.query.filter(
            Mark.student_id.in_(student_ids),
            Mark.subject_id == subject_obj.id,
            Mark.term_id == term_obj.id,
            Mark.assessment_type_id == assessment_type_obj.id
        ).all()

        deleted_count = len(marks_to_delete)

        # Delete component marks first, then marks
        for mark in marks_to_delete:
            # Delete component marks first
            ComponentMark.query.filter_by(mark_id=mark.id).delete()
            # Delete the mark
            db.session.delete(mark)

        # Commit the changes
        db.session.commit()

        # Invalidate any existing cache
        invalidate_cache(grade, stream, term, assessment_type)

        flash(f"Successfully deleted {deleted_count} marks for {subject} in {grade} {stream} - {term} {assessment_type}.", "success")

    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting subject marks: {str(e)}", "error")

    # Redirect back to dashboard or reports page
    return redirect(url_for('classteacher.all_reports'))

@classteacher_bp.route('/api/stream_status/<grade>/<term>/<assessment_type>')
@classteacher_required
def api_get_stream_status(grade, term, assessment_type):
    """API endpoint to check if streams have marks for a specific grade, term, and assessment type."""
    grade_obj = Grade.query.filter_by(name=grade).first()
    term_obj = Term.query.filter_by(name=term).first()
    assessment_type_obj = AssessmentType.query.filter_by(name=assessment_type).first()

    if not (grade_obj and term_obj and assessment_type_obj):
        return jsonify({"error": "Invalid grade, term, or assessment type"}), 400

    streams = Stream.query.filter_by(grade_id=grade_obj.id).all()
    result = []

    for stream in streams:
        # Check if there are any marks for this stream
        has_marks = Mark.query.join(Student).filter(
            Student.stream_id == stream.id,
            Mark.term_id == term_obj.id,
            Mark.assessment_type_id == assessment_type_obj.id
        ).first() is not None

        result.append({
            "name": stream.name,
            "has_marks": has_marks
        })

    return jsonify({"streams": result})


# ============================================================================
# COLLABORATIVE MARKS UPLOAD SYSTEM
# ============================================================================

@classteacher_bp.route('/collaborative_marks_dashboard')
@classteacher_required
def collaborative_marks_dashboard():
    """Dashboard showing marks upload status for all classes managed by the class teacher."""
    try:
        current_teacher_id = session.get('teacher_id')
        if not current_teacher_id:
            flash("Please log in to access this page.", "error")
            return redirect(url_for('auth.classteacher_login'))

        # Get dashboard data
        dashboard_data = CollaborativeMarksService.get_class_teacher_dashboard_data(current_teacher_id)

        if 'error' in dashboard_data:
            flash(f"Error loading dashboard: {dashboard_data['error']}", "error")
            return redirect(url_for('classteacher.dashboard'))

        return render_template('collaborative_marks_dashboard.html', **dashboard_data)

    except Exception as e:
        print(f"Error in collaborative marks dashboard: {str(e)}")
        flash("Error loading collaborative marks dashboard.", "error")
        return redirect(url_for('classteacher.dashboard'))


@classteacher_bp.route('/class_marks_status/<int:grade_id>/<int:stream_id>/<int:term_id>/<int:assessment_type_id>')
@classteacher_required
def class_marks_status(grade_id, stream_id, term_id, assessment_type_id):
    """Show detailed marks upload status for a specific class."""
    print(f"🎯 CLASS_MARKS_STATUS FUNCTION CALLED!")
    print(f"  Route parameters: grade_id={grade_id}, stream_id={stream_id}, term_id={term_id}, assessment_type_id={assessment_type_id}")
    print(f"  Session teacher_id: {session.get('teacher_id')}")
    print(f"  Session role: {session.get('role')}")
    
    try:
        current_teacher_id = session.get('teacher_id')
        if not current_teacher_id:
            flash("Please log in to access this page.", "error")
            return redirect(url_for('auth.classteacher_login'))

        # Get class marks status
        status_data = CollaborativeMarksService.get_class_marks_status(
            grade_id, stream_id, term_id, assessment_type_id, current_teacher_id
        )

        if 'error' in status_data:
            flash(f"Error loading class status: {status_data['error']}", "error")
            return redirect(url_for('classteacher.collaborative_marks_dashboard'))

        # Check if current teacher can upload marks for each subject
        for subject in status_data['subjects']:
            subject['can_upload'] = CollaborativeMarksService.can_teacher_upload_subject(
                current_teacher_id, subject['id'], grade_id, stream_id
            )

        # Add navigation data
        status_data['grade_id'] = grade_id
        status_data['stream_id'] = stream_id
        status_data['term_id'] = term_id
        status_data['assessment_type_id'] = assessment_type_id

        return render_template('class_marks_status.html', **status_data)

    except Exception as e:
        print(f"Error in class marks status: {str(e)}")
        flash("Error loading class marks status.", "error")
        return redirect(url_for('classteacher.collaborative_marks_dashboard'))


@classteacher_bp.route('/upload_subject_marks/<int:grade_id>/<int:stream_id>/<int:subject_id>/<int:term_id>/<int:assessment_type_id>')
@classteacher_required
def upload_subject_marks(grade_id, stream_id, subject_id, term_id, assessment_type_id):
    """Upload marks for a specific subject in a class."""
    try:
        current_teacher_id = session.get('teacher_id')
        if not current_teacher_id:
            flash("Please log in to access this page.", "error")
            return redirect(url_for('auth.classteacher_login'))

        # Check if teacher can upload marks for this subject
        if not CollaborativeMarksService.can_teacher_upload_subject(
            current_teacher_id, subject_id, grade_id, stream_id
        ):
            flash("You are not authorized to upload marks for this subject.", "error")
            return redirect(url_for('classteacher.dashboard'))

        # Get required objects
        grade = Grade.query.get(grade_id)
        stream = Stream.query.get(stream_id)
        subject = Subject.query.get(subject_id)
        term = Term.query.get(term_id)
        assessment_type = AssessmentType.query.get(assessment_type_id)

        if not all([grade, stream, subject, term, assessment_type]):
            flash("Invalid parameters for marks upload.", "error")
            return redirect(url_for('classteacher.collaborative_marks_dashboard'))

        # Get students in this stream
        students = Student.query.filter_by(stream_id=stream_id).order_by(Student.name).all()

        # Get existing marks for this subject
        existing_marks = {}
        marks = Mark.query.filter_by(
            subject_id=subject_id,
            term_id=term_id,
            assessment_type_id=assessment_type_id
        ).join(Student).filter(Student.stream_id == stream_id).all()

        for mark in marks:
            existing_marks[mark.student_id] = mark

        # Check if subject is composite
        components = subject.get_components() if subject.is_composite else []

        upload_data = {
            'grade': grade,
            'stream': stream,
            'subject': subject,
            'term': term,
            'assessment_type': assessment_type,
            'students': students,
            'existing_marks': existing_marks,
            'components': components,
            'is_composite': subject.is_composite,
            'grade_id': grade_id,
            'stream_id': stream_id,
            'subject_id': subject_id,
            'term_id': term_id,
            'assessment_type_id': assessment_type_id
        }

        return render_template('upload_subject_marks.html', **upload_data)

    except Exception as e:
        print(f"Error in upload subject marks: {str(e)}")
        flash("Error loading marks upload page.", "error")
        return redirect(url_for('classteacher.collaborative_marks_dashboard'))


@classteacher_bp.route('/submit_subject_marks/<int:grade_id>/<int:stream_id>/<int:subject_id>/<int:term_id>/<int:assessment_type_id>', methods=['POST'])
@classteacher_required
def submit_subject_marks(grade_id, stream_id, subject_id, term_id, assessment_type_id):
    """Handle submission of marks for a specific subject."""
    try:
        current_teacher_id = session.get('teacher_id')
        if not current_teacher_id:
            flash("Please log in to submit marks.", "error")
            return redirect(url_for('auth.classteacher_login'))

        # Check if teacher can upload marks for this subject
        if not CollaborativeMarksService.can_teacher_upload_subject(
            current_teacher_id, subject_id, grade_id, stream_id
        ):
            flash("You are not authorized to upload marks for this subject.", "error")
            return redirect(url_for('classteacher.dashboard'))

        # Get required objects
        grade = Grade.query.get(grade_id)
        stream = Stream.query.get(stream_id)
        subject = Subject.query.get(subject_id)
        term = Term.query.get(term_id)
        assessment_type = AssessmentType.query.get(assessment_type_id)

        if not all([grade, stream, subject, term, assessment_type]):
            flash("Invalid parameters for marks submission.", "error")
            return redirect(url_for('classteacher.collaborative_marks_dashboard'))

        # Get students in this stream
        students = Student.query.filter_by(stream_id=stream_id).order_by(Student.name).all()

        # Process marks submission
        marks_added = 0
        marks_updated = 0
        total_marks = request.form.get('total_marks', type=int, default=100)

        for student in students:
            # Get mark value from form
            mark_key = f"mark_{student.name.replace(' ', '_')}_{subject.id}"
            mark_value = request.form.get(mark_key, '')

            if mark_value and mark_value.replace('.', '').isdigit():
                try:
                    raw_mark = float(mark_value)

                    # Sanitize the raw mark
                    raw_mark, sanitized_total_marks = MarkConversionService.sanitize_raw_mark(raw_mark, total_marks)

                    # Calculate percentage
                    percentage = MarkConversionService.calculate_percentage(raw_mark, sanitized_total_marks)

                    # Check if mark already exists
                    existing_mark = Mark.query.filter_by(
                        student_id=student.id,
                        subject_id=subject.id,
                        term_id=term.id,
                        assessment_type_id=assessment_type.id
                    ).first()

                    if existing_mark:
                        # Update existing mark
                        existing_mark.mark = raw_mark
                        existing_mark.total_marks = sanitized_total_marks
                        existing_mark.raw_mark = raw_mark
                        existing_mark.max_raw_mark = sanitized_total_marks
                        existing_mark.percentage = percentage
                        marks_updated += 1
                    else:
                        # Create new mark
                        new_mark = Mark(
                            student_id=student.id,
                            subject_id=subject.id,
                            term_id=term.id,
                            assessment_type_id=assessment_type.id,
                            grade_id=grade.id,  # Use grade from function parameters
                            stream_id=stream.id,  # Use stream from function parameters
                            mark=raw_mark,
                            total_marks=sanitized_total_marks,
                            raw_mark=raw_mark,
                            raw_total_marks=sanitized_total_marks,  # Use correct field name
                            percentage=percentage
                        )
                        db.session.add(new_mark)
                        marks_added += 1

                except Exception as e:
                    print(f"Error processing mark for {student.name}: {str(e)}")
                    continue

        # Commit changes
        db.session.commit()

        # Update collaborative marks status
        status_result = CollaborativeMarksService.update_marks_status_after_upload(
            grade_id, stream_id, subject_id, term_id, assessment_type_id, current_teacher_id
        )

        # Show success message
        if marks_added > 0 or marks_updated > 0:
            flash(f"Successfully saved {marks_added} new marks and updated {marks_updated} existing marks for {subject.name}.", "success")

            # Add completion status to message
            if status_result.get('is_complete'):
                flash(f"All students now have marks for {subject.name}. Subject is marked as complete!", "success")
        else:
            flash("No marks were processed.", "warning")

        # Redirect to subject report for immediate feedback
        return redirect(url_for('classteacher.subject_report',
                              grade_id=grade_id, stream_id=stream_id, subject_id=subject_id,
                              term_id=term_id, assessment_type_id=assessment_type_id))

    except Exception as e:
        print(f"Error submitting subject marks: {str(e)}")
        flash("Error submitting marks. Please try again.", "error")
        return redirect(url_for('classteacher.collaborative_marks_dashboard'))


# ============================================================================
# ENHANCED GRADE REPORT GENERATION SYSTEM
# ============================================================================

@classteacher_bp.route('/grade_reports_dashboard')
@classteacher_required
def grade_reports_dashboard():
    """Dashboard for generating grade-level reports with multiple streams."""
    try:
        # Get all grades
        grades = Grade.query.all()
        terms = Term.query.all()
        assessment_types = AssessmentType.query.all()

        return render_template('grade_reports_dashboard.html',
                             grades=grades,
                             terms=terms,
                             assessment_types=assessment_types)

    except Exception as e:
        print(f"Error in grade reports dashboard: {str(e)}")
        flash("Error loading grade reports dashboard.", "error")
        return redirect(url_for('classteacher.dashboard'))


@classteacher_bp.route('/grade_streams_status/<grade_name>/<term>/<assessment_type>')
@classteacher_required
def grade_streams_status(grade_name, term, assessment_type):
    """Show status of all streams in a grade for report generation."""
    try:
        # Get grade streams status
        status_data = GradeReportService.get_grade_streams_status(grade_name, term, assessment_type)

        if 'error' in status_data:
            flash(f"Error loading grade status: {status_data['error']}", "error")
            return redirect(url_for('classteacher.grade_reports_dashboard'))

        return render_template('grade_streams_status.html', **status_data)

    except Exception as e:
        print(f"Error in grade streams status: {str(e)}")
        flash("Error loading grade streams status.", "error")
        return redirect(url_for('classteacher.grade_reports_dashboard'))


@classteacher_bp.route('/generate_individual_stream_report/<grade_name>/<stream_name>/<term>/<assessment_type>')
@classteacher_required
def generate_individual_stream_report(grade_name, stream_name, term, assessment_type):
    """Generate report for a single stream."""
    try:
        pdf_file = GradeReportService.generate_individual_stream_report(
            grade_name, stream_name, term, assessment_type
        )

        if not pdf_file:
            flash(f"Failed to generate report for {grade_name} Stream {stream_name}", "error")
            return redirect(url_for('classteacher.grade_streams_status',
                                  grade_name=grade_name, term=term, assessment_type=assessment_type))

        # Return the PDF file
        filename = f"{grade_name}_Stream_{stream_name}_{term}_{assessment_type}_Report.pdf"
        return send_file(
            pdf_file,
            as_attachment=True,
            download_name=filename,
            mimetype='application/pdf'
        )

    except Exception as e:
        print(f"Error generating individual stream report: {str(e)}")
        flash("Error generating stream report.", "error")
        return redirect(url_for('classteacher.grade_streams_status',
                              grade_name=grade_name, term=term, assessment_type=assessment_type))


@classteacher_bp.route('/generate_consolidated_grade_report/<grade_name>/<term>/<assessment_type>')
@classteacher_required
def generate_consolidated_grade_report(grade_name, term, assessment_type):
    """Generate consolidated report for entire grade."""
    try:
        # Get selected streams from query parameters
        selected_streams = request.args.getlist('streams')

        pdf_file = GradeReportService.generate_consolidated_grade_report(
            grade_name, term, assessment_type, selected_streams if selected_streams else None
        )

        if not pdf_file:
            flash(f"Failed to generate consolidated report for Grade {grade_name}", "error")
            return redirect(url_for('classteacher.grade_streams_status',
                                  grade_name=grade_name, term=term, assessment_type=assessment_type))

        # Return the PDF file
        filename = f"Grade_{grade_name}_Consolidated_{term}_{assessment_type}_Report.pdf"
        return send_file(
            pdf_file,
            as_attachment=True,
            download_name=filename,
            mimetype='application/pdf'
        )

    except Exception as e:
        print(f"Error generating consolidated grade report: {str(e)}")
        flash("Error generating consolidated report.", "error")
        return redirect(url_for('classteacher.grade_streams_status',
                              grade_name=grade_name, term=term, assessment_type=assessment_type))


@classteacher_bp.route('/generate_batch_grade_reports/<grade_name>/<term>/<assessment_type>')
@classteacher_required
def generate_batch_grade_reports(grade_name, term, assessment_type):
    """Generate batch reports for a grade (individual streams + consolidated)."""
    try:
        # Get options from query parameters
        include_individual = request.args.get('individual', 'true').lower() == 'true'
        include_consolidated = request.args.get('consolidated', 'true').lower() == 'true'
        selected_streams = request.args.getlist('streams')

        zip_file = GradeReportService.generate_batch_grade_reports(
            grade_name, term, assessment_type,
            include_individual, include_consolidated,
            selected_streams if selected_streams else None
        )

        if not zip_file:
            flash(f"Failed to generate batch reports for Grade {grade_name}", "error")
            return redirect(url_for('classteacher.grade_streams_status',
                                  grade_name=grade_name, term=term, assessment_type=assessment_type))

        # Return the ZIP file
        filename = f"Grade_{grade_name}_Batch_Reports_{term}_{assessment_type}.zip"
        return send_file(
            zip_file,
            as_attachment=True,
            download_name=filename,
            mimetype='application/zip'
        )

    except Exception as e:
        print(f"Error generating batch grade reports: {str(e)}")
        flash("Error generating batch reports.", "error")
        return redirect(url_for('classteacher.grade_streams_status',
                              grade_name=grade_name, term=term, assessment_type=assessment_type))


@classteacher_bp.route('/database_health', methods=['GET', 'POST'])
@classteacher_required
def database_health():
    """Route for checking and maintaining database health."""
    health_results = None
    creation_results = None
    error_message = None
    success_message = None

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'check_health':
            try:
                health_results = check_database_health()
                if health_results['status'] == 'healthy':
                    success_message = "Database health check passed! All systems operational."
                elif health_results['status'] == 'warning':
                    success_message = f"Database health check completed with warnings: {'; '.join(health_results['warnings'])}"
                else:
                    error_message = f"Database health check failed: {'; '.join(health_results['errors'])}"
            except Exception as e:
                error_message = f"Error during health check: {str(e)}"

        elif action == 'create_missing_tables':
            try:
                creation_results = create_missing_tables()
                if creation_results['success']:
                    if creation_results['tables_created']:
                        success_message = f"Successfully created missing tables: {', '.join(creation_results['tables_created'])}"
                    else:
                        success_message = "All required tables already exist."
                else:
                    error_message = f"Failed to create tables: {'; '.join(creation_results['errors'])}"
            except Exception as e:
                error_message = f"Error creating tables: {str(e)}"

    # Always run a basic health check for display
    try:
        if not health_results:
            health_results = check_database_health()
    except Exception as e:
        error_message = f"Error running health check: {str(e)}"
        health_results = {'status': 'error', 'errors': [str(e)]}

    return render_template(
        'database_health.html',
        health_results=health_results,
        creation_results=creation_results,
        error_message=error_message,
        success_message=success_message
    )

@classteacher_bp.route('/report_configuration', methods=['GET', 'POST'])
@classteacher_required
def report_configuration():
    """Configure report settings including staff assignments and term dates."""
    try:
        if request.method == 'POST':
            # Handle form submission
            term_name = request.form.get('term')
            if not term_name:
                flash('Please select a term.', 'error')
                return redirect(url_for('classteacher.report_configuration'))

            # Get term object
            term = Term.query.filter_by(name=term_name).first()
            if not term:
                flash('Invalid term selected.', 'error')
                return redirect(url_for('classteacher.report_configuration'))

            # Prepare configuration data
            config_data = {
                'academic_year': request.form.get('academic_year'),
                'term_start_date': request.form.get('term_start_date'),
                'term_end_date': request.form.get('term_end_date'),
                'closing_date': request.form.get('closing_date'),
                'opening_date': request.form.get('opening_date'),
                'headteacher_id': request.form.get('headteacher_id') or None,
                'deputy_headteacher_id': request.form.get('deputy_headteacher_id') or None,
                'principal_id': request.form.get('principal_id') or None,
                'show_headteacher': 'show_headteacher' in request.form,
                'show_deputy_headteacher': 'show_deputy_headteacher' in request.form,
                'show_principal': 'show_principal' in request.form,
                'show_class_teacher': 'show_class_teacher' in request.form,
                'school_name': request.form.get('school_name'),
                'school_address': request.form.get('school_address'),
                'school_phone': request.form.get('school_phone'),
                'school_email': request.form.get('school_email'),
                'school_website': request.form.get('school_website'),
                'report_footer': request.form.get('report_footer')
            }

            # Convert date strings to date objects
            from datetime import datetime
            date_fields = ['term_start_date', 'term_end_date', 'closing_date', 'opening_date']
            for field in date_fields:
                if config_data[field]:
                    try:
                        config_data[field] = datetime.strptime(config_data[field], '%Y-%m-%d').date()
                    except ValueError:
                        config_data[field] = None

            # Update configuration
            teacher_id = session.get('teacher_id')
            ReportConfigService.update_report_config(term.id, config_data, teacher_id)

            flash('Report configuration updated successfully!', 'success')
            return redirect(url_for('classteacher.report_configuration'))

        # GET request - show configuration form
        # Get all terms for selection
        terms = Term.query.all()

        # Get current term configuration
        current_term = request.args.get('term')
        if not current_term and terms:
            current_term = terms[0].name

        config = None
        if current_term:
            config = ReportConfigService.get_report_config_for_term(current_term)

        # Get all teachers for selection
        teachers = ReportConfigService.get_all_teachers_for_selection()

        # Organize teachers by role for easier selection
        teachers_by_role = {
            'headteacher': [t for t in teachers if 'head' in t['role'].lower()],
            'deputy': [t for t in teachers if 'deputy' in t['role'].lower()],
            'principal': [t for t in teachers if 'principal' in t['role'].lower()],
            'all': teachers
        }

        return render_template('report_configuration.html',
                             terms=terms,
                             current_term=current_term,
                             config=config,
                             teachers=teachers,
                             teachers_by_role=teachers_by_role)

    except Exception as e:
        print(f"Error in report configuration: {e}")
        flash(f'Error loading report configuration: {str(e)}', 'error')
        return redirect(url_for('classteacher.dashboard'))

def cleanup_invalid_marks():
    """Clean up marks that exceed 100% or have invalid values."""
    try:
        from ..services.mark_conversion_service import MarkConversionService

        # First, fix component max marks that are set to 50 (should be 100)
        from ..models.academic import SubjectComponent
        components_to_fix = SubjectComponent.query.filter(SubjectComponent.max_raw_mark == 50).all()

        for component in components_to_fix:
            component.max_raw_mark = 100
            print(f"Updated component {component.name} max_raw_mark from 50 to 100")

        # Find marks with percentages > 100%
        invalid_marks = Mark.query.filter(Mark.percentage > 100).all()

        for mark in invalid_marks:
            # Recalculate percentage using the service
            if mark.raw_total_marks and mark.raw_total_marks > 0:
                # Sanitize the raw mark and max mark
                sanitized_raw_mark, sanitized_max_mark = MarkConversionService.sanitize_raw_mark(
                    mark.raw_mark or mark.mark,
                    mark.raw_total_marks
                )

                # Update the mark with sanitized values
                mark.raw_mark = sanitized_raw_mark
                mark.mark = sanitized_raw_mark  # Update old field name too
                mark.raw_total_marks = sanitized_max_mark
                mark.total_marks = sanitized_max_mark  # Update old field name too
                mark.percentage = MarkConversionService.calculate_percentage(sanitized_raw_mark, sanitized_max_mark)

                print(f"Fixed invalid mark: {mark.id} - {sanitized_raw_mark}/{sanitized_max_mark} = {mark.percentage}%")

        # Find component marks with percentages > 100%
        from ..models.academic import ComponentMark
        invalid_component_marks = ComponentMark.query.filter(ComponentMark.percentage > 100).all()

        for comp_mark in invalid_component_marks:
            if comp_mark.max_raw_mark and comp_mark.max_raw_mark > 0:
                # Sanitize the component mark
                sanitized_raw_mark, sanitized_max_mark = MarkConversionService.sanitize_raw_mark(
                    comp_mark.raw_mark,
                    comp_mark.max_raw_mark
                )

                # Update the component mark with sanitized values
                comp_mark.raw_mark = sanitized_raw_mark
                comp_mark.max_raw_mark = sanitized_max_mark
                comp_mark.percentage = MarkConversionService.calculate_percentage(sanitized_raw_mark, sanitized_max_mark)

                print(f"Fixed invalid component mark: {comp_mark.id} - {sanitized_raw_mark}/{sanitized_max_mark} = {comp_mark.percentage}%")

        db.session.commit()
        print("Database cleanup completed successfully")

    except Exception as e:
        print(f"Error during database cleanup: {str(e)}")
        db.session.rollback()


# TEMPORARY ROUTE TO FIX KEVIN'S ASSIGNMENT
@classteacher_bp.route('/fix_kevin_assignment')
@dev_only
def fix_kevin_assignment():
    """Temporary route to fix Kevin's class teacher assignment."""
    try:
        from ..services.staff_assignment_service import StaffAssignmentService
        from ..models.assignment import TeacherSubjectAssignment
        from ..models import Teacher, Grade, Stream, Subject
        from sqlalchemy import and_
        
        debug_info = []
        
        # Find Kevin
        kevin = Teacher.query.filter_by(username='kevin').first()
        if kevin:
            debug_info.append(f"✅ Kevin found: ID={kevin.id}, Username={kevin.username}, Role={kevin.role}, Stream ID={kevin.stream_id}")
        else:
            debug_info.append("❌ Kevin not found!")
            return f"<h1>Error</h1><p>Kevin not found!</p><pre>{'<br>'.join(debug_info)}</pre>"
        
        # Find Grade 9
        grade_9 = Grade.query.filter_by(name='Grade 9').first()
        if grade_9:
            debug_info.append(f"✅ Grade 9 found: ID={grade_9.id}")
        else:
            debug_info.append("❌ Grade 9 not found!")
        
        # Find all streams for Grade 9
        grade_9_streams = Stream.query.filter_by(grade_id=grade_9.id).all() if grade_9 else []
        debug_info.append(f"📋 Grade 9 streams: {[f'{s.name} (ID: {s.id})' for s in grade_9_streams]}")
        
        # Find Stream B for Grade 9
        stream_b = Stream.query.filter_by(name='B', grade_id=grade_9.id).first() if grade_9 else None
        if stream_b:
            debug_info.append(f"✅ Stream B found: ID={stream_b.id}, Grade ID={stream_b.grade_id}")
        else:
            debug_info.append("❌ Stream B for Grade 9 not found!")
        
        # Check Kevin's current assignments
        all_assignments = TeacherSubjectAssignment.query.filter_by(teacher_id=kevin.id).all()
        debug_info.append(f"📚 Kevin has {len(all_assignments)} total assignments:")
        for i, assignment in enumerate(all_assignments, 1):
            grade = Grade.query.get(assignment.grade_id) if assignment.grade_id else None
            stream = Stream.query.get(assignment.stream_id) if assignment.stream_id else None
            subject = Subject.query.get(assignment.subject_id) if assignment.subject_id else None
            debug_info.append(f"   {i}. {subject.name if subject else 'Unknown'} - Grade {grade.name if grade else 'Unknown'} Stream {stream.name if stream else 'Unknown'} (Class Teacher: {assignment.is_class_teacher})")
        
        # Try to fix using the service
        print("🔧 Attempting to fix Kevin's class teacher assignment...")
        success = StaffAssignmentService.fix_kevin_class_teacher_assignment()
        
        # Also manually set Kevin's stream_id if it's None
        if kevin.stream_id is None and stream_b:
            kevin.stream_id = stream_b.id
            db.session.commit()
            debug_info.append(f"🔧 Manually set Kevin's stream_id to {stream_b.id}")
        
        if success:
            debug_info.append("✅ StaffAssignmentService.fix_kevin_class_teacher_assignment() returned True")
            
            # Check assignments again after fix
            updated_assignments = TeacherSubjectAssignment.query.filter_by(teacher_id=kevin.id).all()
            debug_info.append(f"📚 After fix, Kevin has {len(updated_assignments)} assignments:")
            for i, assignment in enumerate(updated_assignments, 1):
                grade = Grade.query.get(assignment.grade_id) if assignment.grade_id else None
                stream = Stream.query.get(assignment.stream_id) if assignment.stream_id else None
                subject = Subject.query.get(assignment.subject_id) if assignment.subject_id else None
                debug_info.append(f"   {i}. {subject.name if subject else 'Unknown'} - Grade {grade.name if grade else 'Unknown'} Stream {stream.name if stream else 'Unknown'} (Class Teacher: {assignment.is_class_teacher})")
            
            # Check Kevin's teacher record
            kevin_updated = Teacher.query.filter_by(username='kevin').first()
            debug_info.append(f"🔄 Kevin's updated stream_id: {kevin_updated.stream_id}")
            
            message = "✅ Kevin's class teacher assignment has been fixed successfully! Please refresh the dashboard."
            debug_info.append(message)
        else:
            debug_info.append("❌ StaffAssignmentService.fix_kevin_class_teacher_assignment() returned False")
            message = "❌ Failed to fix Kevin's assignment. Check the details below."
        
        # Test FlexibleMarksService functions
        from ..services.flexible_marks_service import FlexibleMarksService
        
        debug_info.append(f"\n🔧 FlexibleMarksService Tests:")
        
        # Test can_teacher_access_classteacher_portal
        can_access = FlexibleMarksService.can_teacher_access_classteacher_portal(kevin.id)
        debug_info.append(f"   - can_teacher_access_classteacher_portal: {can_access}")
        
        # Test get_teacher_all_class_assignments
        class_assignments = FlexibleMarksService.get_teacher_all_class_assignments(kevin.id)
        debug_info.append(f"   - get_teacher_all_class_assignments: {len(class_assignments)} classes")
        for i, ca in enumerate(class_assignments, 1):
            debug_info.append(f"     {i}. Grade {ca['grade_name']} Stream {ca['stream_name']} - {len(ca['subjects'])} subjects (Class Teacher: {ca['is_class_teacher']})")
        
        # Test get_teacher_portal_summary
        portal_summary = FlexibleMarksService.get_teacher_portal_summary(kevin.id)
        debug_info.append(f"   - get_teacher_portal_summary:")
        debug_info.append(f"     * can_access_portal: {portal_summary.get('can_access_portal', 'N/A')}")
        debug_info.append(f"     * portal_type: {portal_summary.get('portal_type', 'N/A')}")
        debug_info.append(f"     * total_classes: {portal_summary.get('total_classes', 'N/A')}")
        debug_info.append(f"     * total_subjects: {portal_summary.get('total_subjects', 'N/A')}")
        debug_info.append(f"     * is_actual_class_teacher: {portal_summary.get('is_actual_class_teacher', 'N/A')}")
        if 'error' in portal_summary:
            debug_info.append(f"     * ERROR: {portal_summary['error']}")
        
        return f"<h1>{'Success' if success else 'Error'}</h1><p>{message}</p><h2>Debug Info:</h2><pre>{'<br>'.join(debug_info)}</pre><p><a href='/classteacher/'>Go to Dashboard</a></p>"
    
    except Exception as e:
        error_message = f"❌ Error: {str(e)}"
        print(error_message)
        return f"<h1>Error</h1><p>{error_message}</p><p><a href='/classteacher/'>Go to Dashboard</a></p>"


@classteacher_bp.route('/upload', methods=['GET', 'POST'])
@classteacher_required
def upload_marks():
    """Handle both manual marks entry and bulk CSV upload"""
    try:
        # Get teacher info from session
        teacher_id = session.get('teacher_id')
        if not teacher_id:
            flash('Teacher session not found. Please log in again.', 'error')
            return redirect(url_for('auth.login'))
            
        teacher = Teacher.query.get(teacher_id)
        if not teacher:
            flash('Teacher profile not found.', 'error')
            return redirect(url_for('classteacher.dashboard'))

        # Get basic data needed for the form
        grades = Grade.query.all()
        subjects = Subject.query.all()
        assessment_types = AssessmentType.query.filter_by(is_active=True).all()
        
        # Initialize variables
        students = []
        selected_grade = None
        selected_stream = None
        selected_subject = None
        selected_assessment = None
        max_marks = None
        
        if request.method == 'POST':
            print(f"🔍 POST request received")
            print(f"🔍 Form data: {dict(request.form)}")
            
            # Handle load students request
            if 'load_students' in request.form:
                print(f"🔍 Load students request detected")
                selected_grade = request.form.get('grade')
                selected_stream = request.form.get('stream')
                selected_subject = request.form.get('subject')
                selected_assessment = request.form.get('assessment_type')
                max_marks = request.form.get('max_marks')
                
                print(f"🔍 Form values: grade={selected_grade}, stream={selected_stream}, subject={selected_subject}, assessment={selected_assessment}, max_marks={max_marks}")
                
                if all([selected_grade, selected_stream, selected_subject, selected_assessment, max_marks]):
                    print(f"🔍 All fields filled, loading students...")
                    # Load students for this stream (stream already belongs to a grade)
                    students = Student.query.filter_by(stream_id=selected_stream).all()
                    print(f"🔍 Found {len(students)} students in stream {selected_stream}")
                    
                    if len(students) == 0:
                        flash(f'No students found in the selected grade and stream.', 'warning')
                    
                    # Check for existing marks
                    for student in students:
                        existing_mark = Mark.query.filter_by(
                            student_id=student.id,
                            subject_id=selected_subject,
                            assessment_type_id=selected_assessment  # Fixed: should be assessment_type_id
                        ).first()
                        student.existing_mark = existing_mark.raw_mark if existing_mark else None
                        student.existing_percentage = existing_mark.percentage if existing_mark else None
                else:
                    print(f"🔍 Missing fields detected")
                    flash('Please fill in all required fields.', 'error')
            
            # Handle save marks request
            elif 'save_marks' in request.form:
                selected_grade = request.form.get('grade')
                selected_stream = request.form.get('stream')
                selected_subject = request.form.get('subject')
                selected_assessment = request.form.get('assessment_type')
                max_marks = float(request.form.get('max_marks'))
                
                print(f"🔍 Save marks: grade={selected_grade}, stream={selected_stream}, subject={selected_subject}, assessment={selected_assessment}, max_marks={max_marks}")
                
                # Get valid term ID from database instead of hardcoding
                from ..models.academic import Term
                default_term = Term.query.first()
                if not default_term:
                    flash('No terms found in database. Please set up academic terms first.', 'error')
                    return redirect(url_for('classteacher.upload_marks'))
                term_id = default_term.id
                print(f"🔍 Using term_id: {term_id}")
                
                # Process each student's marks
                students_updated = 0
                for key, value in request.form.items():
                    if key.startswith('marks_') and value.strip():
                        student_id = key.replace('marks_', '')
                        try:
                            raw_mark = float(value)
                            if 0 <= raw_mark <= max_marks:
                                from ..services.mark_conversion_service import MarkConversionService
                                percentage = MarkConversionService.calculate_percentage(raw_mark, max_marks)
                                
                                # Check for existing mark with no-autoflush to prevent premature flush
                                with db.session.no_autoflush:
                                    existing_mark = Mark.query.filter_by(
                                        student_id=student_id,
                                        subject_id=selected_subject,
                                        assessment_type_id=selected_assessment,
                                        term_id=term_id  # Include term_id in the filter
                                    ).first()
                                
                                if existing_mark:
                                    existing_mark.raw_mark = raw_mark
                                    existing_mark.raw_total_marks = max_marks
                                    existing_mark.percentage = percentage
                                    existing_mark.mark = raw_mark  # For backward compatibility
                                    existing_mark.total_marks = max_marks  # For backward compatibility
                                else:
                                    new_mark = Mark(
                                        student_id=student_id,
                                        subject_id=selected_subject,
                                        assessment_type_id=selected_assessment,  # Fixed: use assessment_type_id
                                        grade_id=selected_grade,
                                        stream_id=selected_stream,
                                        term_id=term_id,  # Use valid term_id from database
                                        raw_mark=raw_mark,
                                        raw_total_marks=max_marks,
                                        percentage=percentage,
                                        mark=raw_mark,  # For backward compatibility
                                        total_marks=max_marks,  # For backward compatibility
                                        is_uploaded=True,
                                        uploaded_by_teacher_id=teacher_id,
                                        upload_date=db.func.current_timestamp()
                                    )
                                    db.session.add(new_mark)
                                
                                students_updated += 1
                                print(f"🔍 Updated student {student_id}: {raw_mark}/{max_marks} = {percentage:.1f}%")
                        except ValueError as e:
                            print(f"🚨 Error processing mark for student {student_id}: {e}")
                            continue
                
                db.session.commit()
                
                # Get subject name for better feedback
                subject_name = Subject.query.get(selected_subject).name if selected_subject else "Unknown Subject"
                flash(f'✅ Successfully saved marks for {students_updated} students in {subject_name}!', 'success')
                
                # Option 1: Redirect to Subject Report for immediate feedback and validation
                return redirect(url_for('classteacher.subject_report',
                                      grade_id=selected_grade,
                                      stream_id=selected_stream,
                                      subject_id=selected_subject,
                                      term_id=term_id,
                                      assessment_type_id=selected_assessment))
            
            # Handle bulk upload
            elif 'bulk_upload' in request.form:
                if 'bulk_file' not in request.files:
                    flash('No file selected.', 'error')
                else:
                    file = request.files['bulk_file']
                    if file.filename == '':
                        flash('No file selected.', 'error')
                    elif file and file.filename.endswith('.csv'):
                        try:
                            # Process CSV file
                            import csv
                            import io
                            
                            stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
                            csv_input = csv.DictReader(stream)
                            
                            uploaded_count = 0
                            for row in csv_input:
                                # Expected CSV format: student_id, subject_id, assessment_type, marks, max_marks
                                try:
                                    student_id = row['student_id']
                                    subject_id = row['subject_id']
                                    assessment_type = row['assessment_type']
                                    marks = float(row['marks'])
                                    max_marks = float(row['max_marks'])
                                    
                                    # Validate and save
                                    if 0 <= marks <= max_marks:
                                        existing_mark = Mark.query.filter_by(
                                            student_id=student_id,
                                            subject_id=subject_id,
                                            assessment_type=assessment_type
                                        ).first()
                                        
                                        if existing_mark:
                                            existing_mark.marks = marks
                                        else:
                                            new_mark = Mark(
                                                student_id=student_id,
                                                subject_id=subject_id,
                                                assessment_type=assessment_type,
                                                marks=marks,
                                                max_marks=max_marks
                                            )
                                            db.session.add(new_mark)
                                        
                                        uploaded_count += 1
                                except (KeyError, ValueError) as e:
                                    continue
                            
                            db.session.commit()
                            flash(f'Successfully uploaded marks for {uploaded_count} entries.', 'success')
                            return redirect(url_for('classteacher.dashboard'))
                            
                        except Exception as e:
                            flash(f'Error processing file: {str(e)}', 'error')
                    else:
                        flash('Please upload a CSV file.', 'error')
        
        # Get school info
        from ..services.school_config_service import SchoolConfigService
        school_info = SchoolConfigService.get_school_info_dict()
        
        return render_template(
            'classteacher/upload.html',
            school_info=school_info,
            grades=grades,
            subjects=subjects,
            assessment_types=assessment_types,
            students=students,
            selected_grade=selected_grade,
            selected_stream=selected_stream,
            selected_subject=selected_subject,
            selected_assessment=selected_assessment,
            max_marks=max_marks
        )
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"Error in upload_marks: {str(e)}")
        logger.error(f"Full traceback: {error_details}")
        print(f"🚨 EXCEPTION in upload_marks: {str(e)}")
        print(f"🚨 Full traceback: {error_details}")
        flash(f'An error occurred while processing the upload: {str(e)}', 'error')
        return redirect(url_for('classteacher.dashboard'))


@classteacher_bp.route('/upload_test')
@dev_only
def upload_test():
    """Simple test route to verify routing works"""
    return "<h1>Upload Test Route Works!</h1><p><a href='/classteacher/'>Back to Dashboard</a></p>"


@classteacher_bp.route('/upload_class_marks/<int:grade_id>/<int:stream_id>/<int:term_id>/<int:assessment_type_id>')
@classteacher_required
def upload_class_marks(grade_id, stream_id, term_id, assessment_type_id):
    """Enhanced marks upload - Step 2: Subject Selection for a Class"""
    try:
        current_teacher_id = session.get('teacher_id')
        if not current_teacher_id:
            flash("Please log in to access this page.", "error")
            return redirect(url_for('auth.classteacher_login'))

        # Get required objects
        grade = Grade.query.get(grade_id)
        stream = Stream.query.get(stream_id)
        term = Term.query.get(term_id)
        assessment_type = AssessmentType.query.get(assessment_type_id)

        if not all([grade, stream, term, assessment_type]):
            flash("Invalid parameters for marks upload.", "error")
            return redirect(url_for('classteacher.upload_marks'))

        # Check if teacher can manage this class
        assignment_summary = RoleBasedDataService.get_teacher_assignments_summary(current_teacher_id, 'classteacher')
        class_assignments = assignment_summary.get('class_teacher_assignments', [])

        can_manage_class = any(
            assignment['grade_id'] == grade_id and assignment['stream_id'] == stream_id
            for assignment in class_assignments
        )

        if not can_manage_class:
            flash("You are not authorized to upload marks for this class.", "error")
            return redirect(url_for('classteacher.upload_marks'))

        # Get subjects for this grade's education level using the new service
        from ..services.composite_subject_service import CompositeSubjectService
        subjects = CompositeSubjectService.get_subjects_for_upload(grade.education_level)

        # Get students in this stream
        students = Student.query.filter_by(stream_id=stream_id).order_by(Student.name).all()

        # Check marks status for each subject
        subjects_status = []
        for subject in subjects:
            # Count existing marks for this subject
            marks_count = Mark.query.filter_by(
                subject_id=subject.id,
                term_id=term_id,
                assessment_type_id=assessment_type_id
            ).join(Student).filter(Student.stream_id == stream_id).count()

            # Check if teacher can upload for this subject
            can_upload = CollaborativeMarksService.can_teacher_upload_subject(
                current_teacher_id, subject.id, grade_id, stream_id
            )

            subjects_status.append({
                'subject': subject,
                'marks_count': marks_count,
                'total_students': len(students),
                'is_complete': marks_count == len(students),
                'completion_percentage': (marks_count / len(students) * 100) if students else 0,
                'can_upload': can_upload,
                'status_text': 'Complete' if marks_count == len(students) else f'{marks_count}/{len(students)} students'
            })

        return render_template(
            'classteacher/class_marks_status.html',
            grade=grade,
            stream=stream,
            term=term,
            assessment_type=assessment_type,
            students=students,
            subjects_status=subjects_status,
            grade_id=grade_id,
            stream_id=stream_id,
            term_id=term_id,
            assessment_type_id=assessment_type_id
        )

    except Exception as e:
        print(f"Error in upload class marks: {str(e)}")
        flash("Error loading class marks page.", "error")
        return redirect(url_for('classteacher.upload_marks'))


@classteacher_bp.route('/upload_single_subject_marks/<int:grade_id>/<int:stream_id>/<int:subject_id>/<int:term_id>/<int:assessment_type_id>')
@classteacher_required
def upload_single_subject_marks(grade_id, stream_id, subject_id, term_id, assessment_type_id):
    """Enhanced marks upload - Step 3: Individual Subject Marks Entry (same as subject teacher)"""
    try:
        current_teacher_id = session.get('teacher_id')
        if not current_teacher_id:
            flash("Please log in to access this page.", "error")
            return redirect(url_for('auth.classteacher_login'))

        # Check if teacher can upload marks for this subject
        if not CollaborativeMarksService.can_teacher_upload_subject(
            current_teacher_id, subject_id, grade_id, stream_id
        ):
            flash("You are not authorized to upload marks for this subject.", "error")
            return redirect(url_for('classteacher.upload_class_marks',
                                  grade_id=grade_id, stream_id=stream_id,
                                  term_id=term_id, assessment_type_id=assessment_type_id))

        # Get required objects
        grade = Grade.query.get(grade_id)
        stream = Stream.query.get(stream_id)
        subject = Subject.query.get(subject_id)
        term = Term.query.get(term_id)
        assessment_type = AssessmentType.query.get(assessment_type_id)

        if not all([grade, stream, subject, term, assessment_type]):
            flash("Invalid parameters for marks upload.", "error")
            return redirect(url_for('classteacher.upload_marks'))

        # Get students in this stream
        students = Student.query.filter_by(stream_id=stream_id).order_by(Student.name).all()

        # Get existing marks for this subject
        existing_marks = {}
        marks = Mark.query.filter_by(
            subject_id=subject_id,
            term_id=term_id,
            assessment_type_id=assessment_type_id
        ).join(Student).filter(Student.stream_id == stream_id).all()

        for mark in marks:
            existing_marks[mark.student_id] = mark

        # Check if subject is composite
        components = subject.get_components() if subject.is_composite else []

        upload_data = {
            'grade': grade,
            'stream': stream,
            'subject': subject,
            'term': term,
            'assessment_type': assessment_type,
            'students': students,
            'existing_marks': existing_marks,
            'components': components,
            'is_composite': subject.is_composite,
            'grade_id': grade_id,
            'stream_id': stream_id,
            'subject_id': subject_id,
            'term_id': term_id,
            'assessment_type_id': assessment_type_id
        }

        return render_template('classteacher/upload_subject_marks.html', **upload_data)

    except Exception as e:
        print(f"Error in upload single subject marks: {str(e)}")
        flash("Error loading marks upload page.", "error")
        return redirect(url_for('classteacher.upload_marks'))


@classteacher_bp.route('/submit_single_subject_marks/<int:grade_id>/<int:stream_id>/<int:subject_id>/<int:term_id>/<int:assessment_type_id>', methods=['POST'])
@classteacher_required
def submit_single_subject_marks(grade_id, stream_id, subject_id, term_id, assessment_type_id):
    """Enhanced marks submission - Step 4: Process Individual Subject Marks (same as subject teacher)"""
    try:
        current_teacher_id = session.get('teacher_id')
        if not current_teacher_id:
            flash("Please log in to submit marks.", "error")
            return redirect(url_for('auth.classteacher_login'))

        # Check if teacher can upload marks for this subject
        if not CollaborativeMarksService.can_teacher_upload_subject(
            current_teacher_id, subject_id, grade_id, stream_id
        ):
            flash("You are not authorized to upload marks for this subject.", "error")
            return redirect(url_for('classteacher.upload_class_marks',
                                  grade_id=grade_id, stream_id=stream_id,
                                  term_id=term_id, assessment_type_id=assessment_type_id))

        # Get required objects
        grade = Grade.query.get(grade_id)
        stream = Stream.query.get(stream_id)
        subject = Subject.query.get(subject_id)
        term = Term.query.get(term_id)
        assessment_type = AssessmentType.query.get(assessment_type_id)

        if not all([grade, stream, subject, term, assessment_type]):
            flash("Invalid parameters for marks submission.", "error")
            return redirect(url_for('classteacher.upload_marks'))

        # Get students in this stream
        students = Student.query.filter_by(stream_id=stream_id).order_by(Student.name).all()

        # Process marks submission
        marks_added = 0
        marks_updated = 0
        total_marks = request.form.get('total_marks', type=int, default=100)

        for student in students:
            # Get mark value from form
            mark_key = f"mark_{student.name.replace(' ', '_')}_{subject.id}"
            mark_value = request.form.get(mark_key, '')

            if mark_value and mark_value.replace('.', '').isdigit():
                try:
                    raw_mark = float(mark_value)

                    # Sanitize the raw mark
                    raw_mark, sanitized_total_marks = MarkConversionService.sanitize_raw_mark(raw_mark, total_marks)

                    # Calculate percentage
                    percentage = MarkConversionService.calculate_percentage(raw_mark, sanitized_total_marks)

                    # Check if mark already exists
                    existing_mark = Mark.query.filter_by(
                        student_id=student.id,
                        subject_id=subject.id,
                        term_id=term.id,
                        assessment_type_id=assessment_type.id
                    ).first()

                    if existing_mark:
                        # Update existing mark
                        existing_mark.mark = raw_mark
                        existing_mark.total_marks = sanitized_total_marks
                        existing_mark.raw_mark = raw_mark
                        existing_mark.max_raw_mark = sanitized_total_marks
                        existing_mark.percentage = percentage
                        marks_updated += 1
                    else:
                        # Create new mark
                        new_mark = Mark(
                            student_id=student.id,
                            subject_id=subject.id,
                            term_id=term.id,
                            assessment_type_id=assessment_type.id,
                            grade_id=grade.id,
                            stream_id=stream.id,
                            mark=raw_mark,
                            total_marks=sanitized_total_marks,
                            raw_mark=raw_mark,
                            raw_total_marks=sanitized_total_marks,
                            percentage=percentage
                        )
                        db.session.add(new_mark)
                        marks_added += 1

                except Exception as e:
                    print(f"Error processing mark for {student.name}: {str(e)}")
                    continue

        # Commit changes
        db.session.commit()

        # Update collaborative marks status
        status_result = CollaborativeMarksService.update_marks_status_after_upload(
            grade_id, stream_id, subject_id, term_id, assessment_type_id, current_teacher_id
        )

        # Show success message
        if marks_added > 0 or marks_updated > 0:
            flash(f"Successfully saved {marks_added} new marks and updated {marks_updated} existing marks for {subject.name}.", "success")

            # Add completion status to message
            if status_result.get('is_complete'):
                flash(f"All students now have marks for {subject.name}. Subject is marked as complete!", "success")
        else:
            flash("No marks were processed.", "warning")

        # Redirect back to class marks status
        return redirect(url_for('classteacher.upload_class_marks',
                              grade_id=grade_id, stream_id=stream_id,
                              term_id=term_id, assessment_type_id=assessment_type_id))

    except Exception as e:
        print(f"Error submitting single subject marks: {str(e)}")
        flash("Error submitting marks. Please try again.", "error")
        return redirect(url_for('classteacher.upload_marks'))


@classteacher_bp.route('/download_template')
def download_template():
    """Download CSV template for bulk marks upload"""
    try:
        import csv
        from flask import make_response
        import io
        
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write header
        writer.writerow(['student_id', 'subject_id', 'assessment_type', 'marks', 'max_marks'])
        
        # Write sample data with comments
        writer.writerow(['# Sample data - replace with actual values'])
        writer.writerow(['1', '1', 'CAT 1', '85', '100'])
        writer.writerow(['2', '1', 'CAT 1', '78', '100'])
        writer.writerow(['3', '1', 'CAT 1', '92', '100'])
        
        output.seek(0)
        
        response = make_response(output.getvalue())
        response.headers['Content-Type'] = 'text/csv'
        response.headers['Content-Disposition'] = 'attachment; filename=marks_template.csv'
        
        return response
        
    except Exception as e:
        logger.error(f"Error generating template: {str(e)}")
        flash('Error generating template file.', 'error')
        return redirect(url_for('classteacher.upload_marks'))


@classteacher_bp.route('/subject_report/<int:grade_id>/<int:stream_id>/<int:subject_id>/<int:term_id>/<int:assessment_type_id>')
@secure_endpoint(roles=['headteacher','classteacher','teacher'], rate=(40,60), audit_event='report.subject.view')
def subject_report(grade_id, stream_id, subject_id, term_id, assessment_type_id):
    """Generate subject-specific report with grading analysis.

    Hardened for OWASP Top 10 aspects:
    - A01: Broken Access Control -> Verify teacher assignment to subject when role == 'teacher'.
    - A03: Injection -> Path params are int via converter; use safe_get & parameterized queries only.
    - A05: Security Misconfiguration -> Unified error handling, logging, rate limiting.
    - A07: Identification & AuthZ -> Explicit authorization check with clear denied response.
    - A09: Logging & Monitoring -> Structured security log entries for failures.
    - A03/A04: Input validation -> Existence checks on all referenced entities.
    """

    from flask import current_app
    log = current_app.logger

    def _wants_json():
        return (request.accept_mimetypes.best == 'application/json' and
                request.accept_mimetypes['application/json'] >= request.accept_mimetypes['text/html']) or request.is_json
    start_time = time.time()

    try:
        # Fetch objects safely (avoid deprecated Query.get warnings elsewhere)
        grade_obj = safe_get(Grade, grade_id)
        stream_obj = safe_get(Stream, stream_id)
        subject_obj = safe_get(Subject, subject_id)
        term_obj = safe_get(Term, term_id)
        assessment_type_obj = safe_get(AssessmentType, assessment_type_id)

        missing = [name for name, obj in [
            ('grade', grade_obj), ('stream', stream_obj), ('subject', subject_obj),
            ('term', term_obj), ('assessment_type', assessment_type_obj)
        ] if obj is None]
        if missing:
            log.warning("subject_report.invalid_parameters", extra={
                'grade_id': grade_id, 'stream_id': stream_id, 'subject_id': subject_id,
                'term_id': term_id, 'assessment_type_id': assessment_type_id, 'missing': missing
            })
            if _wants_json():
                return error_response('INVALID_REFERENCE', 'One or more referenced entities were not found.', 400, details={'missing': missing})
            flash("Invalid parameters for report generation", "error")
            return redirect(url_for('classteacher.dashboard'))

        # Authorization: if plain subject teacher ensure assignment exists
        role = session.get('role')
        teacher_id = session.get('teacher_id')
        if role == 'teacher' and teacher_id:
            assignment = TeacherSubjectAssignment.query.filter_by(
                teacher_id=teacher_id,
                subject_id=subject_id,
                grade_id=grade_id
            ).filter(
                (TeacherSubjectAssignment.stream_id == stream_id) | (TeacherSubjectAssignment.stream_id == None)
            ).first()
            if not assignment:
                log.warning("subject_report.unauthorized_access", extra={
                    'teacher_id': teacher_id, 'subject_id': subject_id,
                    'grade_id': grade_id, 'stream_id': stream_id
                })
                if _wants_json():
                    return error_response('FORBIDDEN', 'You are not assigned to this subject for the specified class/stream.', 403)
                flash('You are not authorized to view this subject report.', 'error')
                return redirect(url_for('classteacher.dashboard'))

        # Query marks (JOIN only on Student to avoid N+1 on names)
        marks = (Mark.query.filter_by(
            grade_id=grade_id,
            stream_id=stream_id,
            subject_id=subject_id,
            term_id=term_id,
            assessment_type_id=assessment_type_id
        ).join(Student).all())

        if not marks:
            log.info("subject_report.no_marks", extra={
                'grade_id': grade_id, 'stream_id': stream_id, 'subject_id': subject_id,
                'term_id': term_id, 'assessment_type_id': assessment_type_id
            })
            if _wants_json():
                return error_response('NO_MARKS', 'No marks found for this subject and assessment.', 404)
            flash("No marks found for this subject and assessment", "warning")
            return redirect(url_for('classteacher.dashboard'))

        # Compute statistics efficiently
        total_marks_list = [m.raw_mark for m in marks if m.raw_mark is not None]
        max_possible = marks[0].raw_total_marks if marks[0].raw_total_marks else 100
        total_marks_sum = sum(total_marks_list)
        stats_count = len(total_marks_list)

        statistics = {
            'total_students': len(marks),
            'students_with_marks': stats_count,
            'average': total_marks_sum / stats_count if stats_count else 0,
            'highest': max(total_marks_list) if stats_count else 0,
            'lowest': min(total_marks_list) if stats_count else 0,
            'max_possible': max_possible,
            'average_percentage': (total_marks_sum / stats_count / max_possible * 100) if stats_count and max_possible else 0
        }

        # Grade distribution (CBC system)
        grade_distribution = {k: 0 for k in ['EE1','EE2','ME1','ME2','AE1','AE2','BE1','BE2']}
        for m in marks:
            p = m.percentage
            if p is None:
                continue
            if p >= 90: grade_distribution['EE1'] += 1
            elif p >= 75: grade_distribution['EE2'] += 1
            elif p >= 58: grade_distribution['ME1'] += 1
            elif p >= 41: grade_distribution['ME2'] += 1
            elif p >= 31: grade_distribution['AE1'] += 1
            elif p >= 21: grade_distribution['AE2'] += 1
            elif p >= 11: grade_distribution['BE1'] += 1
            else: grade_distribution['BE2'] += 1

        from ..utils.performance import get_performance_category
        students_data = [{
            'id': m.student.id,
            'name': m.student.name,
            'admission_number': m.student.admission_number,
            'raw_mark': m.raw_mark,
            'percentage': m.percentage,
            'grade': get_performance_category(m.percentage) if m.percentage is not None else 'N/A'
        } for m in marks]
        students_data.sort(key=lambda x: x['percentage'] if x['percentage'] is not None else 0, reverse=True)

        from ..services.school_config_service import SchoolConfigService
        school_info = SchoolConfigService.get_school_info_dict()

        report_data = {
            'school_info': school_info,
            'grade': grade_obj.name,
            'stream': stream_obj.name,
            'subject': subject_obj.name,
            'term': term_obj.name,
            'assessment_type': assessment_type_obj.name,
            'statistics': statistics,
            'grade_distribution': grade_distribution,
            'students': students_data,
            'report_title': f'{subject_obj.name} Report - {grade_obj.name} {stream_obj.name}',
            'report_subtitle': f'{term_obj.name} • {assessment_type_obj.name} • Generated: {datetime.now().strftime("%B %d, %Y")}',
            'show_actions': True
        }

        log.info("subject_report.render", extra={
            'subject_id': subject_id, 'grade_id': grade_id, 'stream_id': stream_id,
            'students': len(students_data)
        })
        if _wants_json():
            return jsonify({'report': report_data})
        try:
            elapsed_ms = int((time.time() - start_time) * 1000)
            from ..security_helpers import audit_log
            audit_log('report.subject.view:metrics', elapsed_ms=elapsed_ms, students=len(students_data))
        except Exception:
            pass
        return render_template('classteacher/subject_report.html', **report_data)

    except Exception as e:
        log.exception("subject_report.error")
        if _wants_json():
            return error_response('REPORT_ERROR', 'Unexpected error generating subject report.', 500)
        flash(f"Error generating subject report: {str(e)}", "error")
        return redirect(url_for('classteacher.dashboard'))


def get_letter_grade(percentage):
    """Convert percentage to letter grade."""
    if percentage >= 80: return 'A'
    elif percentage >= 70: return 'B'
    elif percentage >= 60: return 'C'
    elif percentage >= 50: return 'D'
    elif percentage >= 40: return 'E'
    else: return 'F'


# TEST ROUTE - Remove this after testing
@classteacher_bp.route('/test-analytics')
@dev_only
def test_analytics_dashboard():
    """Test analytics page without authentication for debugging."""
    print("🔍 TEST ANALYTICS ROUTE HIT!")
    
    try:
        # Create a mock teacher for testing
        class MockTeacher:
            def __init__(self):
                self.first_name = "Test"
                self.last_name = "Teacher"
                self.id = 1
                
        teacher = MockTeacher()

        # Create test analytics data
        analytics_data = {
            'summary': {
                'students_analyzed': 5,
                'subjects_analyzed': 3,
                'best_subject_average': 85.5,
                'top_student_average': 92.0,
                'has_sufficient_data': True
            },
            'top_performers': [
                {
                    'name': 'John Doe',
                    'student_id': '001',
                    'admission_number': '001',
                    'grade': 'Grade 8',
                    'stream': 'A',
                    'assessment_type': 'Mid Term',
                    'term': 'Term 1',
                    'average_percentage': 92.0,
                    'total_marks_obtained': 460,
                    'total_marks_possible': 500,
                    'grade_letter': 'A',
                    'min_percentage': 85.0,
                    'max_percentage': 98.0,
                    'subjects_count': 5
                },
                {
                    'name': 'Jane Smith',
                    'student_id': '002',
                    'admission_number': '002',
                    'grade': 'Grade 8',
                    'stream': 'A',
                    'assessment_type': 'Mid Term',
                    'term': 'Term 1',
                    'average_percentage': 89.5,
                    'total_marks_obtained': 447,
                    'total_marks_possible': 500,
                    'grade_letter': 'A',
                    'min_percentage': 82.0,
                    'max_percentage': 95.0,
                    'subjects_count': 5
                },
                {
                    'name': 'Bob Johnson',
                    'student_id': '003',
                    'admission_number': '003',
                    'grade': 'Grade 8',
                    'stream': 'A',
                    'assessment_type': 'Mid Term',
                    'term': 'Term 1',
                    'average_percentage': 87.2,
                    'total_marks_obtained': 436,
                    'total_marks_possible': 500,
                    'grade_letter': 'B',
                    'min_percentage': 78.0,
                    'max_percentage': 93.0,
                    'subjects_count': 5
                }
            ],
            'subject_analytics': [
                {
                    'subject_name': 'Mathematics',
                    'average_percentage': 85.5,
                    'student_count': 25,
                    'total_marks': 100
                },
                {
                    'subject_name': 'English',
                    'average_percentage': 82.3,
                    'student_count': 25,
                    'total_marks': 100
                },
                {
                    'subject_name': 'Science',
                    'average_percentage': 78.9,
                    'student_count': 25,
                    'total_marks': 100
                }
            ],
            'has_data': True
        }

        # Create mock filter options
        class MockTerm:
            def __init__(self, name):
                self.name = name
                
        class MockAssessmentType:
            def __init__(self, name):
                self.name = name
                
        class MockGrade:
            def __init__(self, name):
                self.name = name

        terms = [MockTerm("Term 1"), MockTerm("Term 2"), MockTerm("Term 3")]
        assessment_types = [MockAssessmentType("Mid Term"), MockAssessmentType("End Term"), MockAssessmentType("CAT")]
        grades = [MockGrade("Grade 7"), MockGrade("Grade 8"), MockGrade("Grade 9")]

        print(f"🔍 Rendering test template with mock data")

        return render_template('classteacher_analytics_fixed.html',
                             teacher=teacher,
                             analytics_data=analytics_data,
                             terms=terms,
                             assessment_types=assessment_types,
                             grades=grades,
                             current_term_filter=None,
                             current_assessment_filter=None,
                             current_grade_filter=None,
                             page_title="Academic Performance Analytics - TEST")

    except Exception as e:
        print(f"🚨 Error in test analytics route: {e}")
        import traceback
        traceback.print_exc()
        return f"<h1>Error in Test Analytics</h1><pre>{str(e)}</pre><pre>{traceback.format_exc()}</pre>"


# ============================================================================
# GRADE MARKSHEET GENERATION (Combined streams for assigned grades)
# ============================================================================

@classteacher_bp.route('/grade_marksheets')
@classteacher_required()
def grade_marksheets():
    """Show available grade marksheets for the classteacher's assigned grades."""
    try:
        teacher_id = session.get('teacher_id')
        if not teacher_id:
            flash('Session expired. Please log in again.', 'error')
            return redirect(url_for('auth.classteacher_login'))
        
        # Get grades accessible by this teacher
        accessible_grades = GradeMarksheetService.get_teacher_accessible_grades(teacher_id)
        
        # Get available terms and assessment types for filters
        terms = Term.query.all()
        assessment_types = AssessmentType.query.all()
        
        return render_template(
            'classteacher_grade_marksheets.html',
            accessible_grades=accessible_grades,
            terms=terms,
            assessment_types=assessment_types,
            teacher_id=teacher_id
        )
        
    except Exception as e:
        logger.error(f"Error loading grade marksheets: {e}")
        flash(f'Error loading grade marksheets: {str(e)}', 'error')
        return redirect(url_for('classteacher.dashboard'))


@classteacher_bp.route('/api/check_grade_marksheet_eligibility')
@classteacher_required()
def check_grade_marksheet_eligibility():
    """Check if a teacher can generate a grade marksheet for given parameters."""
    try:
        teacher_id = session.get('teacher_id')
        grade_id = request.args.get('grade_id', type=int)
        term = request.args.get('term')
        assessment_type = request.args.get('assessment_type')
        
        if not all([teacher_id, grade_id, term, assessment_type]):
            return jsonify({
                'success': False,
                'message': 'Missing required parameters'
            })
        
        can_generate, details = GradeMarksheetService.can_generate_grade_marksheet(
            teacher_id, grade_id, term, assessment_type
        )
        
        if can_generate:
            return jsonify({
                'success': True,
                'can_generate': True,
                'message': 'Grade marksheet can be generated',
                'details': details
            })
        else:
            return jsonify({
                'success': True,
                'can_generate': False,
                'message': details.get('error', 'Cannot generate marksheet'),
                'reason': details.get('reason'),
                'details': details
            })
            
    except Exception as e:
        logger.error(f"Error checking grade marksheet eligibility: {e}")
        return jsonify({
            'success': False,
            'message': f'System error: {str(e)}'
        })


@classteacher_bp.route('/api/grade_marksheet_preview')
@classteacher_required()
def grade_marksheet_preview():
    """Get preview data for a grade marksheet."""
    try:
        teacher_id = session.get('teacher_id')
        grade_id = request.args.get('grade_id', type=int)
        term = request.args.get('term')
        assessment_type = request.args.get('assessment_type')
        
        if not all([teacher_id, grade_id, term, assessment_type]):
            return jsonify({
                'success': False,
                'message': 'Missing required parameters'
            })
        
        # Check permission first
        can_generate, details = GradeMarksheetService.can_generate_grade_marksheet(
            teacher_id, grade_id, term, assessment_type
        )
        
        if not can_generate:
            return jsonify({
                'success': False,
                'message': details.get('error', 'Cannot generate marksheet'),
                'reason': details.get('reason')
            })
        
        # Get marksheet data
        marksheet_data = GradeMarksheetService.get_grade_marksheet_data(
            grade_id, term, assessment_type
        )
        
        if not marksheet_data:
            return jsonify({
                'success': False,
                'message': 'Failed to retrieve grade data'
            })
        
        return jsonify({
            'success': True,
            'data': marksheet_data
        })
        
    except Exception as e:
        logger.error(f"Error getting grade marksheet preview: {e}")
        return jsonify({
            'success': False,
            'message': f'System error: {str(e)}'
        })


@classteacher_bp.route('/generate_combined_grade_marksheet')
@classteacher_required()
def generate_combined_grade_marksheet():
    """Generate and download a grade marksheet (all streams combined)."""
    try:
        teacher_id = session.get('teacher_id')
        grade_id = request.args.get('grade_id', type=int)
        term = request.args.get('term')
        assessment_type = request.args.get('assessment_type')
        format_type = request.args.get('format', 'pdf').lower()
        
        if not all([teacher_id, grade_id, term, assessment_type]):
            flash('Missing required parameters for grade marksheet generation', 'error')
            return redirect(url_for('classteacher.grade_marksheets'))
        
        # Generate the marksheet
        success, result = GradeMarksheetService.generate_grade_marksheet(
            teacher_id, grade_id, term, assessment_type, format_type
        )
        
        if success and isinstance(result, dict) and 'file_path' in result:
            try:
                # For now, just redirect back with success message
                # In real implementation, this would send the actual file
                flash(f'Grade marksheet generated successfully for {result["marksheet_data"]["grade_name"]}', 'success')
                return redirect(url_for('classteacher.grade_marksheets'))
            except Exception as e:
                logger.error(f"Error sending grade marksheet file: {e}")
                flash('Generated file could not be sent', 'error')
        else:
            error_msg = result if isinstance(result, str) else 'Failed to generate grade marksheet'
            flash(error_msg, 'error')
        
        return redirect(url_for('classteacher.grade_marksheets'))
        
    except Exception as e:
        logger.error(f"Error generating grade marksheet: {e}")
        flash(f'Error generating grade marksheet: {str(e)}', 'error')
        return redirect(url_for('classteacher.grade_marksheets'))


@classteacher_bp.route('/api/grade_report_status')
@classteacher_required()
def grade_report_status():
    """Get the status of individual class reports for a grade."""
    try:
        grade_id = request.args.get('grade_id', type=int)
        term = request.args.get('term')
        assessment_type = request.args.get('assessment_type')
        
        if not all([grade_id, term, assessment_type]):
            return jsonify({
                'success': False,
                'message': 'Missing required parameters'
            })
        
        reports_exist, report_status = GradeMarksheetService.check_class_reports_exist(
            grade_id, term, assessment_type
        )
        
        return jsonify({
            'success': True,
            'reports_exist': reports_exist,
            'status': report_status
        })
        
    except Exception as e:
        logger.error(f"Error checking grade report status: {e}")
        return jsonify({
            'success': False,
            'message': f'System error: {str(e)}'
        })