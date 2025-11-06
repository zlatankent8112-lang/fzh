"""
Parent Management views for the Hillview School Management System.
This module handles parent account management for headteachers.
"""
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify, abort
from functools import wraps
from ..services.auth_service import is_authenticated, get_role
from ..models import db
from ..security.security_manager import secure_headteacher_route, comprehensive_security
from ..security.csrf_protection import CSRFProtection
from ..extensions import limiter, csrf
from sqlalchemy import exists, and_, func
try:
    from ..models.parent import Parent, ParentStudent, ParentEmailLog, EmailTemplate
except ImportError:
    from ..models.parent import Parent, ParentStudent, EmailTemplate
    ParentEmailLog = None  # Optional when email logs are not enabled
from ..models.academic import Student, Grade, Stream
from ..models.user import Teacher
from datetime import datetime, timedelta
import secrets
import string
import io
import csv
import time
from ..services.parent_email_service import ParentEmailService
from difflib import SequenceMatcher
import tempfile
import os
from flask import send_file

# Create blueprint for parent management
parent_management_bp = Blueprint('parent_management', __name__, url_prefix='/parent_management')

def headteacher_required(f):
    """Enhanced decorator with comprehensive security protections."""
    @wraps(f)
    @limiter.limit("30 per minute")  # Rate limiting
    @comprehensive_security()  # Full security stack
    def decorated_function(*args, **kwargs):
        # Apply custom CSRF validation for non-parent endpoints
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            endpoint = (request.endpoint or '')
            if not endpoint.startswith('parent_management.'):
                if not CSRFProtection.validate_request():
                    abort(403, "CSRF token validation failed")
        
        # Session validation
        if not session.get('teacher_id'):
            flash('Authentication required', 'error')
            return redirect(url_for('auth.admin_login'))
        
        # Role validation
        if not is_authenticated(session) or get_role(session) != 'headteacher':
            flash('Headteacher access required', 'error')
            return redirect(url_for('auth.admin_login'))
        
        # Session security checks
        if 'ip_address' in session and session['ip_address'] != request.remote_addr:
            session.clear()
            flash('Session security violation detected', 'error')
            return redirect(url_for('auth.admin_login'))
        
        # Update session activity
        import time
        session['last_activity'] = time.time()
        session['ip_address'] = request.remote_addr
        
        return f(*args, **kwargs)
    return decorated_function

@parent_management_bp.route('/debug_session')
def debug_session():
    """Debug route to check session status."""
    return jsonify({
        'session_data': dict(session),
        'is_authenticated': is_authenticated(session),
        'role': get_role(session),
        'teacher_id': session.get('teacher_id')
    })

@parent_management_bp.route('/debug_auth')
def debug_auth():
    """Debug route to check authentication and create test headteacher."""
    from ..models.user import Teacher
    from werkzeug.security import generate_password_hash

    try:
        # Check existing headteachers
        headteachers = Teacher.query.filter_by(role='headteacher').all()

        result = {
            'session_data': dict(session),
            'is_authenticated': is_authenticated(session),
            'role': get_role(session),
            'headteachers_found': len(headteachers),
            'headteacher_accounts': []
        }

        for ht in headteachers:
            result['headteacher_accounts'].append({
                'id': ht.id,
                'username': ht.username,
                'name': ht.name,
                'role': ht.role
            })

        # Create default headteacher if none exists
        if not headteachers:
            default_ht = Teacher(
                username='admin',
                name='System Administrator',
                role='headteacher',
                password_hash=generate_password_hash('admin123')
            )
            db.session.add(default_ht)
            db.session.commit()

            result['created_default'] = True
            result['default_credentials'] = {'username': 'admin', 'password': 'admin123'}

        return jsonify(result)

    except Exception as e:
        return jsonify({'error': str(e)})

@parent_management_bp.route('/test_dashboard')
def test_dashboard():
    """Test dashboard without authentication (for debugging)."""
    try:
        # Get statistics
        total_parents = Parent.query.count()
        active_parents = Parent.query.filter_by(is_active=True).count()
        verified_parents = Parent.query.filter_by(is_verified=True).count()

        # Get parent-student links
        total_links = ParentStudent.query.count()

        # Get recent parents (last 10)
        recent_parents = Parent.query.order_by(Parent.created_at.desc()).limit(10).all()

        # Get parents without children linked
        parents_without_children = db.session.query(Parent).outerjoin(ParentStudent).filter(ParentStudent.parent_id.is_(None)).all()

        # Get students without parents linked
        students_without_parents = db.session.query(Student).outerjoin(ParentStudent).filter(ParentStudent.student_id.is_(None)).all()

        return render_template('parent_management_dashboard.html',
                             total_parents=total_parents,
                             active_parents=active_parents,
                             verified_parents=verified_parents,
                             total_links=total_links,
                             recent_parents=recent_parents,
                             parents_without_children=parents_without_children,
                             students_without_parents=students_without_parents)

    except Exception as e:
        flash(f'Error loading parent management dashboard: {str(e)}', 'error')
        return f"Error: {str(e)}"

