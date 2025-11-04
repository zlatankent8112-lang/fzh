"""
Configuration settings for the Hillview School Management System.
Enhanced with scalability features, multi-environment support, and
production-grade secret management (no hardcoded passwords/keys in prod).
"""
import os
from typing import Optional
from pathlib import Path
from sqlalchemy.pool import StaticPool
from urllib.parse import quote_plus
from datetime import datetime

class Config:
    """Base configuration class with settings common to all environments.

    Normalized to eliminate duplicated/conflicting definitions observed during A6 assessment.
    """
    # Session / Cookie Security (single authoritative definition; production overrides Secure=True)
    SESSION_COOKIE_NAME = 'hillview_session'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'  # Production may tighten to 'Strict' if needed
    SESSION_COOKIE_SECURE = False    # Set True in ProductionConfig
    PERMANENT_SESSION_LIFETIME = 3600  # 1 hour baseline (Production may shorten or keep)

    # HTTPS / Transport
    FORCE_HTTPS = True  # Enforced in production; ignored in testing

    # Access Control / Auth Hardening
    STRICT_ROLE_ENFORCEMENT = True
    SESSION_PROTECTION = 'strong'
    WTF_CSRF_TIME_LIMIT = 3600

    # Security Validation Toggle
    SECURITY_VALIDATION_STRICT = True  # Disable only for controlled migration scenarios

    # CORS Allowlist (comma-separated origins). Empty => no CORS headers emitted.
    ALLOWED_ORIGINS = os.environ.get('ALLOWED_ORIGINS', '')

    # Content Security Policy (override via env CSP_POLICY if customizing)
    CSP_POLICY = os.environ.get('CSP_POLICY', (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self'; frame-src 'none'; object-src 'none'; base-uri 'self'; form-action 'self'; "
        "frame-ancestors 'none'; upgrade-insecure-requests"
    ))

    # Secret keys (development defaults only; production strictly requires env)
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'your_secret_key_here_change_in_production'
    WTF_CSRF_SECRET_KEY = os.environ.get('WTF_CSRF_SECRET_KEY')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Database Configuration
    # Prefer DATABASE_URL if provided, otherwise compose from individual MySQL env vars
    DATABASE_URL = os.environ.get('DATABASE_URL')

    # MySQL Configuration (safe development defaults; no hardcoded prod password)
    MYSQL_HOST = os.environ.get('MYSQL_HOST') or 'localhost'
    MYSQL_PORT = int(os.environ.get('MYSQL_PORT') or 3306)
    MYSQL_USER = os.environ.get('MYSQL_USER') or 'root'
    # IMPORTANT: No insecure default password. Empty by default for local dev.
    MYSQL_PASSWORD = os.environ.get('MYSQL_PASSWORD') or ''
    MYSQL_DATABASE = os.environ.get('MYSQL_DATABASE') or 'hillview_demo001'
    
    # SQLAlchemy Database URI resolution
    if DATABASE_URL:
        SQLALCHEMY_DATABASE_URI = DATABASE_URL
    else:
        # URL-encode password to safely handle special characters like '@' or '/'
        _ENC_PWD = quote_plus(MYSQL_PASSWORD) if MYSQL_PASSWORD else ''
        SQLALCHEMY_DATABASE_URI = (
            f"mysql+pymysql://{MYSQL_USER}:{_ENC_PWD}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}"
            "?charset=utf8mb4"
        )
    
    # Connection Pool Settings
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 10,
        'pool_timeout': 20,
        'pool_recycle': 3600,
        'pool_pre_ping': True,
        'max_overflow': 20
    }
    
    # (Removed duplicate session config block – unified above)

    # Redis Configuration for Caching and Sessions
    REDIS_HOST = os.environ.get('REDIS_HOST') or 'localhost'
    REDIS_PORT = int(os.environ.get('REDIS_PORT') or 6379)
    REDIS_PASSWORD = os.environ.get('REDIS_PASSWORD')
    REDIS_DB = int(os.environ.get('REDIS_DB') or 0)
    REDIS_URL = f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}" if REDIS_PASSWORD else f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"

    # Cache Configuration
    CACHE_TYPE = 'redis'
    CACHE_DEFAULT_TIMEOUT = 3600  # 1 hour
    CACHE_KEY_PREFIX = 'hillview:'

    # Rate Limiting Configuration
    RATELIMIT_ENABLED = True
    RATELIMIT_STORAGE_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/1"
    RATELIMIT_DEFAULT = "100 per hour"
    RATELIMIT_HEADERS_ENABLED = True

    # Background Tasks Configuration (Celery/RQ)
    CELERY_BROKER_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/2"
    CELERY_RESULT_BACKEND = f"redis://{REDIS_HOST}:{REDIS_PORT}/3"
    CELERY_TASK_SERIALIZER = 'json'
    CELERY_RESULT_SERIALIZER = 'json'
    CELERY_ACCEPT_CONTENT = ['json']
    CELERY_TIMEZONE = 'UTC'
    CELERY_ENABLE_UTC = True

    # File Upload Configuration
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB
    UPLOAD_FOLDER = 'uploads'
    ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'xlsx', 'xls', 'csv'}
    # Security-focused fine-grained file validation (Phase C additions)
    FILE_UPLOAD_MAX_BYTES = int(os.environ.get('FILE_UPLOAD_MAX_BYTES', 5 * 1024 * 1024))  # 5MB logical cap for CSV/XLSX academic imports
    FILE_ALLOWED_DATA_EXTENSIONS = {'.csv', '.xlsx', '.xls'}
    FILE_ALLOWED_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif'}
    FILE_STRICT_MIME_CHECK = True

    # Logging Configuration
    LOG_LEVEL = 'INFO'
    LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    LOG_FILE = None  # Set in environment-specific configs

    # Notification Configuration (SMS & Email)
    # SMS Provider: 'africas_talking', 'twilio', or 'test' (test mode just logs)
    SMS_PROVIDER = os.environ.get('SMS_PROVIDER', 'test')
    
    # Africa's Talking Configuration
    AFRICAS_TALKING_USERNAME = os.environ.get('AFRICAS_TALKING_USERNAME')
    AFRICAS_TALKING_API_KEY = os.environ.get('AFRICAS_TALKING_API_KEY')
    
    # Twilio Configuration
    TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
    TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
    TWILIO_PHONE_NUMBER = os.environ.get('TWILIO_PHONE_NUMBER')
    
    # Email Configuration (SMTP)
    MAIL_SERVER = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'true').lower() == 'true'
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER', MAIL_USERNAME)
    
    # School Information for notifications
    SCHOOL_NAME = os.environ.get('SCHOOL_NAME', 'Hillview School')
    SCHOOL_PHONE = os.environ.get('SCHOOL_PHONE', '+254705204870')
    CURRENT_DATE = datetime.now().strftime('%Y-%m-%d')

    # Security Configuration
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 3600  # 1 hour

    # Application Configuration
    APP_NAME = 'Hillview School Management System'
    APP_VERSION = '2.0.0'

    # Feature Flags (non-invasive defaults)
    # When enabled, reports may use the new MarkCalculator pipeline. Kept False by default.
    REPORTS_USE_MARK_CALCULATOR = os.environ.get('REPORTS_USE_MARK_CALCULATOR', 'false').lower() == 'true'

    @classmethod
    def init_app(cls, app):
        """Initialize application with configuration"""
        # Create necessary directories
        directories = ['logs', 'uploads', 'static/uploads', 'instance', 'backups']
        for directory in directories:
            Path(directory).mkdir(parents=True, exist_ok=True)


