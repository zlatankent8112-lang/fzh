# Phase 5: Automated Scheduled Reporting - Implementation Plan

## 📋 Overview

Implement automated report generation and email delivery system using APScheduler.

---

## 🎯 Goals

1. Schedule automatic report generation (daily/weekly/monthly)
2. Email delivery to administrators
3. Configurable schedules
4. Report history tracking

---

## 🛠️ Implementation Steps

### Step 1: Email Service Setup (30 min)

**Create**: `services/email_service.py`

```python
from flask_mail import Mail, Message
from flask import current_app

class EmailService:
    @staticmethod
    def send_analytics_email(recipients, subject, report_file, report_type):
        """Send email with attached report"""
        # Configure Flask-Mail
        # Attach Excel/PDF file
        # Send email
        pass
```

**Dependencies**:

```bash
pip install Flask-Mail
```

**Configuration** in `config.py`:

```python
MAIL_SERVER = 'smtp.gmail.com'
MAIL_PORT = 587
MAIL_USE_TLS = True
MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
```

---

### Step 2: Scheduled Jobs Configuration (45 min)

**Create**: `services/scheduler_service.py`

```python
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime

class SchedulerService:
    scheduler = None

    @classmethod
    def init_scheduler(cls, app):
        """Initialize APScheduler"""
        cls.scheduler = BackgroundScheduler()
        cls.scheduler.start()
        cls.configure_jobs(app)

    @classmethod
    def configure_jobs(cls, app):
        """Configure scheduled jobs"""
        # Daily report: 6 AM every day
        cls.scheduler.add_job(
            func=cls.generate_daily_report,
            trigger='cron',
            hour=6,
            minute=0,
            args=[app]
        )

        # Weekly report: Monday 8 AM
        cls.scheduler.add_job(
            func=cls.generate_weekly_report,
            trigger='cron',
            day_of_week='mon',
            hour=8,
            minute=0,
            args=[app]
        )

        # Monthly report: 1st of month, 9 AM
        cls.scheduler.add_job(
            func=cls.generate_monthly_report,
            trigger='cron',
            day=1,
            hour=9,
            minute=0,
            args=[app]
        )

    @staticmethod
    def generate_daily_report(app):
        """Generate and email daily report"""
        with app.app_context():
            # Query yesterday's data
            # Generate Excel
            # Email to admins
            pass
```

---

### Step 3: Admin Configuration UI (60 min)

**Create**: `templates/fees/scheduled_reports_config.html`

**Features**:

- Enable/disable scheduled reports
- Configure email recipients
- Set report frequency (daily/weekly/monthly)
- Choose report types (analytics/defaulters/balance)
- Test email button

**Route**: `/fees/scheduled-reports`

```python
@fee_bp.route('/scheduled-reports', methods=['GET', 'POST'])
@fee_access_required
def scheduled_reports_config():
    if request.method == 'POST':
        # Save configuration to database
        # Update scheduler jobs
        pass
    return render_template('fees/scheduled_reports_config.html')
```

---

### Step 4: Report History Tracking (30 min)

**Create Model**: `models/scheduled_report.py`

```python
class ScheduledReport(db.Model):
    __tablename__ = 'scheduled_reports'

    id = Column(Integer, primary_key=True)
    report_type = Column(String(50))  # daily/weekly/monthly
    report_category = Column(String(50))  # analytics/defaulters/balance
    generated_at = Column(DateTime, default=datetime.utcnow)
    file_path = Column(String(255))
    email_sent = Column(Boolean, default=False)
    email_recipients = Column(String(500))
    status = Column(String(20))  # success/failed
    error_message = Column(Text)
```

**Migration**:

```bash
alembic revision --autogenerate -m "Add scheduled_reports table"
alembic upgrade head
```

---

### Step 5: Job Management Interface (45 min)

**Features**:

- View scheduled jobs
- Manually trigger report generation
- View report history
- Download previous reports
- Re-send failed emails

**Route**: `/fees/scheduled-reports/history`

---

## 📊 Report Types

### Daily Report

**Content**:

- Yesterday's collection summary
- New payments count
- Payment method breakdown
- Daily trends chart

**Recipients**: Finance team
**Delivery**: 6:00 AM

---

### Weekly Report

**Content**:

