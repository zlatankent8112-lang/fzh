"""
Remote Code Execution (RCE) Protection Module
Comprehensive protection against RCE vulnerabilities.
"""

import os
import re
import subprocess
import logging
from functools import wraps
from flask import request, abort, current_app

class RCEProtection:
    """Comprehensive RCE protection."""
    
    # Dangerous functions and patterns
    DANGEROUS_FUNCTIONS = [
        'eval', 'exec', 'compile', '__import__', 'open', 'file',
        'input', 'raw_input', 'execfile', 'reload', 'vars', 'dir',
        'globals', 'locals', 'getattr', 'setattr', 'delattr',
        'hasattr', 'callable', 'isinstance', 'issubclass',
        'super', 'property', 'staticmethod', 'classmethod'
    ]
    
    # Command injection patterns
    COMMAND_INJECTION_PATTERNS = [
        r'[;&|`$(){}[\]<>]',  # Shell metacharacters
        r'(rm|del|format|fdisk|mkfs)',  # Dangerous commands
        r'(wget|curl|nc|netcat)',  # Network commands
        r'(python|perl|ruby|php|node)',  # Interpreters
        r'(bash|sh|cmd|powershell)',  # Shells
        r'(cat|type|more|less)',  # File reading commands
        r'(echo|printf|print)',  # Output commands
        r'(find|locate|which|where)',  # Search commands
        r'(ps|top|kill|killall)',  # Process commands
        r'(chmod|chown|chgrp)',  # Permission commands
        r'(su|sudo|runas)',  # Privilege escalation
        r'(crontab|at|schtasks)',  # Scheduling commands
        r'(mount|umount|fsck)',  # Filesystem commands
        r'(iptables|netsh|route)',  # Network configuration
        r'(service|systemctl|sc)',  # Service management
    ]
    
    # Code injection patterns
    CODE_INJECTION_PATTERNS = [
        r'__.*__',  # Python magic methods
        r'import\s+\w+',  # Import statements
        r'from\s+\w+\s+import',  # From-import statements
        r'def\s+\w+\s*\(',  # Function definitions
        r'class\s+\w+\s*\(',  # Class definitions
        r'lambda\s*.*:',  # Lambda expressions
        r'exec\s*\(',  # Exec calls
        r'eval\s*\(',  # Eval calls
        r'compile\s*\(',  # Compile calls
        r'__import__\s*\(',  # Import calls
        r'getattr\s*\(',  # Getattr calls
        r'setattr\s*\(',  # Setattr calls
        r'delattr\s*\(',  # Delattr calls
        r'globals\s*\(\)',  # Globals access
        r'locals\s*\(\)',  # Locals access
        r'vars\s*\(\)',  # Vars access
        r'dir\s*\(\)',  # Dir access
    ]
    
    # File path traversal patterns
    PATH_TRAVERSAL_PATTERNS = [
        r'\.\.',  # Directory traversal
        r'[/\\]etc[/\\]',  # Unix system files
        r'[/\\]proc[/\\]',  # Unix process files
        r'[/\\]sys[/\\]',  # Unix system files
        r'[/\\]dev[/\\]',  # Unix device files
        r'[/\\]var[/\\]',  # Unix variable files
        r'[/\\]tmp[/\\]',  # Temporary files
        r'[/\\]boot[/\\]',  # Boot files
        r'[/\\]root[/\\]',  # Root directory
        r'[/\\]home[/\\]',  # Home directories
        r'C:[/\\]Windows[/\\]',  # Windows system
        r'C:[/\\]Program Files',  # Windows programs
        r'C:[/\\]Users[/\\]',  # Windows users
        r'%SYSTEMROOT%',  # Windows environment
        r'%PROGRAMFILES%',  # Windows environment
        r'%USERPROFILE%',  # Windows environment
    ]
    
    @classmethod
    def detect_command_injection(cls, input_value):
        """
        Detect command injection attempts.
        
        Args:
            input_value: Input to check
            
        Returns:
            bool: True if command injection detected
        """
        if not input_value:
            return False
        
        input_str = str(input_value)
        
        for pattern in cls.COMMAND_INJECTION_PATTERNS:
            if re.search(pattern, input_str, re.IGNORECASE):
                return True
        
        return False
    
    @classmethod
    def detect_code_injection(cls, input_value):
        """
        Detect code injection attempts.
        
        Args:
            input_value: Input to check
            
        Returns:
            bool: True if code injection detected
        """
        if not input_value:
            return False
        
        input_str = str(input_value)
        
        for pattern in cls.CODE_INJECTION_PATTERNS:
            if re.search(pattern, input_str, re.IGNORECASE):
                return True
        
        # Check for dangerous functions
        for func in cls.DANGEROUS_FUNCTIONS:
            if func in input_str:
                return True
        
        return False
    
    @classmethod
    def detect_path_traversal(cls, input_value):
        """
        Detect path traversal attempts.
        
        Args:
            input_value: Input to check
            
        Returns:
            bool: True if path traversal detected
        """
        if not input_value:
            return False
        
        input_str = str(input_value)
        
        for pattern in cls.PATH_TRAVERSAL_PATTERNS:
            if re.search(pattern, input_str, re.IGNORECASE):
                return True
        
        return False
    
    @classmethod
    def sanitize_filename(cls, filename):
        """
        Sanitize filename to prevent path traversal.
        
        Args:
            filename: Original filename
            
        Returns:
            str: Sanitized filename
        """
        if not filename:
            return filename
        
        # Remove path components
        filename = os.path.basename(filename)
        
        # Remove dangerous characters
        filename = re.sub(r'[<>:"/\\|?*]', '', filename)
        
        # Remove leading/trailing dots and spaces
        filename = filename.strip('. ')
        
        # Limit length
        if len(filename) > 255:
            name, ext = os.path.splitext(filename)
            filename = name[:255-len(ext)] + ext
        
        return filename
    
    @classmethod
    def validate_file_path(cls, file_path, allowed_dirs=None):
        """
        Validate file path to prevent directory traversal.
        
        Args:
            file_path: File path to validate
            allowed_dirs: List of allowed directories
            
        Returns:
            bool: True if path is safe
        """
        if not file_path:
            return False
        
        try:
            # Resolve path
            resolved_path = os.path.abspath(file_path)
            
            # Check for path traversal
            if cls.detect_path_traversal(resolved_path):
                return False
            
            # Check against allowed directories
            if allowed_dirs:
                for allowed_dir in allowed_dirs:
                    allowed_abs = os.path.abspath(allowed_dir)
                    if resolved_path.startswith(allowed_abs):
                        return True
                return False
            
            return True
            
        except Exception:
            return False
    
    @classmethod
    def safe_subprocess_call(cls, command, allowed_commands=None):
        """
        Safely execute subprocess command.
        
        Args:
            command: Command to execute
            allowed_commands: List of allowed commands
            
        Returns:
            tuple: (success, output, error)
        """
        if not command:
            return False, "", "Empty command"
        
        # Check if command is allowed
        if allowed_commands:
            cmd_name = command.split()[0] if isinstance(command, str) else command[0]
            if cmd_name not in allowed_commands:
                logging.warning(f"Blocked unauthorized command: {cmd_name}")
                return False, "", "Command not allowed"
        
        # Check for command injection
        if isinstance(command, str) and cls.detect_command_injection(command):
            logging.warning(f"Blocked command injection attempt: {command}")
            return False, "", "Command injection detected"
        
        try:
            # Execute with security restrictions
            result = subprocess.run(
                command,
                shell=False,  # Never use shell=True
                capture_output=True,
                text=True,
                timeout=30,  # 30 second timeout
                check=False
            )
            
            return True, result.stdout, result.stderr
            
        except subprocess.TimeoutExpired:
            logging.warning(f"Command timeout: {command}")
            return False, "", "Command timeout"
        except Exception as e:
            logging.error(f"Command execution error: {e}")
            return False, "", str(e)
    
    @classmethod
    def validate_input_for_rce(cls, input_value, field_name="input"):
        """
        Validate input for RCE attempts.
        
        Args:
            input_value: Input to validate
            field_name: Field name for logging
            
        Returns:
            bool: True if input is safe
        """
        if cls.detect_command_injection(input_value):
            logging.warning(f"Command injection attempt in {field_name}: {input_value}")
            return False
        
        if cls.detect_code_injection(input_value):
            logging.warning(f"Code injection attempt in {field_name}: {input_value}")
            return False
        
        if cls.detect_path_traversal(input_value):
            logging.warning(f"Path traversal attempt in {field_name}: {input_value}")
            return False
        
        return True
    
    @classmethod
    def create_secure_sandbox(cls, sandbox_dir):
        """
        Create a secure sandbox directory.
        
        Args:
            sandbox_dir: Directory to create
            
        Returns:
            bool: True if sandbox created successfully
        """
        try:
            if not os.path.exists(sandbox_dir):
                os.makedirs(sandbox_dir, mode=0o755)
            
            # Set restrictive permissions
            os.chmod(sandbox_dir, 0o755)
            
            return True
            
        except Exception as e:
            logging.error(f"Failed to create sandbox: {e}")
            return False

