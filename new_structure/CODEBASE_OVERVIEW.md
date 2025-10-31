# Hillview School Management System - Codebase Overview

## 🏗️ Architecture Overview

The Hillview School Management System is a **Flask-based web application** following an **MVC (Model-View-Controller) pattern** with a **service layer** for business logic separation. The application is designed for enterprise-grade school management with comprehensive security features.

### Technology Stack
- **Backend Framework**: Flask 2.3.3 (Python)
- **Database**: MySQL (primary) / SQLite (testing) with SQLAlchemy ORM
- **Frontend**: HTML5, CSS3, JavaScript (vanilla + Bootstrap)
- **Security**: Custom security modules, CSRF protection, rate limiting
- **Caching**: Redis (optional, falls back to memory)
- **Background Tasks**: Celery/RQ support
- **PDF Generation**: ReportLab

---

## 📁 Directory Structure

```
new_structure/
├── __init__.py              # Flask app factory (create_app)
├── run.py                   # Application entry point
├── config.py                # Configuration (dev/test/prod)
├── extensions.py            # Flask extensions (db, csrf, limiter)
│
├── models/                  # Database Models (SQLAlchemy)
│   ├── user.py              # Teacher model (authentication)
│   ├── academic.py          # Student, Subject, Grade, Stream, Mark
│   ├── assignment.py        # Teacher-Subject assignments
│   ├── parent.py            # Parent portal models
│   ├── permission.py        # Role-based permissions
│   └── ...
│
├── views/                   # Route Handlers (Blueprints)
│   ├── auth.py              # Authentication routes
│   ├── admin.py             # Headteacher routes
│   ├── teacher.py           # Subject teacher routes
│   ├── classteacher.py     # Class teacher routes
│   ├── parent_simple.py     # Parent portal routes
│   └── ...
│
├── services/                # Business Logic Layer
│   ├── auth_service.py      # Authentication logic
│   ├── report_service.py    # Report generation
│   ├── student_service.py   # Student management
│   ├── grading_service.py   # Grade calculations
│   └── ...
│
├── templates/               # HTML Templates (Jinja2)
│   ├── login.html
│   ├── headteacher.html
│   ├── teacher.html
│   ├── classteacher/
│   └── ...
│
├── static/                  # Static Assets
│   ├── css/                 # Stylesheets
│   ├── js/                  # JavaScript files
│   ├── images/              # Images/logos
│   └── templates/           # Upload templates (CSV/XLSX)
│
├── security/                # Security Modules
│   ├── authorization.py     # Access control
│   ├── access_control.py    # Resource permissions
│   ├── sql_injection_protection.py
│   ├── xss_protection.py
│   └── ...
│
├── middleware/              # Request Middleware
│   └── mark_sanitizer.py    # Input sanitization
│
├── utils/                   # Utility Functions
│   ├── database_init.py     # DB initialization
│   ├── pdf_generator.py     # PDF utilities
│   ├── audit.py             # Audit logging
│   └── ...
│
└── tests/                   # Test Suite
```

---

## 🔐 User Roles & Authentication

### Three Main User Roles:

1. **Headteacher (Admin)**
   - Full system access
   - Manage teachers, students, subjects, grades, streams
   - Configure school settings
   - View analytics and reports
   - Assign teachers to subjects/classes

2. **Subject Teacher**
   - Enter marks for assigned subjects
   - View subject-specific reports
   - Limited to assigned subjects only

3. **Class Teacher**
   - Manage students in assigned class
   - Enter marks for multiple subjects (with permissions)
   - Generate class reports
   - View class analytics

4. **Parent** (Optional Portal)
   - View child's reports
   - Access academic progress
   - Email notifications

### Authentication Flow:
- **Login Routes**: `/admin_login`, `/teacher_login`, `/classteacher_login`
- **Session Management**: Flask sessions with security hardening
- **Password Security**: PBKDF2-HMAC-SHA256 hashing
- **Account Lockout**: 5 failed attempts → 30 min lockout
- **Session Rotation**: On successful login to prevent fixation

---

## 📊 Core Functionality

### 1. Student Management
- **Model**: `Student` (models/academic.py)
- **Features**:
  - Add/Edit/Delete students
  - Assign to Grade & Stream
  - Track admission numbers
  - Student promotion between grades

### 2. Marks Entry & Processing
- **Model**: `Mark` (models/academic.py)
- **Entry Methods**:
  - **Manual Entry**: Form-based entry per subject
  - **Bulk Upload**: CSV/XLSX file upload
  - **Collaborative Entry**: Multiple teachers can contribute
  
- **Mark Processing**:
  - Raw marks → Percentage calculation
  - Grade assignment (CBC grading system)
  - Composite subject aggregation (e.g., English = Grammar + Literature)
  - Missing mark policies

### 3. Report Generation
- **Types**:
  - **Individual Reports**: Single student PDF reports
  - **Class Reports**: Full class with rankings
  - **Subject Reports**: Subject-specific performance
  - **Grade Marksheets**: Grade-level summaries

