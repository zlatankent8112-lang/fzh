"""
SMS & Email Notification Configuration Validator
================================================
This script validates your notification credentials and helps you test
your SMS and Email setup before going to production.

Run this script after adding your credentials to .env file:
    python validate_notifications.py
"""

import os
import sys
from dotenv import load_dotenv
from colorama import init, Fore, Style

# Initialize colorama for colored output
init(autoreset=True)

def print_header(text):
    """Print a formatted header"""
    print(f"\n{Fore.CYAN}{'='*60}")
    print(f"{Fore.CYAN}{text.center(60)}")
    print(f"{Fore.CYAN}{'='*60}\n")

def print_success(text):
    """Print success message"""
    print(f"{Fore.GREEN}✓ {text}")

def print_error(text):
    """Print error message"""
    print(f"{Fore.RED}✗ {text}")

def print_warning(text):
    """Print warning message"""
    print(f"{Fore.YELLOW}⚠ {text}")

def print_info(text):
    """Print info message"""
    print(f"{Fore.BLUE}ℹ {text}")

def check_env_file():
    """Check if .env file exists"""
    print_header("Checking Environment Configuration")
    
    if not os.path.exists('.env'):
        print_error(".env file not found!")
        print_info("Creating .env from .env.example...")
        if os.path.exists('.env.example'):
            with open('.env.example', 'r') as src, open('.env', 'w') as dst:
                dst.write(src.read())
            print_success(".env file created. Please edit it with your credentials.")
            return False
        else:
            print_error(".env.example file not found either!")
            return False
    
    print_success(".env file found")
    load_dotenv()
    return True

def validate_sms_provider():
    """Validate SMS provider configuration"""
    print_header("Validating SMS Configuration")
    
    sms_provider = os.getenv('SMS_PROVIDER', 'test')
    print_info(f"SMS Provider: {sms_provider}")
    
    if sms_provider == 'test':
        print_warning("Running in TEST mode - SMS will only log to console")
        print_info("To enable real SMS, set SMS_PROVIDER to 'africas_talking' or 'twilio'")
        return True
    
    elif sms_provider == 'africas_talking':
        print_info("Africa's Talking Configuration:")
        username = os.getenv('AFRICAS_TALKING_USERNAME')
        api_key = os.getenv('AFRICAS_TALKING_API_KEY')
        sender_id = os.getenv('AFRICAS_TALKING_SENDER_ID', 'HILLVIEW')
        
        if not username:
            print_error("AFRICAS_TALKING_USERNAME not set!")
            return False
        print_success(f"Username: {username}")
        
        if not api_key:
            print_error("AFRICAS_TALKING_API_KEY not set!")
            return False
        print_success(f"API Key: {api_key[:10]}..." + "*" * 20)
        
        print_success(f"Sender ID: {sender_id}")
        
        # Try to import and test Africa's Talking
        try:
            import africastalking
            africastalking.initialize(username, api_key)
            sms = africastalking.SMS
            print_success("Africa's Talking SDK imported successfully")
            print_info("Run test_send_sms() to send a test SMS")
            return True
        except ImportError:
            print_error("africastalking module not installed!")
            print_info("Install it with: pip install africastalking")
            return False
        except Exception as e:
            print_error(f"Error initializing Africa's Talking: {str(e)}")
            return False
    
    elif sms_provider == 'twilio':
        print_info("Twilio Configuration:")
        account_sid = os.getenv('TWILIO_ACCOUNT_SID')
        auth_token = os.getenv('TWILIO_AUTH_TOKEN')
        phone_number = os.getenv('TWILIO_PHONE_NUMBER')
        
        if not account_sid:
            print_error("TWILIO_ACCOUNT_SID not set!")
            return False
        print_success(f"Account SID: {account_sid[:10]}..." + "*" * 20)
        
        if not auth_token:
            print_error("TWILIO_AUTH_TOKEN not set!")
            return False
        print_success(f"Auth Token: {auth_token[:10]}..." + "*" * 20)
        
        if not phone_number:
            print_error("TWILIO_PHONE_NUMBER not set!")
            return False
        print_success(f"Phone Number: {phone_number}")
        
        # Try to import and test Twilio
        try:
            from twilio.rest import Client
            client = Client(account_sid, auth_token)
            print_success("Twilio SDK imported successfully")
            print_info("Run test_send_sms() to send a test SMS")
            return True
        except ImportError:
            print_error("twilio module not installed!")
            print_info("Install it with: pip install twilio")
            return False
        except Exception as e:
            print_error(f"Error initializing Twilio: {str(e)}")
            return False
    
    else:
        print_error(f"Unknown SMS_PROVIDER: {sms_provider}")
        print_info("Valid options: test, africas_talking, twilio")
        return False