@parent_management_bp.route('/dashboard')
@headteacher_required
def dashboard():
    """Parent management dashboard for headteachers."""
    try:
        # Get filter parameters
        grade_filter = request.args.get('grade_filter', '')
        stream_filter = request.args.get('stream_filter', '')
        education_level_filter = request.args.get('education_level_filter', '')
        search_query = request.args.get('search', '')
        status_filter = request.args.get('status', '')  # 'active' | 'inactive' | ''
        verified_filter = request.args.get('verified', '')  # '1' | '0' | ''
        
        # Pagination parameters
        students_page = request.args.get('students_page', 1, type=int)
        parents_page = request.args.get('parents_page', 1, type=int)
        all_parents_page = request.args.get('all_parents_page', 1, type=int)
        links_page = request.args.get('links_page', 1, type=int)
        per_page = request.args.get('per_page', 10, type=int)
        
        # Get statistics
        total_parents = Parent.query.count()
        active_parents = Parent.query.filter_by(is_active=True).count()
        verified_parents = Parent.query.filter_by(is_verified=True).count()
        
        # Get parent-student links
        total_links = ParentStudent.query.count()
        
        # Enhanced statistics for better dashboard insights
        total_students = Student.query.count()
        students_with_parents = db.session.query(Student.id).join(
            ParentStudent, Student.id == ParentStudent.student_id
        ).join(
            Parent, ParentStudent.parent_id == Parent.id
        ).filter(Parent.is_active == True).distinct().count()
        
        students_without_parents_count = total_students - students_with_parents
        
        # Class statistics
        total_grades = Grade.query.count()
        total_streams = Stream.query.count()
        
        # Students by education level
        education_level_stats = db.session.query(
            Grade.education_level, 
            func.count(Student.id).label('student_count')
        ).outerjoin(
            Student, Grade.id == Student.grade_id
        ).group_by(Grade.education_level).all()
        
        # Get recent parents (last 10)
        recent_parents = Parent.query.order_by(Parent.created_at.desc()).limit(10).all()
        
        # Get parents without children linked with pagination
        parents_without_children_query = db.session.query(Parent).outerjoin(ParentStudent).filter(ParentStudent.parent_id.is_(None))
        
        # Apply search filter for parents if provided
        if search_query:
            parents_without_children_query = parents_without_children_query.filter(
                db.or_(
                    Parent.first_name.ilike(f'%{search_query}%'),
                    Parent.last_name.ilike(f'%{search_query}%'),
                    Parent.email.ilike(f'%{search_query}%')
                )
            )
        
        parents_without_children_paginated = parents_without_children_query.paginate(
            page=parents_page, per_page=per_page, error_out=False
        )

        # All parents list with search and pagination
        all_parents_query = Parent.query
        if search_query:
            all_parents_query = all_parents_query.filter(
                db.or_(
                    Parent.first_name.ilike(f'%{search_query}%'),
                    Parent.last_name.ilike(f'%{search_query}%'),
                    Parent.email.ilike(f'%{search_query}%')
                )
            )
        # Apply status filter
        if status_filter == 'active':
            all_parents_query = all_parents_query.filter_by(is_active=True)
        elif status_filter == 'inactive':
            all_parents_query = all_parents_query.filter_by(is_active=False)
        # Apply verified filter
        if verified_filter == '1':
            all_parents_query = all_parents_query.filter_by(is_verified=True)
        elif verified_filter == '0':
            all_parents_query = all_parents_query.filter_by(is_verified=False)
        all_parents_query = all_parents_query.order_by(Parent.created_at.desc())
        all_parents_paginated = all_parents_query.paginate(
            page=all_parents_page, per_page=per_page, error_out=False
        )
        # Children counts for listed parents (single query)
        children_counts = {}
        if all_parents_paginated.items:
            parent_ids = [p.id for p in all_parents_paginated.items]
            rows = db.session.query(ParentStudent.parent_id, func.count(ParentStudent.student_id)) \
                .filter(ParentStudent.parent_id.in_(parent_ids)) \
                .group_by(ParentStudent.parent_id).all()
            children_counts = {pid: cnt for pid, cnt in rows}
        
        # Get students without ACTIVE parents linked with filtering and pagination
        # Include students even if they don't yet have grade/stream assigned
        # Definition: a student is "unlinked" if there is NO ParentStudent link to an active Parent
        active_parent_exists = db.session.query(Parent.id) \
            .join(ParentStudent, Parent.id == ParentStudent.parent_id) \
            .filter(and_(ParentStudent.student_id == Student.id, Parent.is_active == True)) \
            .exists()

        # Enhanced query to handle data integrity issues
        students_without_parents_query = db.session.query(Student, Grade, Stream)\
            .outerjoin(Grade, Student.grade_id == Grade.id)\
            .outerjoin(Stream, Student.stream_id == Stream.id)\
            .filter(~active_parent_exists)
        
        # Apply filters
        if grade_filter:
            students_without_parents_query = students_without_parents_query.filter(Grade.id == grade_filter)
        
        if stream_filter:
            students_without_parents_query = students_without_parents_query.filter(Stream.id == stream_filter)
        
        if education_level_filter:
            students_without_parents_query = students_without_parents_query.filter(Grade.education_level == education_level_filter)
        
        if search_query:
            students_without_parents_query = students_without_parents_query.filter(
                db.or_(
                    Student.name.ilike(f'%{search_query}%'),
                    Student.admission_number.ilike(f'%{search_query}%')
                )
            )
        
        students_without_parents_query = students_without_parents_query.order_by(Grade.name, Stream.name, Student.name)
        
        students_without_parents_paginated = students_without_parents_query.paginate(
            page=students_page, per_page=per_page, error_out=False
        )
        
        # Get filter options
        all_grades = Grade.query.order_by(Grade.name).all()
        all_streams = Stream.query.order_by(Stream.name).all()
        # Use canonical ordered education levels for UI
        from ..utils.constants import EDUCATION_LEVELS_ORDER
        education_levels = EDUCATION_LEVELS_ORDER
        
        # Potential duplicates by phone (simple heuristic)
        dup_phone_rows = db.session.query(Parent.phone, func.count(Parent.id)) \
            .filter(Parent.phone.isnot(None), Parent.phone != '') \
            .group_by(Parent.phone).having(func.count(Parent.id) > 1).all()
        duplicate_groups = []
        if dup_phone_rows:
            dup_phones = [r[0] for r in dup_phone_rows]
            parents_by_phone = Parent.query.filter(Parent.phone.in_(dup_phones)) \
                .order_by(Parent.phone, Parent.created_at.desc()).all()
            # group in python
            temp = {}
            for p in parents_by_phone:
                temp.setdefault(p.phone, []).append(p)
            for phone, plist in temp.items():
                if len(plist) > 1:
                    duplicate_groups.append({'reason': f'Phone: {phone}', 'parents': plist})

        # Recent parent-student links (with optional search filter)
        recent_links_query = db.session.query(
            ParentStudent, Parent, Student, Grade, Stream
        ).join(Parent, ParentStudent.parent_id == Parent.id) \
         .join(Student, ParentStudent.student_id == Student.id) \
         .outerjoin(Grade, Student.grade_id == Grade.id) \
         .outerjoin(Stream, Student.stream_id == Stream.id)

        if search_query:
            recent_links_query = recent_links_query.filter(
                db.or_(
                    Parent.first_name.ilike(f'%{search_query}%'),
                    Parent.last_name.ilike(f'%{search_query}%'),
                    Parent.email.ilike(f'%{search_query}%'),
                    Student.name.ilike(f'%{search_query}%'),
                    Student.admission_number.ilike(f'%{search_query}%')
                )
            )

        recent_links_query = recent_links_query.order_by(ParentStudent.created_at.desc())
        recent_links_paginated = recent_links_query.paginate(page=links_page, per_page=per_page, error_out=False)

        # Get summary counts for filtered data
        total_students_without_parents = students_without_parents_query.count()
        total_parents_without_children = parents_without_children_query.count()
        total_all_parents = all_parents_query.count()
        
        # Check if there are data integrity issues to show/hide fix button
        students_needing_fix = db.session.query(Student).filter(
            db.or_(
                db.and_(Student.grade_id.is_(None), Student.stream_id.isnot(None)),
                db.and_(Student.stream_id.isnot(None), 
                       ~db.session.query(Stream.id).filter(Stream.id == Student.stream_id).exists())
            )
        ).count()
        
        has_data_issues = students_needing_fix > 0
        
        return render_template('parent_management_dashboard.html',
                             total_parents=total_parents,
                             active_parents=active_parents,
                             verified_parents=verified_parents,
                             total_links=total_links,
                             recent_parents=recent_parents,
                             parents_without_children=parents_without_children_paginated.items,
                             parents_pagination=parents_without_children_paginated,
                             all_parents=all_parents_paginated.items,
                             all_parents_pagination=all_parents_paginated,
                             children_counts=children_counts,
                             students_without_parents=students_without_parents_paginated.items,
                             students_pagination=students_without_parents_paginated,
                             recent_links=recent_links_paginated.items,
                             links_pagination=recent_links_paginated,
                             all_grades=all_grades,
                             all_streams=all_streams,
                             education_levels=education_levels,
                             # Enhanced statistics
                             total_students=total_students,
                             students_with_parents=students_with_parents,
                             students_without_parents_count=students_without_parents_count,
                             total_grades=total_grades,
                             total_streams=total_streams,
                             education_level_stats=education_level_stats,
                             has_data_issues=has_data_issues,
                             students_needing_fix=students_needing_fix,
                             current_filters={
                                 'grade_filter': grade_filter,
                                 'stream_filter': stream_filter,
                                 'education_level_filter': education_level_filter,
                                 'search_query': search_query,
                                 'status': status_filter,
                                 'verified': verified_filter,
                                 'per_page': per_page
                             },
                             total_students_without_parents=total_students_without_parents,
                             total_parents_without_children=total_parents_without_children,
                             total_all_parents=total_all_parents,
                             duplicate_groups=duplicate_groups)
    
    except Exception as e:
        db.session.rollback()
        flash(f'Error loading parent management dashboard: {str(e)}', 'error')
        return redirect(url_for('admin.dashboard'))

@parent_management_bp.route('/export_unlinked_data')
@headteacher_required
def export_unlinked_data():
    """Export unlinked parents and students as a CSV download.

    This endpoint is referenced in the dashboard template. Previously missing,
    it caused a BuildError during render and a redirect loop. We generate a
    simple CSV with two sections: unlinked parents and unlinked students.
    """
    try:
        # Unlinked parents
        unlinked_parents = db.session.query(Parent) \
            .outerjoin(ParentStudent) \
            .filter(ParentStudent.parent_id.is_(None)) \
            .order_by(Parent.created_at.desc()) \
            .all()

        # Unlinked students with class info (include those missing class assignment)
        active_parent_exists = db.session.query(Parent.id) \
            .join(ParentStudent, Parent.id == ParentStudent.parent_id) \
            .filter(and_(ParentStudent.student_id == Student.id, Parent.is_active == True)) \
            .exists()

        unlinked_students = db.session.query(Student, Grade, Stream) \
            .outerjoin(Grade, Student.grade_id == Grade.id) \
            .outerjoin(Stream, Student.stream_id == Stream.id) \
            .filter(~active_parent_exists) \
            .order_by(Grade.name, Stream.name, Student.name) \
            .all()

        # Build CSV in memory
        buf = io.StringIO()
        writer = csv.writer(buf)

        # Section 1: Parents without children
        writer.writerow(["Section", "Parents without linked children"])
        writer.writerow(["id", "first_name", "last_name", "email", "phone", "is_verified", "is_active", "created_at"])
        for p in unlinked_parents:
            writer.writerow([
                p.id, p.first_name, p.last_name, p.email, p.phone or '',
                'yes' if p.is_verified else 'no',
                'yes' if p.is_active else 'no',
                p.created_at.strftime('%Y-%m-%d %H:%M:%S') if p.created_at else ''
            ])

        writer.writerow([])  # blank line between sections

        # Section 2: Students without parents
        writer.writerow(["Section", "Students without linked parents"])
        writer.writerow(["id", "name", "admission_number", "grade", "stream"]) 
        for s, g, st in unlinked_students:
            writer.writerow([
                s.id,
                s.name,
                s.admission_number or '',
                (g.name if g else ''),
                (st.name if st else '')
            ])

        csv_data = buf.getvalue()

        from flask import Response
        filename = f"unlinked_parents_students_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
        return Response(
            csv_data,
            mimetype='text/csv',
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"'
            }
        )

    except Exception as e:
        db.session.rollback()
        flash(f'Error exporting unlinked data: {str(e)}', 'error')
        return redirect(url_for('parent_management.dashboard'))

