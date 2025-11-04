"""
Flask extensions for the Hillview School Management System.
This file initializes Flask extensions used throughout the application.
"""
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Table as _sa_Table
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
import os
import logging
import time
import builtins

# Simple module-level cache for redis health data
_redis_health_cache = {
    'last_check': 0.0,
    'status': 'unknown',
    'info': {},
    'error': None
}

def _choose_rate_limit_storage():
    """Return a confirmed-working rate limit storage URI.

    Strategy:
    1. Honor explicit RATE_LIMIT_STORAGE_URI / RATELIMIT_STORAGE_URL / env override.
    2. If disabled via REDIS_DISABLED=1 -> memory.
    3. In tests (PYTEST_CURRENT_TEST) default to memory unless FORCE_REDIS.
    4. Attempt single fast ping (<=300ms) to Redis; on failure fall back to memory.
    5. Never raise – always return a usable URI.
    """
    # 1. Explicit config (environment)
    explicit = os.environ.get('RATE_LIMIT_STORAGE_URI') or os.environ.get('RATELIMIT_STORAGE_URL')
    if explicit:
        candidate = explicit
    else:
        candidate = 'redis://localhost:6379/0'

    # 2. Disabled flag
    if os.environ.get('REDIS_DISABLED') == '1':
        return 'memory://'

    # 3. Pytest default memory unless forced
    if 'PYTEST_CURRENT_TEST' in os.environ and not os.environ.get('FORCE_REDIS'):
        return 'memory://'

    # 4. Probe Redis quickly
    if candidate.startswith('redis://'):
        try:
            import redis  # type: ignore
            r = redis.Redis.from_url(candidate, socket_connect_timeout=0.3, socket_timeout=0.3)
            r.ping()
            return candidate  # success
        except Exception:
            logging.getLogger(__name__).warning("Redis unavailable during initialization; using in-memory rate limiting")
            return 'memory://'
    return candidate


def _redis_health(storage_uri: str, force: bool = False):
    """Return lightweight Redis health details (cached ~10s).

    Returns a dict with keys: status (up/down/unknown), used_memory, connected_clients, error
    Non-fatal: any exception returns status=down.
    """
    global _redis_health_cache
    now = time.time()
    if not force and (now - _redis_health_cache['last_check'] < 10):
        return _redis_health_cache

    if not storage_uri.startswith('redis://'):
        _redis_health_cache.update({'last_check': now, 'status': 'not-redis', 'info': {}, 'error': None})
        return _redis_health_cache
    try:
        import redis  # type: ignore
        r = redis.Redis.from_url(storage_uri, socket_connect_timeout=0.5, socket_timeout=0.5)
        pong = r.ping()
        if not pong:
            raise RuntimeError('PING failed')
        info = r.info(section='memory')
        clients = r.info(section='clients')
        _redis_health_cache.update({
            'last_check': now,
            'status': 'up',
            'info': {
                'used_memory_human': info.get('used_memory_human'),
                'used_memory': info.get('used_memory'),
                'connected_clients': clients.get('connected_clients')
            },
            'error': None
        })
    except Exception as e:
        _redis_health_cache.update({'last_check': now, 'status': 'down', 'info': {}, 'error': str(e)})
    return _redis_health_cache

# Initialize extensions
# Ensure a single SQLAlchemy instance across any module re-imports during tests
if hasattr(builtins, '_new_structure_db_singleton') and isinstance(getattr(builtins, '_new_structure_db_singleton'), SQLAlchemy):
    db = getattr(builtins, '_new_structure_db_singleton')  # type: ignore[assignment]
else:
    db = SQLAlchemy()
    setattr(builtins, '_new_structure_db_singleton', db)

# Ensure declarative model table construction is resilient to module re-imports that
# share the same SQLAlchemy metadata (common in tests that purge and re-import modules).
# By default, SQLAlchemy raises if a Table with the same name is already present in
# metadata. We set extend_existing=True by default for all models to allow safe redefinition
# of Table constructs on re-import without affecting runtime behavior.
try:
    orig_table_cls = getattr(db.Model, '__table_cls__', None)
    def _table_cls_with_extend(cls, *args, **kwargs):  # type: ignore[no-redef]
        kwargs.setdefault('extend_existing', True)
        if callable(orig_table_cls):
            try:
                # If Flask-SQLAlchemy provides its own implementation, prefer it
                return orig_table_cls.__func__(cls, *args, **kwargs)  # type: ignore[attr-defined]
            except Exception:
                pass
        return _sa_Table(*args, **kwargs)
    # Assign as a classmethod on the base model
    db.Model.__table_cls__ = classmethod(_table_cls_with_extend)  # type: ignore[assignment]
except Exception:
    # Best-effort; if patching fails, models may still specify extend_existing individually
    pass
csrf = CSRFProtect()

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.login_view = 'auth.admin_login'  # Redirect to admin/teacher login page
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'info'

# Establish storage at import time with safe fallback
_initial_storage = _choose_rate_limit_storage()
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=_initial_storage,
)

def configure_rate_limiter(app):
    """Bind limiter to app and log chosen backend.

    We intentionally do NOT attempt to reconfigure storage here to avoid
    triggering Redis connection attempts after fallback selection.
    To override, set FORCE_REDIS=1 and ensure Redis is reachable before import.
    """
    try:
        # If we already determined memory fallback but app config still points to redis, override it
        if _initial_storage == 'memory://' and str(app.config.get('RATELIMIT_STORAGE_URL', '')).startswith('redis://'):
            app.logger.warning("Overriding configured redis RATELIMIT_STORAGE_URL with memory:// due to earlier fallback")
            app.config['RATELIMIT_STORAGE_URL'] = 'memory://'

        # Always expose the chosen storage through a canonical config key for downstream checks/tests
        if 'RATE_LIMIT_STORAGE_URI' not in app.config:
            app.config['RATE_LIMIT_STORAGE_URI'] = _initial_storage
        # Backwards compatibility alias
        if 'RATELIMIT_STORAGE_URL' not in app.config:
            app.config['RATELIMIT_STORAGE_URL'] = _initial_storage

        if not getattr(limiter, 'app', None):
            limiter.init_app(app)

        # Production enforcement: disallow memory backend unless explicitly permitted
        storage_attr = getattr(limiter, 'storage_uri', None) or getattr(limiter, '_storage_uri', None)
        if app.config.get('ENV') == 'production' or app.config.get('FLASK_ENV') == 'production':
            allow_memory = os.environ.get('ALLOW_IN_MEMORY_LIMITS') == '1'
            if str(storage_attr).startswith('memory://') and not allow_memory:
                raise RuntimeError("In-memory rate limiting backend is not allowed in production. Set RATE_LIMIT_STORAGE_URI to a Redis URI or ALLOW_IN_MEMORY_LIMITS=1 for a temporary override.")

        # After init, log final storage backend (use internal attribute if public one not present in this version)
        app.logger.info(f"Rate limiter storage active: {storage_attr}")

        # Attach helper for health endpoint
        app.extensions['rate_limiter_health'] = lambda: _redis_health(str(storage_attr))
    except Exception as e:
        app.logger.warning(f"Limiter initialization failed; continuing without limits: {e}")