class DevelopmentConfig(Config):
    """Configuration for development environment."""
    DEBUG = True
    # SERVER_NAME = 'localhost:5000'  # Commented out to allow access from any IP
    APPLICATION_ROOT = '/'
    PREFERRED_URL_SCHEME = 'http'

    # Development-specific overrides
    CACHE_TYPE = 'simple'  # Use simple cache for development
    RATELIMIT_ENABLED = True  # Enable rate limiting for security testing
    RATELIMIT_STORAGE_URL = 'memory://'  # Use memory storage for development
    LOG_LEVEL = 'DEBUG'
    WTF_CSRF_ENABLED = True  # Enable CSRF protection for security testing

    # Use MySQL for development (inherits from base Config class)
    # SQLALCHEMY_DATABASE_URI is inherited from Config class - MySQL configuration

    @classmethod
    def init_app(cls, app):
        """Initialize development app"""
        super().init_app(app)
        # Additional development setup
        app.logger.setLevel(cls.LOG_LEVEL)


class TestingConfig(Config):
    """Configuration for testing environment."""
    TESTING = True
    DEBUG = False

    # Testing-specific overrides
    CACHE_TYPE = 'null'  # Disable caching for testing
    RATELIMIT_ENABLED = False
    WTF_CSRF_ENABLED = False
    LOG_LEVEL = 'ERROR'
    FORCE_HTTPS = False
    STRICT_ROLE_ENFORCEMENT = False  # Relaxed for testing
    SECRET_KEY = 'test-secret-key-for-testing'

    # Use in-memory SQLite for testing
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    # Ensure in-memory SQLite persists across connections within the test process
    SQLALCHEMY_ENGINE_OPTIONS = {
        'poolclass': StaticPool,
        'connect_args': {'check_same_thread': False},
    }