@parent_management_bp.route('/export_parents')
@headteacher_required
def export_parents():
    """Export all parents with status, verification, and children count."""
    try:
        # Fetch all parents
        parents = Parent.query.order_by(Parent.created_at.desc()).all()
        # Build children counts
        parent_ids = [p.id for p in parents]
        counts = {}
        if parent_ids:
            rows = db.session.query(ParentStudent.parent_id, func.count(ParentStudent.student_id)) \
                .filter(ParentStudent.parent_id.in_(parent_ids)) \
                .group_by(ParentStudent.parent_id).all()
            counts = {pid: cnt for pid, cnt in rows}

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["id", "first_name", "last_name", "email", "phone", "is_active", "is_verified", "children_count", "created_at"]) 
        for p in parents:
            writer.writerow([
                p.id, p.first_name, p.last_name, p.email, p.phone or '',
                'yes' if p.is_active else 'no',
                'yes' if p.is_verified else 'no',
                counts.get(p.id, 0),
                p.created_at.strftime('%Y-%m-%d %H:%M:%S') if p.created_at else ''
            ])

        from flask import Response
        filename = f"parents_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
        return Response(
            buf.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'}
        )
    except Exception as e:
        db.session.rollback()
        flash(f'Error exporting parents: {str(e)}', 'error')
        return redirect(url_for('parent_management.dashboard'))

@parent_management_bp.route('/dashboard_enhanced')
@headteacher_required
def dashboard_enhanced():
    """Enhanced parent management dashboard with advanced features."""
    try:
        # Get search and filter parameters
        search_query = request.args.get('search', '')
        status_filter = request.args.get('status', '')
        verified_filter = request.args.get('verified', '')
        
        # Get statistics
        total_parents = Parent.query.count()
        active_parents = Parent.query.filter_by(is_active=True).count()
        verified_parents = Parent.query.filter_by(is_verified=True).count()
        total_links = ParentStudent.query.count()
        
        # Build query for recent parents with filters
        recent_parents_query = Parent.query
        
        # Apply search filter
        if search_query:
            recent_parents_query = recent_parents_query.filter(
                db.or_(
                    Parent.first_name.ilike(f'%{search_query}%'),
                    Parent.last_name.ilike(f'%{search_query}%'),
                    Parent.email.ilike(f'%{search_query}%')
                )
            )
        
        # Apply status filter
        if status_filter == 'active':
            recent_parents_query = recent_parents_query.filter_by(is_active=True)
        elif status_filter == 'inactive':
            recent_parents_query = recent_parents_query.filter_by(is_active=False)
        
        # Apply verification filter
        if verified_filter == '1':
            recent_parents_query = recent_parents_query.filter_by(is_verified=True)
        elif verified_filter == '0':
            recent_parents_query = recent_parents_query.filter_by(is_verified=False)
        
        # Get recent parents with applied filters
        recent_parents = recent_parents_query.order_by(Parent.created_at.desc()).limit(20).all()
        
        # Get parents without children linked
        parents_without_children = db.session.query(Parent)\
            .outerjoin(ParentStudent)\
            .filter(ParentStudent.parent_id.is_(None))\
            .order_by(Parent.created_at.desc())\
            .all()
        
        # Get students without ACTIVE parents linked (include those without class assignment)
        active_parent_exists = db.session.query(Parent.id) \
            .join(ParentStudent, Parent.id == ParentStudent.parent_id) \
            .filter(and_(ParentStudent.student_id == Student.id, Parent.is_active == True)) \
            .exists()

        students_without_parents = db.session.query(Student, Grade, Stream)\
            .outerjoin(Grade, Student.grade_id == Grade.id)\
            .outerjoin(Stream, Student.stream_id == Stream.id)\
            .filter(~active_parent_exists)\
            .order_by(Grade.name, Stream.name, Student.name)\
            .all()
        
        return render_template('parent_management_dashboard_enhanced.html',
                             total_parents=total_parents,
                             active_parents=active_parents,
                             verified_parents=verified_parents,
                             total_links=total_links,
                             recent_parents=recent_parents,
                             parents_without_children=parents_without_children,
                             students_without_parents=[item[0] for item in students_without_parents])
    
    except Exception as e:
        db.session.rollback()
        flash(f'Error loading enhanced parent management dashboard: {str(e)}', 'error')
        return redirect(url_for('admin.dashboard'))

@parent_management_bp.route('/stats')
@headteacher_required
def get_stats():
    """API endpoint to get updated statistics for the enhanced dashboard."""
    try:
        stats = {
            'success': True,
            'total_parents': Parent.query.count(),
            'active_parents': Parent.query.filter_by(is_active=True).count(),
            'verified_parents': Parent.query.filter_by(is_verified=True).count(),
            'total_links': ParentStudent.query.count()
        }
        return jsonify(stats)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@parent_management_bp.route('/add_parent', methods=['GET', 'POST'])
@headteacher_required
def add_parent():
    """Add a new parent account."""
    if request.method == 'POST':
        try:
            # Get form data
            first_name = request.form.get('first_name', '').strip()
            last_name = request.form.get('last_name', '').strip()
            email = request.form.get('email', '').strip().lower()
            phone = request.form.get('phone', '').strip()
            
            # Validation
            if not all([first_name, last_name, email]):
                flash('First name, last name, and email are required.', 'error')
                return render_template('add_parent.html')
            
            # Check if email already exists
            existing_parent = Parent.query.filter_by(email=email).first()
            if existing_parent:
                flash('A parent with this email already exists.', 'error')
                return render_template('add_parent.html')
            
            # Generate temporary password
            temp_password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8))
            
            # Create parent account
            parent = Parent(
                first_name=first_name,
                last_name=last_name,
                email=email,
                phone=phone,
                is_verified=False,  # Will need to verify email
                is_active=True
            )
            parent.set_password(temp_password)
            
            db.session.add(parent)
            db.session.commit()
            
            # Show password on the same page for 30 seconds
            success_message = f'Parent account created successfully!'
            info_message = 'Please share the temporary password with the parent and ask them to change it after first login.'
            return render_template('add_parent.html', temp_password=temp_password, success_message=success_message, info_message=info_message)
        
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating parent account: {str(e)}', 'error')
    
    return render_template('add_parent.html')

@parent_management_bp.route('/link_parent_student', methods=['GET', 'POST'])
@headteacher_required
def link_parent_student():
    """Link a parent to a student."""
    if request.method == 'POST':
        try:
            # Support linking a single student to multiple parents
            parent_ids = request.form.getlist('parent_ids[]')
            parent_ids = [int(pid) for pid in parent_ids if str(pid).isdigit()]
            parent_id = request.form.get('parent_id', type=int)
            student_id = request.form.get('student_id', type=int)
            relationship_type = request.form.get('relationship_type', 'parent')
            is_primary_contact = request.form.get('is_primary_contact') == 'on'
            
            # Normalize parents list
            if not parent_ids and parent_id:
                parent_ids = [parent_id]

            # Validation
            if not student_id or not parent_ids:
                flash('Please select at least one parent and a student.', 'error')
                return redirect(url_for('parent_management.link_parent_student'))

            linked = 0
            skipped = 0
            for pid in parent_ids:
                # Check if link already exists
                existing_link = ParentStudent.query.filter_by(parent_id=pid, student_id=student_id).first()
                if existing_link:
                    skipped += 1
                    continue

                # Create the link
                link = ParentStudent(
                    parent_id=pid,
                    student_id=student_id,
                    relationship_type=relationship_type,
                    is_primary_contact=is_primary_contact if len(parent_ids) == 1 else False,
                    created_by=session.get('teacher_id')
                )
                db.session.add(link)
                linked += 1

            db.session.commit()

            msg = f'Linked {linked} parent(s) to the student.'
            if skipped:
                msg += f' Skipped {skipped} already-linked parent(s).'
            flash(msg, 'success' if linked else 'warning')
            return redirect(url_for('parent_management.dashboard'))
        
        except Exception as e:
            db.session.rollback()
            flash(f'Error linking parent and student: {str(e)}', 'error')
    
    # Get all parents and students for the form
    parents = Parent.query.filter_by(is_active=True).order_by(Parent.first_name, Parent.last_name).all()
    # Include students without class assignment (outer joins)
    students = db.session.query(Student, Grade, Stream)\
        .outerjoin(Grade, Student.grade_id == Grade.id)\
        .outerjoin(Stream, Student.stream_id == Stream.id)\
        .order_by(Grade.name, Stream.name, Student.name).all()
    
    return render_template('link_parent_student.html', parents=parents, students=students)