- Week's collection summary
- Grade-wise performance
- Stream-wise performance
- Top payers and defaulters

**Recipients**: School administrators
**Delivery**: Monday 8:00 AM

---

### Monthly Report

**Content**:

- Full analytics dashboard (all sheets)
- Monthly trends
- Outstanding balances
- Collection rate analysis
- Comparative analysis (vs previous month)

**Recipients**: Management + Board
**Delivery**: 1st of month, 9:00 AM

---

## 🔧 Configuration Options

### Email Settings Table

```sql
CREATE TABLE email_config (
    id INTEGER PRIMARY KEY,
    report_type VARCHAR(50),
    enabled BOOLEAN DEFAULT TRUE,
    recipients TEXT,  -- JSON array
    cc_recipients TEXT,
    subject_template VARCHAR(255),
    schedule VARCHAR(50),  -- cron expression
    created_at DATETIME,
    updated_at DATETIME
);
```

---

## 🧪 Testing Plan

### Manual Tests

- [ ] Send test email with attachment
- [ ] Trigger daily report manually
- [ ] Verify cron schedule
- [ ] Check email delivery
- [ ] Validate report content
- [ ] Test failure handling

### Automated Tests

```python
def test_daily_report_generation():
    # Test report generation without email
    pass

def test_email_attachment():
    # Test email with Excel attachment
    pass

def test_scheduler_configuration():
    # Test APScheduler setup
    pass
```

---

## 📁 File Structure

```
services/
  ├── email_service.py          (NEW)
  ├── scheduler_service.py      (NEW)
  └── fee_export_service.py     (EXISTING)

templates/fees/
  ├── scheduled_reports_config.html  (NEW)
  └── scheduled_reports_history.html (NEW)

models/
  └── scheduled_report.py       (NEW)

migrations/
  └── versions/
      └── xxx_add_scheduled_reports.py  (NEW)
```

---

## ⚙️ Environment Variables

Add to `.env`:

```bash
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=True
MAIL_USERNAME=your-email@gmail.com
MAIL_PASSWORD=your-app-password
MAIL_DEFAULT_SENDER=your-email@gmail.com

# Report Recipients
DAILY_REPORT_RECIPIENTS=finance@school.com
WEEKLY_REPORT_RECIPIENTS=admin@school.com,finance@school.com
MONTHLY_REPORT_RECIPIENTS=management@school.com,board@school.com
```

---

## 🚀 Deployment Checklist

- [ ] Install Flask-Mail
- [ ] Configure email server
- [ ] Add environment variables
- [ ] Run migrations
- [ ] Initialize scheduler in app.py
- [ ] Test email delivery
- [ ] Configure cron jobs
- [ ] Monitor first scheduled run
- [ ] Set up error alerts

---

## 📈 Success Metrics

- **Report Delivery Rate**: Target 99%
- **Email Open Rate**: Track via analytics
- **Report Generation Time**: < 30 seconds
- **Storage Usage**: Monitor report file sizes

---

## 🔒 Security Considerations

1. **Email Credentials**: Store in environment variables
2. **Report Access**: Only authorized users can download
3. **File Storage**: Automatic cleanup after 90 days
4. **Error Handling**: Log failures, don't expose details

---

## 💡 Enhancement Ideas

- **Slack Integration**: Post reports to Slack channels
- **SMS Alerts**: Critical defaulter alerts via SMS
- **Custom Report Builder**: Let users design custom reports
- **Report Templates**: Multiple template options
- **Multi-language Reports**: Generate in different languages

---

## 🎯 Estimated Timeline

| Task            | Duration      | Priority |
| --------------- | ------------- | -------- |
| Email Service   | 30 min        | High     |
| Scheduler Setup | 45 min        | High     |
| Admin Config UI | 60 min        | Medium   |
| Report History  | 30 min        | Medium   |
| Job Management  | 45 min        | Low      |
| Testing         | 60 min        | High     |
| **Total**       | **4.5 hours** |          |

---

## 🤝 Dependencies

### Already Installed:

✅ APScheduler

### To Install:

- Flask-Mail

### System Requirements:

- SMTP server access (Gmail/SendGrid/etc)
- Sufficient disk space for report files
- Cron/background job support

---

_Ready to implement when you say go!_
