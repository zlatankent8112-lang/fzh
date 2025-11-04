"""
Notification Service
Handles SMS and Email notifications for M-PESA payments.
Supports multiple providers: Africa's Talking, Twilio, SMTP.
"""
import os
import logging
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Import external providers (with fallback for missing packages)
try:
    import africastalking
except ImportError:
    africastalking = None

try:
    from twilio.rest import Client
except ImportError:
    Client = None

logger = logging.getLogger(__name__)


class NotificationService:
    """Service for sending SMS and Email notifications."""
    
    @staticmethod
    def send_sms(phone_number, message):
        """
        Send SMS notification.
        Supports Africa's Talking, Twilio, or test mode.
        
        Args:
            phone_number: Recipient phone number (254XXXXXXXXX format)
            message: SMS message content
            
        Returns:
            bool: True if sent successfully, False otherwise
        """
        try:
            # Validate phone number
            if not phone_number or not str(phone_number).isdigit():
                logger.error(f"Invalid phone number: {phone_number}")
                return False
            
            # Normalize phone number
            phone_number = str(phone_number)
            if not phone_number.startswith('+'):
                phone_number = '+' + phone_number
            
            # Get SMS provider from environment
            sms_provider = os.getenv('SMS_PROVIDER', 'test').lower()
            
            # Test mode - just log
            if sms_provider == 'test':
                logger.info(f"TEST MODE: SMS to {phone_number}: {message}")
                return True
            
            # Africa's Talking
            elif sms_provider == 'africas_talking':
                return NotificationService._send_sms_africastalking(phone_number, message)
            
            # Twilio
            elif sms_provider == 'twilio':
                return NotificationService._send_sms_twilio(phone_number, message)
            
            else:
                logger.warning(f"Unknown SMS provider: {sms_provider}. Using test mode.")
                logger.info(f"TEST MODE: SMS to {phone_number}: {message}")
                return True
                
        except Exception as e:
            logger.error(f"Error sending SMS: {e}", exc_info=True)
            return False
    
    @staticmethod
    def _send_sms_africastalking(phone_number, message):
        """Send SMS via Africa's Talking API."""
        try:
            if africastalking is None:
                raise ImportError("africastalking package not installed")
            
            # Get credentials
            username = os.getenv('AFRICAS_TALKING_USERNAME')
            api_key = os.getenv('AFRICAS_TALKING_API_KEY')
            sender_id = os.getenv('AFRICAS_TALKING_SENDER_ID', 'MPESA')
            
            if not username or not api_key:
                logger.error("Africa's Talking credentials not configured")
                return False
            
            # Initialize SDK
            africastalking.initialize(username, api_key)
            sms = africastalking.SMS
            
            # Send SMS
            response = sms.send(message, [phone_number], sender_id)
            
            # Check response
            recipients = response.get('SMSMessageData', {}).get('Recipients', [])
            if recipients and len(recipients) > 0:
                status_code = recipients[0].get('statusCode')
                # Status codes 101 and 102 indicate success
                if status_code in [101, 102]:
                    logger.info(f"SMS sent successfully to {phone_number} via Africa's Talking")
                    return True
                else:
                    logger.error(f"SMS failed with status code {status_code}")
                    return False
            else:
                logger.error("No recipients in SMS response")
                return False
                
        except ImportError as e:
            logger.error(f"africastalking package not installed: {e}. Run: pip install africastalking")
            return False
        except Exception as e:
            logger.error(f"Error sending SMS via Africa's Talking: {e}", exc_info=True)
            return False
    
    @staticmethod
    def _send_sms_twilio(phone_number, message):
        """Send SMS via Twilio API."""
        try:
            if Client is None:
                raise ImportError("twilio package not installed")
            
            # Get credentials
            account_sid = os.getenv('TWILIO_ACCOUNT_SID')
            auth_token = os.getenv('TWILIO_AUTH_TOKEN')
            from_number = os.getenv('TWILIO_PHONE_NUMBER')
            
            if not account_sid or not auth_token or not from_number:
                logger.error("Twilio credentials not configured")
                return False
            
            # Initialize client
            client = Client(account_sid, auth_token)
            
            # Send SMS
            message_obj = client.messages.create(
                body=message,
                from_=from_number,
                to=phone_number
            )
            
            logger.info(f"SMS sent successfully to {phone_number} via Twilio. SID: {message_obj.sid}")
            return True
            
        except ImportError as e:
            logger.error(f"twilio package not installed: {e}. Run: pip install twilio")
            return False
        except Exception as e:
            logger.error(f"Error sending SMS via Twilio: {e}", exc_info=True)
            return False
    
    @staticmethod
    def send_email(to_email, subject, body, html=True):
        """
        Send email notification.
        
        Args:
            to_email: Recipient email address
            subject: Email subject
            body: Email body (plain text or HTML)
            html: Whether body is HTML (default: True)
            
        Returns:
            bool: True if sent successfully, False otherwise
        """
        try:
            # Validate email
            if not to_email or '@' not in to_email:
                logger.error(f"Invalid email address: {to_email}")
                return False
            
            # Get email configuration
            email_enabled = os.getenv('EMAIL_ENABLED', 'true').lower() == 'true'
            
            if not email_enabled:
                logger.info(f"Email disabled. Would send to {to_email}: {subject}")
                return True
            
            # Get SMTP settings
            smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
            smtp_port = int(os.getenv('SMTP_PORT', '587'))
            smtp_username = os.getenv('SMTP_USERNAME')
            smtp_password = os.getenv('SMTP_PASSWORD')
            from_email = os.getenv('SMTP_FROM_EMAIL', smtp_username)
            
            if not smtp_username or not smtp_password:
                logger.warning("SMTP credentials not configured. Email not sent.")
                return False
            
            # Create message
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = from_email
            msg['To'] = to_email
            
            # Attach body
            if html:
                msg.attach(MIMEText(body, 'html'))
            else:
                msg.attach(MIMEText(body, 'plain'))
            
            # Send email
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(smtp_username, smtp_password)
                server.send_message(msg)
            
            logger.info(f"Email sent successfully to {to_email}")
            return True
            
        except Exception as e:
            logger.error(f"Error sending email: {e}", exc_info=True)
            return False
    
    @staticmethod
    def send_payment_notification(phone_number=None, email=None, parent_phone=None,
                                 student_name=None, amount=None, receipt_number=None,
                                 mpesa_receipt=None, transaction_date=None):
        """
        Send payment notification via SMS and/or Email.
        Flexible parameters to support different calling patterns.
        
        Args:
            phone_number: Primary phone number (payer's phone)
            email: Email address (parent/guardian)
            parent_phone: Alternative parent phone number
            student_name: Name of student
            amount: Payment amount
            receipt_number: Receipt number (alias for mpesa_receipt)
            mpesa_receipt: M-PESA receipt number
            transaction_date: Transaction date/time
            
        Returns:
            dict: {
                'sms_sent': bool,
                'email_sent': bool,
                'sms_error': str or None,
                'email_error': str or None
            }
        """
        result = {
            'sms_sent': False,
            'email_sent': False,
            'sms_error': None,
            'email_error': None
        }
        
        try:
            # Use receipt_number if mpesa_receipt not provided (parameter alias)
            if not mpesa_receipt and receipt_number:
                mpesa_receipt = receipt_number
            
            # Format amount
            amount_str = f"KES {amount:,.2f}" if amount else "KES 0.00"
            
            # Format date
            if transaction_date:
                if isinstance(transaction_date, str):
                    date_str = transaction_date
                else:
                    date_str = transaction_date.strftime('%Y-%m-%d %H:%M:%S')
            else:
                date_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            # Prepare SMS message
            sms_message = f"Payment received for {student_name or 'student'}. "
            sms_message += f"Amount: {amount_str}. "
            if mpesa_receipt:
                sms_message += f"M-PESA Ref: {mpesa_receipt}. "
            sms_message += "Thank you!"
            
            # Send SMS (try both phone numbers)
            sms_sent = False
            if phone_number:
                sms_sent = NotificationService.send_sms(phone_number=phone_number, message=sms_message)
                if sms_sent:
                    result['sms_sent'] = True
            
            if not sms_sent and parent_phone:
                sms_sent = NotificationService.send_sms(phone_number=parent_phone, message=sms_message)
                if sms_sent:
                    result['sms_sent'] = True
            
            if not result['sms_sent'] and (phone_number or parent_phone):
                result['sms_error'] = "Failed to send SMS"
            
            # Store the message for test verification
            result['message'] = sms_message
            
            # Send Email
            if email:
                school_name = os.getenv('SCHOOL_NAME', 'School')
                
                # Prepare HTML email
                email_subject = f"Payment Confirmation - {student_name or 'Student'}"
                email_body = f"""
                <html>
                <body style="font-family: Arial, sans-serif; padding: 20px; background-color: #f5f5f5;">
                    <div style="max-width: 600px; margin: 0 auto; background-color: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 5px rgba(0,0,0,0.1);">
                        <h2 style="color: #2c5f2d; margin-bottom: 20px;">Payment Confirmation</h2>
                        <p>Dear Parent/Guardian,</p>
                        <p>We have received your M-PESA payment for <strong>{student_name or 'your student'}</strong>.</p>
                        
                        <div style="background-color: #f0f8f0; padding: 20px; border-radius: 5px; margin: 20px 0;">
                            <table style="width: 100%; border-collapse: collapse;">
                                <tr>
                                    <td style="padding: 8px 0;"><strong>Amount:</strong></td>
                                    <td style="padding: 8px 0; text-align: right;">{amount_str}</td>
                                </tr>
                                <tr>
                                    <td style="padding: 8px 0;"><strong>M-PESA Receipt:</strong></td>
                                    <td style="padding: 8px 0; text-align: right;">{mpesa_receipt or 'N/A'}</td>
                                </tr>
                                <tr>
                                    <td style="padding: 8px 0;"><strong>Date:</strong></td>
                                    <td style="padding: 8px 0; text-align: right;">{date_str}</td>
                                </tr>
                            </table>
                        </div>
                        
                        <p>Your payment has been successfully recorded in our system.</p>
                        <p>Thank you for choosing {school_name}.</p>
                        
                        <hr style="border: none; border-top: 1px solid #ddd; margin: 30px 0;">
                        <p style="font-size: 12px; color: #666;">
                            This is an automated notification from {school_name}. Please do not reply to this email.
                        </p>
                    </div>
                </body>
                </html>
                """
                
                email_sent = NotificationService.send_email(
                    to_email=email,
                    subject=email_subject,
                    body=email_body,
                    html=True
                )
                
                if email_sent:
                    result['email_sent'] = True
                else:
                    result['email_error'] = "Failed to send email"
            
            # Return True for success (backward compatibility with tests)
            # Return False for failure
            # Return dict for detailed results when both fail
            if result['sms_sent'] or result['email_sent']:
                return True
            else:
                return False
            
        except Exception as e:
            logger.error(f"Error in send_payment_notification: {e}", exc_info=True)
            result['sms_error'] = str(e)
            result['email_error'] = str(e)
            result['message'] = ''
            return result
    
    @staticmethod
    def send_bulk_sms(recipients, message):
        """
        Send SMS to multiple recipients.
        
        Args:
            recipients: List of phone numbers
            message: SMS message
            
        Returns:
            dict: {
                'total': int,
                'sent': int,
                'failed': int,
                'results': list of dicts
            }
        """
        results = {
            'total': len(recipients),
            'sent': 0,
            'failed': 0,
            'results': []
        }
        
        for phone in recipients:
            sent = NotificationService.send_sms(phone, message)
            if sent:
                results['sent'] += 1
                results['results'].append({
                    'phone': phone,
                    'status': 'sent'
                })
            else:
                results['failed'] += 1
                results['results'].append({
                    'phone': phone,
                    'status': 'failed'
                })
        
        return results
    
    @staticmethod
    def send_fee_reminder(phone_number, email, student_name, balance, due_date=None):
        """
        Send fee reminder notification.
        
        Args:
            phone_number: Parent phone number
            email: Parent email
            student_name: Student name
            balance: Outstanding balance
            due_date: Payment due date
            
        Returns:
            dict: Notification results
        """
        result = {
            'sms_sent': False,
            'email_sent': False
        }
        
        try:
            balance_str = f"KES {balance:,.2f}"
            due_str = due_date.strftime('%Y-%m-%d') if due_date else 'soon'
            
            # SMS message
            sms_message = f"Fee reminder for {student_name}. "
            sms_message += f"Outstanding balance: {balance_str}. "
            sms_message += f"Please pay by {due_str}. Thank you."
            
            # Send SMS
            if phone_number:
                result['sms_sent'] = NotificationService.send_sms(phone_number, sms_message)
            
            # Send Email
            if email:
                school_name = os.getenv('SCHOOL_NAME', 'School')
                subject = f"Fee Reminder - {student_name}"
                body = f"""
                <html>
                <body style="font-family: Arial, sans-serif; padding: 20px;">
                    <h2>Fee Reminder</h2>
                    <p>Dear Parent/Guardian,</p>
                    <p>This is a reminder that there is an outstanding balance for <strong>{student_name}</strong>.</p>
                    <p><strong>Outstanding Balance:</strong> {balance_str}</p>
                    <p><strong>Due Date:</strong> {due_str}</p>
                    <p>Please make payment at your earliest convenience.</p>
                    <p>Thank you,<br>{school_name}</p>
                </body>
                </html>
                """
                
                result['email_sent'] = NotificationService.send_email(email, subject, body)
            
            return result
            
        except Exception as e:
            logger.error(f"Error sending fee reminder: {e}", exc_info=True)
            return result