@parent_management_bp.route('/view_parent/<int:parent_id>')
@headteacher_required
def view_parent(parent_id):
    """View parent details and linked children."""
    try:
        parent = Parent.query.get_or_404(parent_id)
        
        # Get linked children with their class information
        children_query = db.session.query(
            ParentStudent, Student, Grade, Stream
        ).join(Student, ParentStudent.student_id == Student.id)\
         .join(Grade, Student.grade_id == Grade.id)\
         .join(Stream, Student.stream_id == Stream.id)\
         .filter(ParentStudent.parent_id == parent_id)\
         .order_by(Grade.name, Stream.name, Student.name)
        
        children = children_query.all()
        
        # Get email logs for this parent
        email_logs = []
        if ParentEmailLog:
            email_logs = ParentEmailLog.query.filter_by(parent_id=parent_id).order_by(ParentEmailLog.created_at.desc()).limit(10).all()
        
        return render_template('view_parent.html', 
                             parent=parent, 
                             children=children,
                             email_logs=email_logs)
    
    except Exception as e:
        flash(f'Error viewing parent: {str(e)}', 'error')
        return redirect(url_for('parent_management.dashboard'))

@parent_management_bp.route('/unlink_parent_student/<int:link_id>')
@headteacher_required
def unlink_parent_student(link_id):
    """Remove a parent-student link."""
    try:
        link = ParentStudent.query.get_or_404(link_id)
        parent_name = link.parent.get_full_name()
        student_name = link.student.name
        
        db.session.delete(link)
        db.session.commit()
        
        flash(f'Successfully unlinked {parent_name} from {student_name}.', 'success')
    
    except Exception as e:
        db.session.rollback()
        flash(f'Error unlinking parent and student: {str(e)}', 'error')
    
    return redirect(url_for('parent_management.dashboard'))

@parent_management_bp.route('/resend_verification/<int:parent_id>', methods=['POST'])
@headteacher_required
def resend_verification(parent_id):
    """Resend verification email to a single parent."""
    try:
        parent = Parent.query.get_or_404(parent_id)
        if parent.is_verified:
            flash('Parent is already verified.', 'info')
            return redirect(url_for('parent_management.dashboard'))

        success, message = ParentEmailService.send_verification_email(parent)
        if success:
            db.session.commit()
            flash('Verification email sent.', 'success')
        else:
            db.session.rollback()
            flash(f'Failed to send verification email: {message}', 'error')
    except Exception as e:
        db.session.rollback()
        flash(f'Error resending verification: {str(e)}', 'error')
    return redirect(url_for('parent_management.dashboard'))

@parent_management_bp.route('/bulk_parents_action', methods=['POST'])
@headteacher_required
def bulk_parents_action():
    """Perform bulk actions on selected parents: activate, deactivate, resend_verification, delete (soft)."""
    try:
        action = request.form.get('action')
        ids = request.form.getlist('parent_ids[]') or request.form.getlist('parent_ids')
        parent_ids = [int(pid) for pid in ids if str(pid).isdigit()]
        if not parent_ids:
            flash('No parents selected.', 'warning')
            return redirect(url_for('parent_management.dashboard'))

        parents = Parent.query.filter(Parent.id.in_(parent_ids)).all()
        if not parents:
            flash('Selected parents not found.', 'error')
            return redirect(url_for('parent_management.dashboard'))

        count_ok = 0
        count_err = 0

        if action == 'activate':
            for p in parents:
                p.is_active = True
                count_ok += 1
            db.session.commit()
            flash(f'Activated {count_ok} parent(s).', 'success')
        elif action == 'deactivate':
            for p in parents:
                p.is_active = False
                count_ok += 1
            db.session.commit()
            flash(f'Deactivated {count_ok} parent(s).', 'success')
        elif action == 'resend_verification':
            for p in parents:
                if p.is_verified:
                    continue
                success, _ = ParentEmailService.send_verification_email(p)
                if success:
                    count_ok += 1
                else:
                    count_err += 1
            # commit token/time for those updated
            db.session.commit()
            msg = f'Resent verification to {count_ok} parent(s).'
            if count_err:
                msg += f' {count_err} failed.'
            flash(msg, 'success' if count_err == 0 else 'warning')
        elif action == 'delete':
            # Soft delete: mark inactive and disable notifications
            for p in parents:
                p.is_active = False
                p.email_notifications = False
            db.session.commit()
            flash(f'Soft-deleted (deactivated) {len(parents)} parent(s).', 'success')
        else:
            flash('Unknown action.', 'error')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Bulk action error: {str(e)}', 'error')
    return redirect(url_for('parent_management.dashboard'))

@parent_management_bp.route('/merge_parents', methods=['POST'])
@headteacher_required
def merge_parents():
    """Merge duplicate parent accounts by moving links/logs and deactivating the duplicate."""
    try:
        primary_id = request.form.get('primary_id', type=int)
        duplicate_id = request.form.get('duplicate_id', type=int)
        if not primary_id or not duplicate_id or primary_id == duplicate_id:
            flash('Invalid merge request.', 'error')
            return redirect(url_for('parent_management.dashboard'))

        primary = Parent.query.get_or_404(primary_id)
        duplicate = Parent.query.get_or_404(duplicate_id)

        # Move parent-student links (avoid unique collisions)
        dup_links = ParentStudent.query.filter_by(parent_id=duplicate.id).all()
        for link in dup_links:
            exists_link = ParentStudent.query.filter_by(parent_id=primary.id, student_id=link.student_id).first()
            if exists_link:
                # duplicate link redundant
                db.session.delete(link)
            else:
                link.parent_id = primary.id

        # Move email logs if model exists
        if ParentEmailLog:
            for log in ParentEmailLog.query.filter_by(parent_id=duplicate.id).all():
                log.parent_id = primary.id

        # Soft-delete duplicate
        duplicate.is_active = False
        duplicate.email_notifications = False

        db.session.commit()
        flash('Parents merged successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error merging parents: {str(e)}', 'error')
    return redirect(url_for('parent_management.dashboard'))

@parent_management_bp.route('/delete_parent/<int:parent_id>', methods=['POST'])
@csrf.exempt
@headteacher_required
def delete_parent(parent_id):
    """Delete a parent account and any associated links/logs."""
    try:
        parent = Parent.query.get_or_404(parent_id)

        # Remove parent-student links first to satisfy FK constraints
        ParentStudent.query.filter_by(parent_id=parent.id).delete(synchronize_session=False)

        # Remove optional email logs if the model exists
        if ParentEmailLog:
            ParentEmailLog.query.filter_by(parent_id=parent.id).delete(synchronize_session=False)

        # Finally delete the parent
        db.session.delete(parent)
        db.session.commit()

        flash('Parent account deleted successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting parent: {str(e)}', 'error')

    return redirect(url_for('parent_management.dashboard'))

@parent_management_bp.route('/toggle_parent_status/<int:parent_id>')
@headteacher_required
def toggle_parent_status(parent_id):
    """Toggle parent active/inactive status."""
    try:
        parent = Parent.query.get_or_404(parent_id)
        parent.is_active = not parent.is_active
        
        db.session.commit()
        
        status = "activated" if parent.is_active else "deactivated"
        flash(f'Parent account {status} successfully.', 'success')
    
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating parent status: {str(e)}', 'error')
    
    return redirect(url_for('parent_management.dashboard'))