- **Services**:
  - `report_service.py`: Core report logic
  - `class_report_builder.py`: Class report builder with caching
  - `grade_marksheet_service.py`: Grade-level reports

- **Features**:
  - PDF generation (ReportLab)
  - Subject filtering
  - Position ranking
  - Mean grade calculation
  - Performance remarks

### 4. Analytics & Dashboards
- **Headteacher Analytics**:
  - School-wide performance metrics
  - Grade/stream comparisons
  - Subject performance analysis
  - Top performing students

- **Class Teacher Analytics**:
  - Class-specific metrics
  - Student progress tracking
  - Subject averages

- **Services**: `analytics_service.py`, `report_based_analytics_service.py`

### 5. Subject Management
- **Features**:
  - Create subjects by education level (lower/upper primary, junior secondary)
  - Composite subjects (e.g., English with Grammar/Literature components)
  - Subject-component relationships
  - Weight-based aggregation

- **Model**: `Subject` (models/academic.py)

### 6. Teacher Assignments
- **Model**: `TeacherSubjectAssignment`
- **Features**:
  - Assign teachers to subjects
  - Assign class teachers to streams
  - Permission-based access control
  - Bulk assignment tools

### 7. Permission System
- **Models**: `ClassTeacherPermission`, `FunctionPermission`
- **Features**:
  - Role-based access control (RBAC)
  - Function-level permissions
  - Subject-scoped permissions
  - Request/approval workflow

---

## 🔒 Security Features

### OWASP Top 10 Protection:
- **A1: Broken Access Control**: Centralized authorization, decorators
- **A2: Cryptographic Failures**: PBKDF2 hashing, secure session management
- **A3: Injection**: SQLAlchemy ORM, input validation, sanitization
- **A4: Insecure Design**: Session rotation, debug route protection
- **A5: Security Misconfiguration**: Environment-based configs
- **A6: Vulnerable Components**: Pinned dependencies
- **A7: XSS**: Output sanitization, CSP headers
- **A8: Insecure Deserialization**: Safe JSON handling
- **A9: Using Components with Known Vulnerabilities**: Dependency auditing
- **A10: Insufficient Logging**: Comprehensive audit logging

### Security Modules:
- `security/authorization.py`: Access control checks
- `security/access_control.py`: Resource permission matrix
- `security/sql_injection_protection.py`: SQL injection prevention
- `security/xss_protection.py`: XSS prevention
- `security/file_upload_security.py`: Secure file uploads

### Rate Limiting:
- Flask-Limiter integration
- Redis-backed (falls back to memory)
- Configurable per route

---

## 🔄 Application Flow

### Startup Sequence:
1. `run.py` → Loads environment variables (.env)
2. `create_app()` → Initializes Flask app
3. Config loaded (DevelopmentConfig/ProductionConfig)
4. Extensions initialized (db, csrf, limiter)
5. Blueprints registered
6. Middleware attached
7. Server starts on port 8080

### Request Flow:
1. **Request arrives** → Middleware checks (security, rate limiting)
2. **Route matching** → Blueprint handles request
3. **Authentication check** → Session validation
4. **Authorization check** → Role/permission validation
5. **Service layer** → Business logic execution
6. **Database operations** → SQLAlchemy queries
7. **Response rendering** → Template rendering or JSON

### Report Generation Flow:
1. User selects: Grade, Stream, Term, Assessment Type
2. `ClassReportBuilder.build()` → Fetches data, applies filters
3. Mark calculation → Percentage, grades, totals
4. Composite aggregation → Component subjects combined
5. Ranking → Position calculation
6. PDF generation → ReportLab creates PDF
7. Download/Preview → User receives report

---

## 📝 Key Models & Relationships

### Teacher ↔ Subjects (Many-to-Many)
- `teacher_subjects` association table
- Teachers can teach multiple subjects
- Subjects can have multiple teachers

### Student ↔ Stream (Many-to-One)
- Each student belongs to one stream
- Stream belongs to one grade

### Mark Relationships:
- `Mark.student_id` → Student
- `Mark.subject_id` → Subject
- `Mark.term_id` → Term
- `Mark.assessment_type_id` → AssessmentType
- `Mark.grade_id` → Grade
- `Mark.stream_id` → Stream

### Composite Subjects:
- `Subject.is_composite` → True for parent subjects
- `Subject.is_component` → True for component subjects
- `Subject.composite_parent` → Parent subject name
- Components aggregated by weight

---

## 🛠️ Key Services

### `report_service.py`
- `get_class_report_data()`: Fetch class report data
- `generate_individual_report()`: Generate student PDF
- Handles mark aggregation, ranking, grade calculation

### `class_report_builder.py`
- `ClassReportBuilder.build()`: Unified report builder
- Caching support
- Subject filtering
- Composite subject handling

### `auth_service.py`
- `authenticate_teacher()`: Login validation
- Account lockout handling
- Session management