class ProductionConfig(Config):
    """Production environment hardening."""
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_SAMESITE = 'Strict'
    PERMANENT_SESSION_LIFETIME = 7200  # 2 hours session window
    RATELIMIT_DEFAULT = "200 per hour"
    LOG_LEVEL = 'WARNING'
    LOG_FILE = '/var/log/hillview/app.log'
    WTF_CSRF_TIME_LIMIT = 7200

    # Harden pool & performance
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 20,
        'pool_timeout': 30,
        'pool_recycle': 7200,
        'pool_pre_ping': True,
        'max_overflow': 40
    }

    @classmethod
    def init_app(cls, app):
        super().init_app(app)
        # Strict secret and database configuration validation for production
        # 1) SECRET_KEY must be provided via environment and must not be the placeholder
        secret_from_env = os.environ.get('SECRET_KEY')
        if not secret_from_env or secret_from_env == 'your_secret_key_here_change_in_production':
            raise RuntimeError(
                'SECURITY ERROR: SECRET_KEY must be set via environment for production '
                'and cannot use the development placeholder.'
            )

        # 2) CSRF secret should be provided explicitly (falls back to SECRET_KEY if missing)
        csrf_secret = os.environ.get('WTF_CSRF_SECRET_KEY')
        if not csrf_secret:
            # Not fatal, but strongly recommended; log a warning
            app.logger.warning('WTF_CSRF_SECRET_KEY is not set; falling back to SECRET_KEY. Set an explicit CSRF secret in production.')

        # 3) Database credentials must not rely on insecure defaults
        # Require either DATABASE_URL or a non-empty MYSQL_PASSWORD
        has_database_url = bool(os.environ.get('DATABASE_URL'))
        has_secure_mysql_password = bool(os.environ.get('MYSQL_PASSWORD'))
        if not (has_database_url or has_secure_mysql_password):
            raise RuntimeError(
                'SECURITY ERROR: Database credentials not configured for production. '
                'Provide DATABASE_URL or set MYSQL_PASSWORD in the environment.'
            )

        # Logging setup
        import logging
        from logging.handlers import RotatingFileHandler
        if cls.LOG_FILE:
            try:
                file_handler = RotatingFileHandler(
                    cls.LOG_FILE, maxBytes=10*1024*1024, backupCount=10
                )
                file_handler.setFormatter(logging.Formatter(cls.LOG_FORMAT))
                file_handler.setLevel(logging.INFO)
                app.logger.addHandler(file_handler)
                app.logger.setLevel(logging.INFO)
            except Exception:
                app.logger.warning('Could not attach rotating file handler', exc_info=True)
        app.config['SECURITY_HEADERS'] = {
            'Strict-Transport-Security': 'max-age=31536000; includeSubDomains; preload',
            'X-Content-Type-Options': 'nosniff',
            'X-Frame-Options': 'DENY',
            'X-XSS-Protection': '1; mode=block',
            'Referrer-Policy': 'strict-origin-when-cross-origin'
        }


# Configuration dictionary (after ProductionConfig consolidation)
config = {
    'development': DevelopmentConfig,
    'testing': TestingConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}

def get_config(config_name: Optional[str] = None):
    """
    Get configuration based on environment

    Args:
        config_name: Configuration name (development, production, testing)

    Returns:
        Configuration class
    """
    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'development')

    return config.get(config_name, config['default'])

def is_development() -> bool:
    """Check if running in development environment"""
    return os.environ.get('FLASK_ENV', 'development') == 'development'

def is_production() -> bool:
    """Check if running in production environment"""
    return os.environ.get('FLASK_ENV') == 'production'

def is_testing() -> bool:
    """Check if running in testing environment"""
    return os.environ.get('FLASK_ENV') == 'testing'

# PRODUCTION SECURITY CONFIGURATION
## NOTE: Removed duplicated ProductionConfig definition (consolidated above)