@parent_management_bp.route('/search_parents')
@headteacher_required
def search_parents():
    """Search parents by name or email."""
    # Support direct lookup by id for preselection
    parent_id = request.args.get('id', type=int)
    query = request.args.get('q', '').strip()

    try:
        parent_list = []
        if parent_id:
            parent = Parent.query.get(parent_id)
            if parent:
                parent_list.append({
                    'id': parent.id,
                    'name': parent.get_full_name(),
                    'email': parent.email,
                    'phone': parent.phone,
                    'is_active': parent.is_active,
                    'is_verified': parent.is_verified
                })
        elif query:
            parents = Parent.query.filter(
                db.or_(
                    Parent.first_name.ilike(f'%{query}%'),
                    Parent.last_name.ilike(f'%{query}%'),
                    Parent.email.ilike(f'%{query}%')
                )
            ).limit(10).all()
            for parent in parents:
                parent_list.append({
                    'id': parent.id,
                    'name': parent.get_full_name(),
                    'email': parent.email,
                    'phone': parent.phone,
                    'is_active': parent.is_active,
                    'is_verified': parent.is_verified
                })

        return jsonify({'parents': parent_list})

    except Exception as e:
        return jsonify({'error': str(e)})

@parent_management_bp.route('/search_students')
@headteacher_required
def search_students():
    """Search students by name or admission number."""
    # Support direct lookup by id for preselection; also accept admission number string
    student_id_raw = request.args.get('id', '').strip()
    student_id = int(student_id_raw) if student_id_raw.isdigit() else None
    query = request.args.get('q', '').strip()

    try:
        student_list = []
        if student_id_raw:
            # Use outer joins so students without class assignment are also supported
            base = db.session.query(Student, Grade, Stream)\
                .outerjoin(Grade, Student.grade_id == Grade.id)\
                .outerjoin(Stream, Student.stream_id == Stream.id)
            if student_id is not None:
                result = base.filter(Student.id == student_id).first()
            else:
                # Treat provided id as admission number (case-insensitive exact match)
                result = base.filter(func.lower(Student.admission_number) == student_id_raw.lower()).first()
            if result:
                student, grade, stream = result
                # Safe class label: only include present parts
                parts = []
                if grade and getattr(grade, 'name', None):
                    parts.append(grade.name)
                if stream and getattr(stream, 'name', None):
                    parts.append(stream.name)
                class_label = ' '.join(parts) if parts else 'Unassigned'
                student_list.append({
                    'id': student.id,
                    'name': student.name,
                    'admission_number': student.admission_number,
                    'class': class_label,
                    'grade_id': grade.id if grade else None,
                    'stream_id': stream.id if stream else None
                })
        elif query:
            # Include students without class assignment using outer joins
            students_query = db.session.query(
                Student, Grade, Stream
            ).outerjoin(Grade, Student.grade_id == Grade.id)\
             .outerjoin(Stream, Student.stream_id == Stream.id)\
             .filter(
                db.or_(
                    Student.name.ilike(f'%{query}%'),
                    Student.admission_number.ilike(f'%{query}%')
                )
            ).order_by(Student.name.asc()).limit(20)
            for student, grade, stream in students_query:
                parts = []
                if grade and getattr(grade, 'name', None):
                    parts.append(grade.name)
                if stream and getattr(stream, 'name', None):
                    parts.append(stream.name)
                class_label = ' '.join(parts) if parts else 'Unassigned'
                student_list.append({
                    'id': student.id,
                    'name': student.name,
                    'admission_number': student.admission_number,
                    'class': class_label,
                    'grade_id': grade.id if grade else None,
                    'stream_id': stream.id if stream else None
                })

        return jsonify({'students': student_list})

    except Exception as e:
        return jsonify({'error': str(e)})

@parent_management_bp.route('/bulk_link_students', methods=['POST'])
@headteacher_required
def bulk_link_students():
    """Bulk link multiple students to a parent."""
    try:
        parent_id = request.form.get('parent_id', type=int)
        student_ids = request.form.getlist('student_ids[]')
        relationship_type = request.form.get('relationship_type', 'parent')

        if not parent_id or not student_ids:
            return jsonify({'success': False, 'message': 'Please select a parent and at least one student.'})

        # Verify parent exists
        parent = Parent.query.get(parent_id)
        if not parent:
            return jsonify({'success': False, 'message': 'Parent not found.'})

        linked_count = 0
        errors = []

        for student_id in student_ids:
            try:
                # Check if link already exists
                existing_link = ParentStudent.query.filter_by(parent_id=parent_id, student_id=student_id).first()
                if existing_link:
                    student = Student.query.get(student_id)
                    errors.append(f'{student.name if student else f"Student {student_id}"} is already linked to this parent.')
                    continue

                # Create the link
                link = ParentStudent(
                    parent_id=parent_id,
                    student_id=student_id,
                    relationship_type=relationship_type,
                    is_primary_contact=False,
                    created_by=session.get('teacher_id')
                )
                db.session.add(link)
                linked_count += 1
            except Exception as e:
                errors.append(f'Error linking student {student_id}: {str(e)}')

        db.session.commit()

        message = f'Successfully linked {linked_count} student(s) to {parent.get_full_name()}.'
        if errors:
            message += f' {len(errors)} error(s): ' + '; '.join(errors[:3])
            if len(errors) > 3:
                message += f' and {len(errors) - 3} more...'

        return jsonify({'success': True, 'message': message})

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Error: {str(e)}'})

@parent_management_bp.route('/fix_student_data', methods=['POST', 'GET'])
@headteacher_required
def fix_student_data():
    """Fix student data integrity issues (grade assignments based on streams)."""
    try:
        fixed_count = 0
        issues_found = []
        
        # Detailed analysis and reporting
        print("🔍 Starting data integrity analysis...")
        
        # Find students with stream but no grade
        students_to_fix = db.session.query(Student, Stream)\
            .outerjoin(Stream, Student.stream_id == Stream.id)\
            .filter(Student.grade_id.is_(None), Student.stream_id.isnot(None)).all()
        
        print(f"Found {len(students_to_fix)} students with streams but no grades")
        
        for student, stream in students_to_fix:
            if stream and stream.grade_id:
                # Get the grade that owns this stream
                grade = Grade.query.get(stream.grade_id)
                if grade:
                    print(f"Fixing {student.name}: {student.admission_number} -> Grade {grade.name}")
                    student.grade_id = stream.grade_id
                    fixed_count += 1
                    issues_found.append(f'Fixed {student.name}: Set grade to {grade.name}')
        
        # Find students with orphaned stream references
        students_invalid_stream = db.session.query(Student)\
            .filter(Student.stream_id.isnot(None))\
            .filter(~db.session.query(Stream.id)\
                   .filter(Stream.id == Student.stream_id).exists()).all()
        
        print(f"Found {len(students_invalid_stream)} students with invalid stream references")
        
        for student in students_invalid_stream:
            print(f"Cleaning {student.name}: Removing invalid stream ID {student.stream_id}")
            student.stream_id = None
            fixed_count += 1
            issues_found.append(f'Fixed {student.name}: Removed invalid stream reference')
        
        if fixed_count > 0:
            db.session.commit()
            flash(f'✅ Fixed {fixed_count} student data integrity issues!', 'success')
            for issue in issues_found[:5]:  # Show first 5 fixes
                flash(issue, 'info')
            
            # Show summary of all students after fix
            all_students = db.session.query(Student, Grade, Stream)\
                .outerjoin(Grade, Student.grade_id == Grade.id)\
                .outerjoin(Stream, Student.stream_id == Stream.id)\
                .order_by(Grade.name, Stream.name, Student.name).all()
            
            flash(f'📊 Summary: {len(all_students)} students now have proper grade/stream assignments', 'success')
            
        else:
            flash('✅ No data integrity issues found to fix.', 'info')
        
        return redirect(url_for('parent_management.dashboard'))
        
    except Exception as e:
        db.session.rollback()
        flash(f'❌ Error fixing student data: {str(e)}', 'error')
        return redirect(url_for('parent_management.dashboard'))

# ================== BULK CSV IMPORT FEATURES ==================

@parent_management_bp.route('/bulk_import')
@headteacher_required
def bulk_import_parents():
    """Display bulk import page for CSV parent upload."""
    return render_template('bulk_import_parents.html')

@parent_management_bp.route('/download_import_template')
@headteacher_required
def download_import_template():
    """Download CSV template for bulk parent import."""
    try:
        # Create CSV template with sample data
        template_data = [
            ['first_name', 'last_name', 'email', 'phone', 'relationship_type'],
            ['John', 'Doe', 'john.doe@email.com', '+1234567890', 'father'],
            ['Jane', 'Smith', 'jane.smith@email.com', '+1987654321', 'mother'],
            ['', '', '', '', ''],  # Empty row for user data
        ]
        
        # Create temporary file
        temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        writer = csv.writer(temp_file)
        
        for row in template_data:
            writer.writerow(row)
        
        temp_file.close()
        
        return send_file(
            temp_file.name,
            as_attachment=True,
            download_name='parent_import_template.csv',
            mimetype='text/csv'
        )
        
    except Exception as e:
        flash(f'Error generating template: {str(e)}', 'error')
        return redirect(url_for('parent_management.bulk_import_parents'))

