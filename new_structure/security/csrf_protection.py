"""
Cross-Site Request Forgery (CSRF) Protection Module
Comprehensive protection against CSRF attacks.
"""

import secrets
import hmac
import hashlib
import time
import logging
from functools import wraps
from flask import session, request, abort, current_app

class CSRFProtection:
    """Comprehensive CSRF protection."""
    
    TOKEN_LENGTH = 32
    TOKEN_LIFETIME = 3600  # 1 hour
    
    @classmethod
    def generate_token(cls):
        """
        Generate a secure CSRF token.
        
        Returns:
            str: CSRF token
        """
        # Generate random token
        token = secrets.token_urlsafe(cls.TOKEN_LENGTH)
        
        # Add timestamp
        timestamp = str(int(time.time()))
        
        # Create HMAC signature
        secret_key = current_app.config.get('SECRET_KEY', 'default-secret')
        message = f"{token}:{timestamp}"
        signature = hmac.new(
            secret_key.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()
        
        # Combine token, timestamp, and signature
        csrf_token = f"{token}:{timestamp}:{signature}"
        
        return csrf_token
    
    @classmethod
    def validate_token(cls, token, session_token=None):
        """
        Validate CSRF token.
        
        Args:
            token: Token to validate
            session_token: Token stored in session (optional)
            
        Returns:
            bool: True if token is valid
        """
        if not token:
            return False
        
        try:
            # Parse token
            parts = token.split(':')
            if len(parts) != 3:
                return False
            
            token_value, timestamp_str, signature = parts
            timestamp = int(timestamp_str)
            
            # Check token age
            current_time = int(time.time())
            if current_time - timestamp > cls.TOKEN_LIFETIME:
                logging.warning("CSRF token expired")
                return False
            
            # Verify signature
            secret_key = current_app.config.get('SECRET_KEY', 'default-secret')
            message = f"{token_value}:{timestamp_str}"
            expected_signature = hmac.new(
                secret_key.encode(),
                message.encode(),
                hashlib.sha256
            ).hexdigest()
            
            if not hmac.compare_digest(signature, expected_signature):
                logging.warning("CSRF token signature invalid")
                return False
            
            # If session token provided, compare
            if session_token and not hmac.compare_digest(token, session_token):
                logging.warning("CSRF token doesn't match session")
                return False
            
            return True
            
        except (ValueError, IndexError) as e:
            logging.warning(f"CSRF token validation error: {e}")
            return False
    
    @classmethod
    def get_token_from_request(cls):
        """
        Extract CSRF token from request.
        
        Returns:
            str: CSRF token or None
        """
        # Check form data
        if 'csrf_token' in request.form:
            return request.form['csrf_token']
        
        # Check JSON data
        if request.is_json and request.json and 'csrf_token' in request.json:
            return request.json['csrf_token']
        
        # Check headers
        if 'X-CSRFToken' in request.headers:
            return request.headers['X-CSRFToken']
        
        if 'X-CSRF-Token' in request.headers:
            return request.headers['X-CSRF-Token']
        
        return None
    
    @classmethod
    def protect_session(cls):
        """
        Add CSRF protection to current session.
        """
        if 'csrf_token' not in session:
            session['csrf_token'] = cls.generate_token()
    
    @classmethod
    def get_session_token(cls):
        """
        Get CSRF token from session, generating if needed.
        
        Returns:
            str: CSRF token
        """
        cls.protect_session()
        return session['csrf_token']
    
    @classmethod
    def validate_request(cls):
        """
        Validate CSRF token for current request.
        
        Returns:
            bool: True if request is valid
        """
        # Skip validation for safe methods
        if request.method in ['GET', 'HEAD', 'OPTIONS']:
            return True
        
        # Get tokens
        request_token = cls.get_token_from_request()
        session_token = session.get('csrf_token')
        
        if not request_token:
            logging.warning(f"CSRF token missing from {request.method} request to {request.endpoint}")
            return False
        
        if not session_token:
            logging.warning("CSRF token missing from session")
            return False
        
        return cls.validate_token(request_token, session_token)
    
    @classmethod
    def regenerate_token(cls):
        """
        Regenerate CSRF token (useful after login/logout).
        """
        session['csrf_token'] = cls.generate_token()

def csrf_protect(f):
    """
    Decorator to protect routes from CSRF attacks.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not CSRFProtection.validate_request():
            user_id = session.get('teacher_id', 'anonymous')
            ip_address = request.remote_addr
            logging.warning(f"CSRF attack blocked: User {user_id} from {ip_address} on {request.endpoint}")
            abort(403, "CSRF token validation failed")
        
        return f(*args, **kwargs)
    
    return decorated_function

def require_csrf_token(f):
    """
    Decorator to ensure CSRF token is present in session.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        CSRFProtection.protect_session()
        return f(*args, **kwargs)
    
    return decorated_function

class CSRFTokenGenerator:
    """
    Helper class for generating CSRF tokens in templates.
    """
    
    @staticmethod
    def get_token():
        """
        Get CSRF token for use in templates.
        
        Returns:
            str: CSRF token
        """
        return CSRFProtection.get_session_token()
    
    @staticmethod
    def get_hidden_input():
        """
        Get HTML hidden input with CSRF token.
        
        Returns:
            str: HTML input element
        """
        token = CSRFProtection.get_session_token()
        return f'<input type="hidden" name="csrf_token" value="{token}">'
    
    @staticmethod
    def get_meta_tag():
        """
        Get HTML meta tag with CSRF token.
        
        Returns:
            str: HTML meta element
        """
        token = CSRFProtection.get_session_token()
        return f'<meta name="csrf-token" content="{token}">'

class AjaxCSRFProtection:
    """
    Helper class for AJAX CSRF protection.
    """
    
    @staticmethod
    def get_headers():
        """
        Get headers for AJAX requests.
        
        Returns:
            dict: Headers with CSRF token
        """
        token = CSRFProtection.get_session_token()
        return {
            'X-CSRFToken': token,
            'Content-Type': 'application/json'
        }
    
    @staticmethod
    def get_jquery_setup():
        """
        Get jQuery setup code for automatic CSRF protection.
        
        Returns:
            str: JavaScript code
        """
        token = CSRFProtection.get_session_token()
        return f"""
        // Automatic CSRF protection for jQuery AJAX requests
        $.ajaxSetup({{
            beforeSend: function(xhr, settings) {{
                if (!/^(GET|HEAD|OPTIONS|TRACE)$/i.test(settings.type) && !this.crossDomain) {{
                    xhr.setRequestHeader("X-CSRFToken", "{token}");
                }}
            }}
        }});
        """
    
    @staticmethod
    def get_fetch_wrapper():
        """
        Get fetch wrapper function with CSRF protection.
        
        Returns:
            str: JavaScript code
        """
        token = CSRFProtection.get_session_token()
        return f"""
        // Secure fetch wrapper with CSRF protection
        function secureFetch(url, options = {{}}) {{
            const defaults = {{
                headers: {{
                    'X-CSRFToken': '{token}',
                    'Content-Type': 'application/json'
                }}
            }};
            
            // Merge options
            const config = {{
                ...defaults,
                ...options,
                headers: {{
                    ...defaults.headers,
                    ...options.headers
                }}
            }};
            
            return fetch(url, config);
        }}
        """

def init_csrf_protection(app):
    """
    Initialize CSRF protection for Flask app.
    
    Args:
        app: Flask application instance
    """
    @app.before_request
    def csrf_protect_request():
        # Skip CSRF protection for certain endpoints
        exempt_endpoints = [
            'static',
            'auth.login',
            'auth.admin_login',
            'auth.teacher_login',
            'auth.parent_login'
        ]
        
        if request.endpoint in exempt_endpoints:
            return

        # Skip CSRF for specific blueprints and paths (handled separately)
        blueprint = (request.blueprint or '')
        if blueprint in ('parent_management', 'mpesa', 'parent_portal'):
            return

        # Also allow by URL prefix to be safe if blueprint isn't set
        if request.path.startswith('/parent_management/') or request.path.startswith('/mpesa/'):
            return
        
        # Skip for GET requests to most endpoints
        if request.method == 'GET':
            CSRFProtection.protect_session()
            return
        
        # Validate CSRF for state-changing requests
        if not CSRFProtection.validate_request():
            user_id = session.get('teacher_id', 'anonymous')
            ip_address = request.remote_addr
            logging.warning(f"CSRF attack blocked: User {user_id} from {ip_address} on {request.endpoint}")
            abort(403, "CSRF token validation failed")
    
    # Add template globals
    @app.template_global()
    def csrf_token():
        return CSRFProtection.get_session_token()
    
    @app.template_global()
    def csrf_input():
        return CSRFTokenGenerator.get_hidden_input()
    
    @app.template_global()
    def csrf_meta():
        return CSRFTokenGenerator.get_meta_tag()
    
    # Add context processor
    @app.context_processor
    def inject_csrf_token():
        return {
            'csrf_token': CSRFProtection.get_session_token(),
            'csrf_input': CSRFTokenGenerator.get_hidden_input(),
            'csrf_meta': CSRFTokenGenerator.get_meta_tag()
        }
