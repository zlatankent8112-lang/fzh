"""
Server-Side Request Forgery (SSRF) Protection Module
Comprehensive protection against SSRF attacks.
"""

import re
import socket
import ipaddress
import logging
from functools import wraps
from urllib.parse import urlparse
from flask import request, abort
import requests

class SSRFProtection:
    """Comprehensive SSRF protection."""
    
    # Blocked IP ranges (RFC 1918 private networks and others)
    BLOCKED_IP_RANGES = [
        '0.0.0.0/8',        # Current network
        '10.0.0.0/8',       # Private network
        '127.0.0.0/8',      # Loopback
        '169.254.0.0/16',   # Link-local
        '172.16.0.0/12',    # Private network
        '192.168.0.0/16',   # Private network
        '224.0.0.0/4',      # Multicast
        '240.0.0.0/4',      # Reserved
        '::1/128',          # IPv6 loopback
        'fc00::/7',         # IPv6 private
        'fe80::/10',        # IPv6 link-local
        'ff00::/8',         # IPv6 multicast
    ]
    
    # Blocked hostnames
    BLOCKED_HOSTNAMES = [
        'localhost',
        'metadata.google.internal',
        'instance-data',
        'metadata',
        'metadata.azure.com',
        'metadata.packet.net',
        'metadata.digitalocean.com',
        'metadata.vultr.com',
        'metadata.linode.com',
        'metadata.scaleway.com',
        'metadata.oracle.com',
        'metadata.aws.amazon.com',
        'metadata.gce.internal',
        'metadata.internal',
        'consul',
        'vault',
        'etcd',
        'kubernetes',
        'docker',
        'registry',
    ]
    
    # Blocked ports
    BLOCKED_PORTS = [
        22,    # SSH
        23,    # Telnet
        25,    # SMTP
        53,    # DNS
        110,   # POP3
        143,   # IMAP
        993,   # IMAPS
        995,   # POP3S
        1433,  # MSSQL
        1521,  # Oracle
        3306,  # MySQL
        3389,  # RDP
        5432,  # PostgreSQL
        5984,  # CouchDB
        6379,  # Redis
        8086,  # InfluxDB
        9200,  # Elasticsearch
        9300,  # Elasticsearch
        11211, # Memcached
        27017, # MongoDB
        50070, # Hadoop
    ]
    
    # Allowed protocols
    ALLOWED_PROTOCOLS = ['http', 'https']
    
    # Allowed domains (whitelist approach)
    ALLOWED_DOMAINS = [
        # Add your trusted domains here
        'api.example.com',
        'cdn.example.com',
        'trusted-service.com'
    ]
    
    @classmethod
    def is_ip_blocked(cls, ip_address):
        """
        Check if IP address is in blocked ranges.
        
        Args:
            ip_address: IP address to check
            
        Returns:
            bool: True if IP is blocked
        """
        try:
            ip = ipaddress.ip_address(ip_address)
            
            for blocked_range in cls.BLOCKED_IP_RANGES:
                if ip in ipaddress.ip_network(blocked_range, strict=False):
                    return True
            
            return False
            
        except ValueError:
            # Invalid IP address
            return True
    
    @classmethod
    def is_hostname_blocked(cls, hostname):
        """
        Check if hostname is blocked.
        
        Args:
            hostname: Hostname to check
            
        Returns:
            bool: True if hostname is blocked
        """
        hostname_lower = hostname.lower()
        
        # Check exact matches
        if hostname_lower in cls.BLOCKED_HOSTNAMES:
            return True
        
        # Check for patterns
        blocked_patterns = [
            r'.*\.local$',
            r'.*\.internal$',
            r'.*\.localhost$',
            r'.*metadata.*',
            r'.*consul.*',
            r'.*vault.*',
            r'.*etcd.*',
            r'.*kubernetes.*',
            r'.*docker.*',
        ]
        
        for pattern in blocked_patterns:
            if re.match(pattern, hostname_lower):
                return True
        
        return False
    
    @classmethod
    def is_port_blocked(cls, port):
        """
        Check if port is blocked.
        
        Args:
            port: Port number to check
            
        Returns:
            bool: True if port is blocked
        """
        return port in cls.BLOCKED_PORTS
    
    @classmethod
    def resolve_hostname(cls, hostname):
        """
        Resolve hostname to IP addresses.
        
        Args:
            hostname: Hostname to resolve
            
        Returns:
            list: List of IP addresses or empty list if error
        """
        try:
            # Get all IP addresses for hostname
            addr_info = socket.getaddrinfo(hostname, None)
            ip_addresses = [info[4][0] for info in addr_info]
            
            # Remove duplicates
            return list(set(ip_addresses))
            
        except socket.gaierror:
            logging.warning(f"Failed to resolve hostname: {hostname}")
            return []
    
    @classmethod
    def validate_url(cls, url):
        """
        Validate URL for SSRF protection.
        
        Args:
            url: URL to validate
            
        Returns:
            tuple: (is_valid, error_message)
        """
        if not url:
            return False, "Empty URL"
        
        try:
            parsed = urlparse(url)
            
            # Check protocol
            if parsed.scheme not in cls.ALLOWED_PROTOCOLS:
                return False, f"Protocol {parsed.scheme} not allowed"
            
            # Check hostname
            hostname = parsed.hostname
            if not hostname:
                return False, "No hostname in URL"
            
            # Check if hostname is blocked
            if cls.is_hostname_blocked(hostname):
                return False, f"Hostname {hostname} is blocked"
            
            # Check port
            port = parsed.port
            if port and cls.is_port_blocked(port):
                return False, f"Port {port} is blocked"
            
            # Resolve hostname to IP addresses
            ip_addresses = cls.resolve_hostname(hostname)
            if not ip_addresses:
                return False, f"Cannot resolve hostname {hostname}"
            
            # Check if any resolved IP is blocked
            for ip in ip_addresses:
                if cls.is_ip_blocked(ip):
                    return False, f"IP address {ip} is blocked"
            
            # Check against whitelist if configured
            if cls.ALLOWED_DOMAINS:
                domain_allowed = False
                for allowed_domain in cls.ALLOWED_DOMAINS:
                    if hostname == allowed_domain or hostname.endswith('.' + allowed_domain):
                        domain_allowed = True
                        break
                
                if not domain_allowed:
                    return False, f"Domain {hostname} not in whitelist"
            
            return True, "URL is valid"
            
        except Exception as e:
            return False, f"URL validation error: {str(e)}"
    
    @classmethod
    def safe_request(cls, url, method='GET', **kwargs):
        """
        Make a safe HTTP request with SSRF protection.
        
        Args:
            url: URL to request
            method: HTTP method
            **kwargs: Additional arguments for requests
            
        Returns:
            requests.Response or None: Response object or None if blocked
        """
        # Validate URL
        is_valid, error_msg = cls.validate_url(url)
        if not is_valid:
            logging.warning(f"SSRF attempt blocked: {error_msg} for URL {url}")
            return None
        
        try:
            # Set safe defaults
            safe_kwargs = {
                'timeout': 10,  # 10 second timeout
                'allow_redirects': False,  # Don't follow redirects
                'verify': True,  # Verify SSL certificates
                'stream': False,  # Don't stream response
            }
            
            # Update with provided kwargs
            safe_kwargs.update(kwargs)
            
            # Limit response size
            if 'stream' not in kwargs:
                safe_kwargs['stream'] = True
            
            # Make request
            response = requests.request(method, url, **safe_kwargs)
            
            # Check response size
            content_length = response.headers.get('content-length')
            if content_length and int(content_length) > 10 * 1024 * 1024:  # 10MB limit
                logging.warning(f"Response too large from {url}")
                return None
            
            # Read response with size limit
            if safe_kwargs.get('stream'):
                content = b''
                for chunk in response.iter_content(chunk_size=8192):
                    content += chunk
                    if len(content) > 10 * 1024 * 1024:  # 10MB limit
                        logging.warning(f"Response too large from {url}")
                        return None
                
                # Replace response content
                response._content = content
            
            return response
            
        except requests.exceptions.RequestException as e:
            logging.error(f"Request error for {url}: {e}")
            return None
    
    @classmethod
    def validate_webhook_url(cls, webhook_url):
        """
        Validate webhook URL for SSRF protection.
        
        Args:
            webhook_url: Webhook URL to validate
            
        Returns:
            bool: True if webhook URL is safe
        """
        is_valid, _ = cls.validate_url(webhook_url)
        return is_valid
    
    @classmethod
    def validate_redirect_url(cls, redirect_url, allowed_domains=None):
        """
        Validate redirect URL for SSRF protection.
        
        Args:
            redirect_url: Redirect URL to validate
            allowed_domains: List of allowed domains for redirect
            
        Returns:
            bool: True if redirect URL is safe
        """
        if not redirect_url:
            return False
        
        try:
            parsed = urlparse(redirect_url)
            
            # Only allow relative URLs or specific domains
            if not parsed.netloc:
                # Relative URL - check for path traversal
                if '..' in parsed.path or parsed.path.startswith('//'):
                    return False
                return True
            
            # Absolute URL - validate domain
            if allowed_domains:
                hostname = parsed.hostname
                if not hostname:
                    return False
                
                for allowed_domain in allowed_domains:
                    if hostname == allowed_domain or hostname.endswith('.' + allowed_domain):
                        return True
                
                return False
            
            # If no allowed domains specified, use general validation
            is_valid, _ = cls.validate_url(redirect_url)
            return is_valid
            
        except Exception:
            return False