### `grading_service.py`
- Grade calculation (CBC system)
- Percentage to grade conversion
- Performance remarks

### `student_service.py`
- Student CRUD operations
- Promotion handling
- Data validation

---

## 🌐 Main Routes

### Authentication:
- `/` → Login page
- `/admin_login` → Headteacher login
- `/teacher_login` → Subject teacher login
- `/classteacher_login` → Class teacher login
- `/logout` → Logout

### Headteacher Dashboard:
- `/headteacher` → Main dashboard
- `/manage_teachers` → Teacher management
- `/manage_students` → Student management
- `/manage_subjects` → Subject management
- `/analytics` → Analytics dashboard

### Class Teacher:
- `/classteacher` → Dashboard
- `/classteacher/class_report` → Generate class report
- `/classteacher/upload` → Upload marks
- `/classteacher/analytics` → Class analytics

### Subject Teacher:
- `/teacher` → Dashboard
- `/teacher/subject_report` → Subject reports
- `/teacher/enter_marks` → Enter marks

---

## 🗄️ Database Schema

### Core Tables:
- `teacher`: User accounts
- `student`: Student records
- `subject`: Subjects
- `grade`: Grade levels
- `stream`: Class streams
- `term`: Academic terms
- `assessment_type`: Assessment types (midterm, endterm)
- `mark`: Student marks
- `teacher_subject_assignment`: Teacher-subject links
- `class_teacher_permission`: Class teacher permissions

### Configuration Tables:
- `school_configuration`: School settings
- `report_configuration`: Report templates
- `assessment_weights_config`: Assessment weighting
- `rounding_mode_config`: Grade rounding rules

---

## 📦 Configuration

### Environment Variables:
- `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`
- `SECRET_KEY`: Flask secret key (required in production)
- `FLASK_ENV`: `development` / `testing` / `production`
- `REDIS_HOST`, `REDIS_PORT`: Redis connection (optional)
- `REDIS_DISABLED=1`: Disable Redis, use memory

### Config Classes:
- `DevelopmentConfig`: Debug enabled, MySQL DB
- `TestingConfig`: In-memory SQLite, no CSRF
- `ProductionConfig`: Strict security, file logging

---

## 🚀 Running the Application

### Development:
```bash
cd new_structure
python run.py
```
- Server runs on `http://localhost:8080`
- Default login: `headteacher` / `admin123`

### Production:
- Use `gunicorn` with `wsgi.py`
- Set `FLASK_ENV=production`
- Configure MySQL and Redis
- Set secure `SECRET_KEY`

---

## 📚 Key Design Patterns

1. **Application Factory**: `create_app()` pattern for flexible configuration
2. **Blueprint Pattern**: Modular route organization
3. **Service Layer**: Business logic separated from routes
4. **Repository Pattern**: Models abstract database access
5. **Decorator Pattern**: Security decorators (`@enforce`, `@classteacher_required`)
6. **Strategy Pattern**: Different grading strategies (CBC, 8-4-4)

---

## 🔍 Important Files to Understand

1. **`__init__.py`**: App initialization, middleware setup
2. **`config.py`**: Configuration management
3. **`models/academic.py`**: Core data models
4. **`views/classteacher.py`**: Largest view file (class teacher functionality)
5. **`services/report_service.py`**: Report generation logic
6. **`security/authorization.py`**: Access control system

---

## 📈 Scalability Features

- **Database Connection Pooling**: SQLAlchemy pool configuration
- **Redis Caching**: Optional caching layer
- **Background Tasks**: Celery/RQ support for async operations
- **Rate Limiting**: Prevents abuse
- **Database Migrations**: Alembic support

---

## 🧪 Testing

- **Test Framework**: pytest
- **Test Config**: `TestingConfig` with in-memory SQLite
- **Test Files**: `tests/` directory
- **Fixtures**: Database fixtures for testing

---

## 📖 Documentation Files

The codebase includes extensive markdown documentation:
- `SECURITY_IMPLEMENTATION_REPORT.md`: Security features
- `IMPLEMENTATION_COMPLETE.md`: Feature implementation
- `DEPLOYMENT_GUIDE.md`: Deployment instructions
- Various fix/solution documents for specific features

---

## 🎯 Key Workflows

### Adding a New Student:
1. Headteacher → `/manage_students` → Add student form
2. Select Grade & Stream
3. Student created in database
4. Can now receive marks

### Entering Marks:
1. Class Teacher → Select Grade/Stream/Subject/Term/Assessment
2. Form displays all students in class
3. Enter marks → Submit
4. Marks saved to `mark` table
5. Collaborative marks status updated

### Generating Reports:
1. Select Grade, Stream, Term, Assessment Type
2. Optionally filter subjects
3. `ClassReportBuilder` fetches data
4. Calculations performed (totals, averages, grades)
5. PDF generated via ReportLab
6. Download or preview

---

This overview provides a comprehensive understanding of the codebase structure and functionality. The system is well-organized with clear separation of concerns, comprehensive security, and scalable architecture.