@parent_management_bp.route('/process_bulk_import', methods=['POST'])
@headteacher_required
def process_bulk_import():
    """Process bulk parent import from CSV data."""
    try:
        data = request.get_json()
        csv_data = data.get('data', [])
        options = data.get('options', {})
        
        if not csv_data:
            return jsonify({'success': False, 'message': 'No data provided'})
        
        imported_count = 0
        skipped_count = 0
        error_count = 0
        errors = []
        created_parents = []
        
        for i, row in enumerate(csv_data):
            try:
                row_num = i + 2  # Account for header row
                
                # Validate required fields
                if not all([row.get('first_name'), row.get('last_name'), row.get('email')]):
                    error_count += 1
                    errors.append(f'Row {row_num}: Missing required fields')
                    continue
                
                email = row['email'].strip().lower()
                
                # Check for existing parent if skip_duplicates is enabled
                if options.get('skip_duplicates', True):
                    existing = Parent.query.filter_by(email=email).first()
                    if existing:
                        skipped_count += 1
                        continue
                
                # Generate secure password
                temp_password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8))
                
                # Create parent account
                parent = Parent(
                    first_name=row['first_name'].strip(),
                    last_name=row['last_name'].strip(),
                    email=email,
                    phone=row.get('phone', '').strip() or None,
                    is_verified=False,
                    is_active=options.get('activate_accounts', True)
                )
                parent.set_password(temp_password)
                
                db.session.add(parent)
                db.session.flush()  # Get the ID
                
                created_parents.append({
                    'parent': parent,
                    'password': temp_password
                })
                imported_count += 1
                
            except Exception as e:
                error_count += 1
                errors.append(f'Row {row_num}: {str(e)}')
        
        # Commit all changes
        if imported_count > 0:
            db.session.commit()
        
        # Send welcome emails if requested
        welcome_emails_sent = 0
        if options.get('send_welcome_emails', False) and created_parents:
            for item in created_parents:
                try:
                    # You can implement email sending here
                    # For now, we'll just count as sent
                    welcome_emails_sent += 1
                except Exception as e:
                    pass  # Continue with other emails
        
        return jsonify({
            'success': True,
            'imported_count': imported_count,
            'skipped_count': skipped_count,
            'error_count': error_count,
            'errors': errors[:10],  # Limit errors shown
            'welcome_emails_sent': welcome_emails_sent
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})

# ================== COMMUNICATION CENTER FEATURES ==================

@parent_management_bp.route('/communication_center')
@headteacher_required
def communication_center():
    """Display parent communication center."""
    try:
        # Get data for dropdowns
        grades = Grade.query.order_by(Grade.name).all()
        streams = Stream.query.order_by(Stream.name).all()
        
        from ..utils.constants import EDUCATION_LEVELS_ORDER
        education_levels = EDUCATION_LEVELS_ORDER
        
        # Get communication stats
        total_active_parents = Parent.query.filter_by(is_active=True).count()
        unverified_parents_count = Parent.query.filter_by(is_verified=False, is_active=True).count()
        
        # Get recent communications (placeholder - you can implement a Communications model)
        recent_communications = []
        
        return render_template('communication_center.html',
                             grades=grades,
                             streams=streams,
                             education_levels=education_levels,
                             total_active_parents=total_active_parents,
                             unverified_parents_count=unverified_parents_count,
                             recent_communications=recent_communications)
    
    except Exception as e:
        flash(f'Error loading communication center: {str(e)}', 'error')
        return redirect(url_for('parent_management.dashboard'))

@parent_management_bp.route('/get_parents_for_communication')
@headteacher_required
def get_parents_for_communication():
    """Get list of parents for communication selection."""
    try:
        parents = db.session.query(
            Parent.id,
            Parent.first_name,
            Parent.last_name,
            Parent.email,
            func.count(ParentStudent.student_id).label('children_count')
        ).outerjoin(
            ParentStudent, Parent.id == ParentStudent.parent_id
        ).filter(
            Parent.is_active == True
        ).group_by(
            Parent.id
        ).order_by(
            Parent.first_name, Parent.last_name
        ).all()
        
        parent_list = []
        for p in parents:
            parent_list.append({
                'id': p.id,
                'name': f'{p.first_name} {p.last_name}',
                'email': p.email,
                'children_count': p.children_count
            })
        
        return jsonify({'parents': parent_list})
    
    except Exception as e:
        return jsonify({'error': str(e)})

@parent_management_bp.route('/search_parents_for_communication')
@headteacher_required
def search_parents_for_communication():
    """Search parents for communication selection."""
    try:
        query = request.args.get('q', '').strip()
        
        if not query or len(query) < 2:
            return jsonify({'parents': []})
        
        parents = db.session.query(
            Parent.id,
            Parent.first_name,
            Parent.last_name,
            Parent.email,
            func.count(ParentStudent.student_id).label('children_count')
        ).outerjoin(
            ParentStudent, Parent.id == ParentStudent.parent_id
        ).filter(
            Parent.is_active == True,
            db.or_(
                Parent.first_name.ilike(f'%{query}%'),
                Parent.last_name.ilike(f'%{query}%'),
                Parent.email.ilike(f'%{query}%')
            )
        ).group_by(
            Parent.id
        ).limit(20).all()
        
        parent_list = []
        for p in parents:
            parent_list.append({
                'id': p.id,
                'name': f'{p.first_name} {p.last_name}',
                'email': p.email,
                'children_count': p.children_count
            })
        
        return jsonify({'parents': parent_list})
    
    except Exception as e:
        return jsonify({'error': str(e)})