def rce_protection(f):
    """
    Decorator to protect routes from RCE attacks.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Validate form data
        if request.form:
            safe_form_fields = {'csrf_token'}
            for field_name, value in request.form.items():
                if field_name in safe_form_fields:
                    continue
                if not RCEProtection.validate_input_for_rce(value, field_name):
                    logging.warning(f"RCE attempt blocked on route: {request.endpoint}")
                    abort(400, "Potentially dangerous input detected")
        
        # Validate query parameters
        if request.args:
            for param_name, value in request.args.items():
                if not RCEProtection.validate_input_for_rce(value, f"query_param_{param_name}"):
                    logging.warning(f"RCE attempt blocked on route: {request.endpoint}")
                    abort(400, "Potentially dangerous query parameters detected")
        
        # Validate JSON data
        if request.is_json and request.json:
            for key, value in request.json.items():
                if isinstance(value, str) and not RCEProtection.validate_input_for_rce(value, f"json_{key}"):
                    logging.warning(f"RCE attempt blocked on route: {request.endpoint}")
                    abort(400, "Potentially dangerous JSON data detected")
        
        # Validate file uploads
        if request.files:
            for file_key, file in request.files.items():
                if file.filename:
                    sanitized_filename = RCEProtection.sanitize_filename(file.filename)
                    if not sanitized_filename or sanitized_filename != file.filename:
                        logging.warning(f"Dangerous filename blocked: {file.filename}")
                        abort(400, "Invalid filename")
        
        return f(*args, **kwargs)
    
    return decorated_function

class SecureFileHandler:
    """
    Secure file handling utilities.
    """
    
    @staticmethod
    def safe_file_read(file_path, allowed_dirs=None, max_size=1024*1024):
        """
        Safely read file content.
        
        Args:
            file_path: Path to file
            allowed_dirs: List of allowed directories
            max_size: Maximum file size in bytes
            
        Returns:
            str: File content or None if error
        """
        if not RCEProtection.validate_file_path(file_path, allowed_dirs):
            logging.warning(f"Blocked file read attempt: {file_path}")
            return None
        
        try:
            # Check file size
            if os.path.getsize(file_path) > max_size:
                logging.warning(f"File too large: {file_path}")
                return None
            
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
                
        except Exception as e:
            logging.error(f"File read error: {e}")
            return None
    
    @staticmethod
    def safe_file_write(file_path, content, allowed_dirs=None, max_size=1024*1024):
        """
        Safely write file content.
        
        Args:
            file_path: Path to file
            content: Content to write
            allowed_dirs: List of allowed directories
            max_size: Maximum content size in bytes
            
        Returns:
            bool: True if successful
        """
        if not RCEProtection.validate_file_path(file_path, allowed_dirs):
            logging.warning(f"Blocked file write attempt: {file_path}")
            return False
        
        if len(content.encode('utf-8')) > max_size:
            logging.warning(f"Content too large for file: {file_path}")
            return False
        
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            # Set restrictive permissions
            os.chmod(file_path, 0o644)
            
            return True
            
        except Exception as e:
            logging.error(f"File write error: {e}")
            return False
    
    @staticmethod
    def safe_file_delete(file_path, allowed_dirs=None):
        """
        Safely delete file.
        
        Args:
            file_path: Path to file
            allowed_dirs: List of allowed directories
            
        Returns:
            bool: True if successful
        """
        if not RCEProtection.validate_file_path(file_path, allowed_dirs):
            logging.warning(f"Blocked file delete attempt: {file_path}")
            return False
        
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
            return True
            
        except Exception as e:
            logging.error(f"File delete error: {e}")
            return False