def ssrf_protection(f):
    """
    Decorator to protect routes from SSRF attacks.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check form data for URLs
        if request.form:
            for field_name, value in request.form.items():
                if _looks_like_url(value):
                    is_valid, error_msg = SSRFProtection.validate_url(value)
                    if not is_valid:
                        logging.warning(f"SSRF attempt blocked in form field {field_name}: {error_msg}")
                        abort(400, "Invalid URL detected")
        
        # Check query parameters for URLs
        if request.args:
            for param_name, value in request.args.items():
                if _looks_like_url(value):
                    is_valid, error_msg = SSRFProtection.validate_url(value)
                    if not is_valid:
                        logging.warning(f"SSRF attempt blocked in query param {param_name}: {error_msg}")
                        abort(400, "Invalid URL detected")
        
        # Check JSON data for URLs
        if request.is_json and request.json:
            for key, value in request.json.items():
                if isinstance(value, str) and _looks_like_url(value):
                    is_valid, error_msg = SSRFProtection.validate_url(value)
                    if not is_valid:
                        logging.warning(f"SSRF attempt blocked in JSON field {key}: {error_msg}")
                        abort(400, "Invalid URL detected")
        
        return f(*args, **kwargs)
    
    @staticmethod
    def _looks_like_url(value):
        """Check if value looks like a URL."""
        if not isinstance(value, str):
            return False
        return value.startswith(('http://', 'https://', 'ftp://', 'ftps://'))
    
    return decorated_function

class SecureHTTPClient:
    """
    Secure HTTP client with SSRF protection.
    """
    
    def __init__(self, allowed_domains=None):
        """
        Initialize secure HTTP client.
        
        Args:
            allowed_domains: List of allowed domains
        """
        self.allowed_domains = allowed_domains or []
    
    def get(self, url, **kwargs):
        """Make a safe GET request."""
        return SSRFProtection.safe_request(url, 'GET', **kwargs)
    
    def post(self, url, **kwargs):
        """Make a safe POST request."""
        return SSRFProtection.safe_request(url, 'POST', **kwargs)
    
    def put(self, url, **kwargs):
        """Make a safe PUT request."""
        return SSRFProtection.safe_request(url, 'PUT', **kwargs)
    
    def delete(self, url, **kwargs):
        """Make a safe DELETE request."""
        return SSRFProtection.safe_request(url, 'DELETE', **kwargs)
    
    def head(self, url, **kwargs):
        """Make a safe HEAD request."""
        return SSRFProtection.safe_request(url, 'HEAD', **kwargs)