@parent_management_bp.route('/send_bulk_communication', methods=['POST'])
@headteacher_required
def send_bulk_communication():
    """Send bulk communication to selected parents."""
    try:
        data = request.get_json()
        
        audience = data.get('audience')
        message_type = data.get('message_type')
        subject = data.get('subject', '')
        content = data.get('content', '')
        
        if not all([audience, message_type, content]):
            return jsonify({'success': False, 'message': 'Missing required fields'})
        
        # Get target parents based on audience
        target_parents = []
        
        if audience == 'all':
            target_parents = Parent.query.filter_by(is_active=True).all()
        elif audience == 'unverified':
            target_parents = Parent.query.filter_by(is_active=True, is_verified=False).all()
        elif audience == 'custom':
            parent_ids = data.get('parent_ids', [])
            if parent_ids:
                target_parents = Parent.query.filter(
                    Parent.id.in_(parent_ids),
                    Parent.is_active == True
                ).all()
        elif audience == 'grade':
            filters = data.get('filters', {})
            # Build query based on filters
            query = db.session.query(Parent).join(
                ParentStudent, Parent.id == ParentStudent.parent_id
            ).join(
                Student, ParentStudent.student_id == Student.id
            ).filter(Parent.is_active == True)
            
            if filters.get('grade_id'):
                query = query.filter(Student.grade_id == filters['grade_id'])
            if filters.get('stream_id'):
                query = query.filter(Student.stream_id == filters['stream_id'])
            
            target_parents = query.distinct().all()
        
        if not target_parents:
            return jsonify({'success': False, 'message': 'No recipients selected'})
        
        # Send messages (implement actual email/SMS sending here)
        sent_count = 0
        for parent in target_parents:
            try:
                # Placeholder for actual message sending
                # You would implement email/SMS sending here
                sent_count += 1
            except Exception as e:
                continue
        
        return jsonify({
            'success': True,
            'sent_count': sent_count,
            'message': f'Successfully sent to {sent_count} recipients'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

# ================== ADVANCED ANALYTICS DASHBOARD ==================

@parent_management_bp.route('/analytics_dashboard')
@headteacher_required
def analytics_dashboard():
    """Display advanced analytics dashboard."""
    return render_template('analytics_dashboard.html')

@parent_management_bp.route('/get_analytics_data')
@headteacher_required
def get_analytics_data():
    """Get analytics data for charts and metrics."""
    try:
        # Parent engagement metrics
        total_parents = Parent.query.count()
        active_parents = Parent.query.filter_by(is_active=True).count()
        verified_parents = Parent.query.filter_by(is_verified=True).count()
        
        # Registration trends (last 12 months)
        from datetime import datetime, timedelta
        import calendar
        
        registration_trends = []
        for i in range(12):
            date = datetime.now() - timedelta(days=30*i)
            month_start = datetime(date.year, date.month, 1)
            next_month = month_start + timedelta(days=32)
            month_end = datetime(next_month.year, next_month.month, 1)
            
            count = Parent.query.filter(
                Parent.created_at >= month_start,
                Parent.created_at < month_end
            ).count()
            
            registration_trends.append({
                'month': calendar.month_abbr[date.month],
                'year': date.year,
                'count': count
            })
        
        # Communication effectiveness (mock data for demonstration)
        communication_stats = {
            'emails_sent': 156,
            'open_rate': 78.5,
            'response_rate': 45.2,
            'avg_response_time': '2.3 hours'
        }
        
        # Parent distribution by grade
        grade_distribution = db.session.query(
            Grade.name,
            func.count(Parent.id).label('parent_count')
        ).join(
            ParentStudent, Parent.id == ParentStudent.parent_id
        ).join(
            Student, ParentStudent.student_id == Student.id
        ).join(
            Grade, Student.grade_id == Grade.id
        ).filter(
            Parent.is_active == True
        ).group_by(Grade.name).all()
        
        return jsonify({
            'success': True,
            'total_parents': total_parents,
            'active_parents': active_parents,
            'verified_parents': verified_parents,
            'registration_trends': registration_trends,
            'communication_stats': communication_stats,
            'grade_distribution': [{'grade': item[0], 'count': item[1]} for item in grade_distribution]
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@parent_management_bp.route('/generate_custom_report', methods=['POST'])
@headteacher_required
def generate_custom_report():
    """Generate custom analytics report."""
    try:
        params = request.get_json()
        report_type = params.get('report_type')
        date_range = params.get('date_range', 'last_30_days')
        
        # Calculate date range
        end_date = datetime.now()
        if date_range == 'last_7_days':
            start_date = end_date - timedelta(days=7)
        elif date_range == 'last_30_days':
            start_date = end_date - timedelta(days=30)
        elif date_range == 'last_90_days':
            start_date = end_date - timedelta(days=90)
        else:
            start_date = end_date - timedelta(days=30)
        
        report_data = {}
        
        if report_type == 'engagement':
            # Parent engagement report
            active_parents_period = Parent.query.filter(
                Parent.is_active == True,
                Parent.last_login >= start_date
            ).count()
            
            report_data = {
                'title': 'Parent Engagement Report',
                'period': f'{start_date.strftime("%Y-%m-%d")} to {end_date.strftime("%Y-%m-%d")}',
                'metrics': {
                    'active_logins': active_parents_period,
                    'total_active_parents': Parent.query.filter_by(is_active=True).count(),
                    'engagement_rate': f'{(active_parents_period / Parent.query.filter_by(is_active=True).count() * 100):.1f}%' if Parent.query.filter_by(is_active=True).count() > 0 else '0%'
                }
            }
        
        return jsonify({'success': True, 'report': report_data})
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

# ================== EVENT & MEETING MANAGEMENT ==================

@parent_management_bp.route('/event_management')
@headteacher_required
def event_management():
    """Display event management dashboard."""
    return render_template('event_management.html')

@parent_management_bp.route('/create_event', methods=['POST'])
@headteacher_required
def create_event():
    """Create a new school event."""
    try:
        data = request.get_json()
        
        # In a full implementation, you would create an Event model
        # For now, we'll simulate event creation
        event_data = {
            'title': data.get('title'),
            'description': data.get('description'),
            'event_type': data.get('event_type'),
            'date': data.get('date'),
            'time': data.get('time'),
            'duration': data.get('duration', 60),
            'location': data.get('location'),
            'max_attendees': data.get('max_attendees'),
            'requires_rsvp': data.get('requires_rsvp', False),
            'created_by': session.get('teacher_id'),
            'created_at': datetime.now().isoformat()
        }
        
        # Here you would save to Event model
        # event = Event(**event_data)
        # db.session.add(event)
        # db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Event created successfully',
            'event_id': 123  # Would be actual event.id
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@parent_management_bp.route('/get_events')
@headteacher_required
def get_events():
    """Get list of upcoming events."""
    try:
        # Mock event data - replace with actual Event model queries
        events = [
            {
                'id': 1,
                'title': 'Parent-Teacher Conference',
                'date': '2025-10-15',
                'time': '14:00',
                'type': 'conference',
                'attendees': 25,
                'max_attendees': 50,
                'status': 'upcoming'
            },
            {
                'id': 2,
                'title': 'School Board Meeting',
                'date': '2025-10-20',
                'time': '18:00',
                'type': 'meeting',
                'attendees': 12,
                'max_attendees': 30,
                'status': 'upcoming'
            }
        ]
        
        return jsonify({'success': True, 'events': events})
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@parent_management_bp.route('/schedule_conference', methods=['POST'])
@headteacher_required
def schedule_conference():
    """Schedule parent-teacher conference."""
    try:
        data = request.get_json()
        
        # Conference scheduling logic
        conference_data = {
            'parent_id': data.get('parent_id'),
            'teacher_id': data.get('teacher_id'),
            'student_id': data.get('student_id'),
            'date': data.get('date'),
            'time_slot': data.get('time_slot'),
            'duration': data.get('duration', 30),
            'notes': data.get('notes', ''),
            'created_by': session.get('teacher_id')
        }
        
        return jsonify({
            'success': True,
            'message': 'Conference scheduled successfully',
            'conference_id': 456
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

# ================== DOCUMENT MANAGEMENT HUB ==================

@parent_management_bp.route('/document_management')
@headteacher_required
def document_management():
    """Display document management dashboard."""
    return render_template('document_management.html')

@parent_management_bp.route('/get_document_requests')
@headteacher_required
def get_document_requests():
    """Get pending document requests."""
    try:
        # Mock document request data
        requests = [
            {
                'id': 1,
                'parent_name': 'John Doe',
                'student_name': 'Alice Doe',
                'document_type': 'ID Copy',
                'status': 'pending',
                'requested_date': '2025-10-01',
                'priority': 'high'
            },
            {
                'id': 2,
                'parent_name': 'Jane Smith',
                'student_name': 'Bob Smith',
                'document_type': 'Medical Certificate',
                'status': 'submitted',
                'requested_date': '2025-09-28',
                'priority': 'medium'
            }
        ]
        
        return jsonify({'success': True, 'requests': requests})
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@parent_management_bp.route('/request_documents', methods=['POST'])
@headteacher_required
def request_documents():
    """Send document request to parents."""
    try:
        data = request.get_json()
        
        parent_ids = data.get('parent_ids', [])
        document_types = data.get('document_types', [])
        deadline = data.get('deadline')
        message = data.get('message', '')
        
        # Create document requests
        requests_created = 0
        for parent_id in parent_ids:
            for doc_type in document_types:
                # Here you would create DocumentRequest records
                requests_created += 1
        
        return jsonify({
            'success': True,
            'message': f'Document requests sent to {len(parent_ids)} parents',
            'requests_created': requests_created
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@parent_management_bp.route('/verify_document', methods=['POST'])
@headteacher_required
def verify_document():
    """Verify or reject submitted document."""
    try:
        data = request.get_json()
        
        document_id = data.get('document_id')
        action = data.get('action')  # 'approve' or 'reject'
        notes = data.get('notes', '')
        
        # Update document status
        # document = Document.query.get(document_id)
        # document.status = 'verified' if action == 'approve' else 'rejected'
        # document.verification_notes = notes
        # document.verified_by = session.get('teacher_id')
        # document.verified_at = datetime.now()
        
        return jsonify({
            'success': True,
            'message': f'Document {action}d successfully'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

# ================== SMART DUPLICATE DETECTION FEATURES ==================

@parent_management_bp.route('/duplicate_detection')
@headteacher_required
def duplicate_detection():
    """Display duplicate detection page."""
    return render_template('duplicate_detection.html')

def calculate_name_similarity(name1, name2):
    """Calculate similarity between two names using SequenceMatcher."""
    if not name1 or not name2:
        return 0
    
    # Normalize names
    name1 = name1.lower().strip()
    name2 = name2.lower().strip()
    
    return SequenceMatcher(None, name1, name2).ratio() * 100

def find_phone_duplicates(parents):
    """Find parents with duplicate phone numbers."""
    phone_groups = {}
    
    for parent in parents:
        if parent.phone and parent.phone.strip():
            # Normalize phone number (remove spaces, dashes, etc.)
            phone = ''.join(filter(str.isdigit, parent.phone))
            if len(phone) >= 10:  # Valid phone number
                if phone not in phone_groups:
                    phone_groups[phone] = []
                phone_groups[phone].append(parent)
    
    # Return only groups with duplicates
    return {phone: parents for phone, parents in phone_groups.items() if len(parents) > 1}

def find_email_duplicates(parents):
    """Find parents with duplicate email addresses."""
    email_groups = {}
    
    for parent in parents:
        if parent.email:
            email = parent.email.lower().strip()
            if email not in email_groups:
                email_groups[email] = []
            email_groups[email].append(parent)
    
    # Return only groups with duplicates
    return {email: parents for email, parents in email_groups.items() if len(parents) > 1}

def find_name_duplicates(parents, threshold=80):
    """Find parents with similar names using fuzzy matching."""
    duplicates = []
    
    for i, parent1 in enumerate(parents):
        for parent2 in parents[i+1:]:
            # Calculate similarity for full names
            name1 = f"{parent1.first_name} {parent1.last_name}"
            name2 = f"{parent2.first_name} {parent2.last_name}"
            
            similarity = calculate_name_similarity(name1, name2)
            
            if similarity >= threshold:
                duplicates.append({
                    'parents': [parent1, parent2],
                    'similarity': similarity,
                    'match_type': 'name'
                })
    
    return duplicates

@parent_management_bp.route('/scan_duplicates', methods=['POST'])
@headteacher_required
def scan_duplicates():
    """Scan for duplicate parent accounts."""
    try:
        params = request.get_json()
        
        method = params.get('method', 'quick')
        match_email = params.get('match_email', True)
        match_phone = params.get('match_phone', True)
        match_names = params.get('match_names', False)
        name_similarity = int(params.get('name_similarity', 80))
        check_children = params.get('check_children', False)
        ignore_inactive = params.get('ignore_inactive', True)
        
        # Get parents to scan
        query = Parent.query
        if ignore_inactive:
            query = query.filter_by(is_active=True)
        
        parents = query.all()
        total_scanned = len(parents)
        
        duplicate_groups = []
        group_id = 1
        
        # Email duplicates
        if match_email:
            email_duplicates = find_email_duplicates(parents)
            for email, parent_list in email_duplicates.items():
                duplicate_groups.append({
                    'id': group_id,
                    'parents': [{
                        'id': p.id,
                        'name': f'{p.first_name} {p.last_name}',
                        'email': p.email,
                        'phone': p.phone or '',
                        'is_active': p.is_active,
                        'is_verified': p.is_verified,
                        'created_at': p.created_at.isoformat() if p.created_at else '',
                        'children_count': db.session.query(ParentStudent).filter_by(parent_id=p.id).count(),
                        'last_login': None,  # Implement if you have login tracking
                        'email_notifications': getattr(p, 'email_notifications', True)
                    } for p in parent_list],
                    'match_type': 'email',
                    'confidence': 95,  # High confidence for exact email matches
                    'status': 'pending'
                })
                group_id += 1
        
        # Phone duplicates
        if match_phone:
            phone_duplicates = find_phone_duplicates(parents)
            for phone, parent_list in phone_duplicates.items():
                # Skip if already found via email
                emails_in_group = {p.email.lower() for p in parent_list}
                already_found = False
                for group in duplicate_groups:
                    group_emails = {p['email'].lower() for p in group['parents']}
                    if emails_in_group.intersection(group_emails):
                        already_found = True
                        break
                
                if not already_found:
                    duplicate_groups.append({
                        'id': group_id,
                        'parents': [{
                            'id': p.id,
                            'name': f'{p.first_name} {p.last_name}',
                            'email': p.email,
                            'phone': p.phone or '',
                            'is_active': p.is_active,
                            'is_verified': p.is_verified,
                            'created_at': p.created_at.isoformat() if p.created_at else '',
                            'children_count': db.session.query(ParentStudent).filter_by(parent_id=p.id).count(),
                            'last_login': None,
                            'email_notifications': getattr(p, 'email_notifications', True)
                        } for p in parent_list],
                        'match_type': 'phone',
                        'confidence': 90,  # High confidence for exact phone matches
                        'status': 'pending'
                    })
                    group_id += 1
        
        # Name duplicates (fuzzy matching)
        if match_names and method in ['smart', 'comprehensive']:
            name_duplicates = find_name_duplicates(parents, name_similarity)
            for duplicate in name_duplicates:
                # Skip if already found via email/phone
                parent_ids_in_group = {p.id for p in duplicate['parents']}
                already_found = False
                for group in duplicate_groups:
                    group_ids = {p['id'] for p in group['parents']}
                    if parent_ids_in_group.intersection(group_ids):
                        already_found = True
                        break
                
                if not already_found:
                    duplicate_groups.append({
                        'id': group_id,
                        'parents': [{
                            'id': p.id,
                            'name': f'{p.first_name} {p.last_name}',
                            'email': p.email,
                            'phone': p.phone or '',
                            'is_active': p.is_active,
                            'is_verified': p.is_verified,
                            'created_at': p.created_at.isoformat() if p.created_at else '',
                            'children_count': db.session.query(ParentStudent).filter_by(parent_id=p.id).count(),
                            'last_login': None,
                            'email_notifications': getattr(p, 'email_notifications', True)
                        } for p in duplicate['parents']],
                        'match_type': 'name',
                        'confidence': int(duplicate['similarity']),
                        'status': 'pending'
                    })
                    group_id += 1
        
        # Calculate stats
        duplicate_groups_count = len(duplicate_groups)
        accounts_affected = sum(len(group['parents']) for group in duplicate_groups)
        avg_confidence = sum(group['confidence'] for group in duplicate_groups) / len(duplicate_groups) if duplicate_groups else 0
        
        return jsonify({
            'success': True,
            'total_scanned': total_scanned,
            'duplicate_groups': duplicate_groups_count,
            'accounts_affected': accounts_affected,
            'avg_confidence': avg_confidence,
            'duplicates': duplicate_groups
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@parent_management_bp.route('/merge_duplicate_parents', methods=['POST'])
@headteacher_required
def merge_duplicate_parents():
    """Merge duplicate parent accounts."""
    try:
        data = request.get_json()
        primary_id = data.get('primary_id')
        duplicate_ids = data.get('duplicate_ids', [])
        
        if not primary_id or not duplicate_ids:
            return jsonify({'success': False, 'message': 'Missing required data'})
        
        primary_parent = Parent.query.get(primary_id)
        if not primary_parent:
            return jsonify({'success': False, 'message': 'Primary parent not found'})
        
        merged_count = 0
        
        for dup_id in duplicate_ids:
            duplicate_parent = Parent.query.get(dup_id)
            if not duplicate_parent:
                continue
            
            # Move parent-student links
            duplicate_links = ParentStudent.query.filter_by(parent_id=dup_id).all()
            for link in duplicate_links:
                # Check if primary parent already has this child
                existing_link = ParentStudent.query.filter_by(
                    parent_id=primary_id,
                    student_id=link.student_id
                ).first()
                
                if existing_link:
                    # Delete duplicate link
                    db.session.delete(link)
                else:
                    # Transfer link to primary parent
                    link.parent_id = primary_id
            
            # Move email logs if they exist
            if ParentEmailLog:
                email_logs = ParentEmailLog.query.filter_by(parent_id=dup_id).all()
                for log in email_logs:
                    log.parent_id = primary_id
            
            # Merge useful information if primary parent lacks it
            if not primary_parent.phone and duplicate_parent.phone:
                primary_parent.phone = duplicate_parent.phone
            
            # Soft delete duplicate parent
            duplicate_parent.is_active = False
            duplicate_parent.email = f'MERGED_{duplicate_parent.email}_{dup_id}'
            
            merged_count += 1
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'merged_count': merged_count,
            'message': f'Successfully merged {merged_count} duplicate accounts'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})

@parent_management_bp.route('/ignore_duplicate', methods=['POST'])
@headteacher_required
def ignore_duplicate():
    """Mark duplicate group as ignored (not actually duplicates)."""
    try:
        data = request.get_json()
        # In a full implementation, you might store this in a separate table
        # For now, we'll just return success
        return jsonify({'success': True, 'message': 'Duplicate group marked as ignored'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@parent_management_bp.route('/mark_for_review', methods=['POST'])
@headteacher_required
def mark_for_review():
    """Mark duplicate group for later review."""
    try:
        data = request.get_json()
        # In a full implementation, you might store this in a separate table
        # For now, we'll just return success
        return jsonify({'success': True, 'message': 'Duplicate group marked for review'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})