def validate_email_config():
    """Validate email configuration"""
    print_header("Validating Email Configuration")
    
    mail_server = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
    mail_port = os.getenv('MAIL_PORT', '587')
    mail_username = os.getenv('MAIL_USERNAME')
    mail_password = os.getenv('MAIL_PASSWORD')
    mail_use_tls = os.getenv('MAIL_USE_TLS', 'true').lower() == 'true'
    
    print_info(f"SMTP Server: {mail_server}:{mail_port}")
    print_info(f"TLS Enabled: {mail_use_tls}")
    
    if not mail_username:
        print_warning("MAIL_USERNAME not set - email notifications disabled")
        return False
    print_success(f"Username: {mail_username}")
    
    if not mail_password:
        print_warning("MAIL_PASSWORD not set - email notifications disabled")
        return False
    print_success(f"Password: {'*' * len(mail_password)}")
    
    # Test SMTP connection
    print_info("Testing SMTP connection...")
    try:
        import smtplib
        from email.mime.text import MIMEText
        
        with smtplib.SMTP(mail_server, int(mail_port)) as server:
            if mail_use_tls:
                server.starttls()
            server.login(mail_username, mail_password)
            print_success("SMTP connection successful!")
            print_info("Run test_send_email() to send a test email")
            return True
    except ImportError:
        print_error("smtplib module not available!")
        return False
    except Exception as e:
        print_error(f"SMTP connection failed: {str(e)}")
        print_info("\nFor Gmail users:")
        print_info("1. Enable 2-factor authentication")
        print_info("2. Generate app-specific password: https://myaccount.google.com/apppasswords")
        print_info("3. Use the app password, not your regular Gmail password")
        return False

def validate_school_info():
    """Validate school information"""
    print_header("Validating School Information")
    
    school_name = os.getenv('SCHOOL_NAME', 'Hillview School')
    school_phone = os.getenv('SCHOOL_PHONE', '+254700000000')
    school_email = os.getenv('SCHOOL_EMAIL', 'info@hillviewschool.com')
    
    print_success(f"School Name: {school_name}")
    print_success(f"School Phone: {school_phone}")
    print_success(f"School Email: {school_email}")
    
    return True

def test_send_sms():
    """Interactive SMS test function"""
    print_header("SMS Test Function")
    
    phone = input(f"{Fore.YELLOW}Enter phone number (format: 254712345678): {Style.RESET_ALL}")
    if not phone:
        print_error("Phone number required!")
        return
    
    message = input(f"{Fore.YELLOW}Enter test message (or press Enter for default): {Style.RESET_ALL}")
    if not message:
        message = "Test SMS from Hillview School Management System. Notifications are working!"
    
    print_info("\nAttempting to send SMS...")
    
    try:
        from utils.notification_service import NotificationService
        
        result = NotificationService.send_sms(
            phone_number=phone,
            message=message
        )
        
        if result:
            print_success("SMS sent successfully!")
        else:
            print_error("SMS sending failed!")
    except Exception as e:
        print_error(f"Error sending SMS: {str(e)}")

def test_send_email():
    """Interactive email test function"""
    print_header("Email Test Function")
    
    email = input(f"{Fore.YELLOW}Enter email address: {Style.RESET_ALL}")
    if not email:
        print_error("Email address required!")
        return
    
    print_info("\nAttempting to send email...")
    
    try:
        from utils.notification_service import NotificationService
        
        # Test data
        test_data = {
            'student_name': 'Test Student',
            'amount': 1000.00,
            'receipt': 'TEST123456',
            'date': '2025-11-04 14:30:00'
        }
        
        result = NotificationService.send_payment_email(
            email=email,
            student_name=test_data['student_name'],
            amount=test_data['amount'],
            mpesa_receipt=test_data['receipt'],
            transaction_date=test_data['date']
        )
        
        if result:
            print_success("Email sent successfully! Check your inbox.")
        else:
            print_error("Email sending failed!")
    except Exception as e:
        print_error(f"Error sending email: {str(e)}")

def main():
    """Main validation function"""
    print(f"{Fore.MAGENTA}{Style.BRIGHT}")
    print("╔═══════════════════════════════════════════════════════════╗")
    print("║   SMS & EMAIL NOTIFICATION CONFIGURATION VALIDATOR        ║")
    print("║   Hillview School Management System                       ║")
    print("╚═══════════════════════════════════════════════════════════╝")
    print(Style.RESET_ALL)
    
    # Step 1: Check .env file
    if not check_env_file():
        print(f"\n{Fore.RED}Please configure your .env file and run this script again.")
        sys.exit(1)
    
    # Step 2: Validate configurations
    sms_ok = validate_sms_provider()
    email_ok = validate_email_config()
    school_ok = validate_school_info()
    
    # Step 3: Summary
    print_header("Validation Summary")
    
    if sms_ok:
        print_success("SMS Configuration: VALID")
    else:
        print_error("SMS Configuration: INVALID")
    
    if email_ok:
        print_success("Email Configuration: VALID")
    else:
        print_error("Email Configuration: INVALID")
    
    if school_ok:
        print_success("School Information: VALID")
    
    # Step 4: Interactive testing
    if sms_ok or email_ok:
        print_header("Interactive Testing")
        print_info("You can now test your configuration:")
        print_info("1. Run: python validate_notifications.py --test-sms")
        print_info("2. Run: python validate_notifications.py --test-email")
        print_info("3. Or import this file and call test_send_sms() or test_send_email()")
        
        # Check for command-line flags
        if len(sys.argv) > 1:
            if sys.argv[1] == '--test-sms':
                test_send_sms()
            elif sys.argv[1] == '--test-email':
                test_send_email()
    
    print(f"\n{Fore.GREEN}{Style.BRIGHT}✓ Validation complete!{Style.RESET_ALL}\n")

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n{Fore.YELLOW}Validation cancelled by user.{Style.RESET_ALL}")
        sys.exit(0)
    except Exception as e:
        print(f"\n{Fore.RED}Unexpected error: {str(e)}{Style.RESET_ALL}")
        sys.exit(1)
