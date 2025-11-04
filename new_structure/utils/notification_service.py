"""
Notification service for M-PESA payments.
Handles SMS and Email notifications for payment confirmations.
"""
import logging
from typing import Optional, Dict, Any
from flask import current_app
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)


class NotificationService:
    """Service for sending payment notifications via SMS and Email."""
    
    @staticmethod
    def send_payment_notification(
        student_name: str,
        amount: float,
        receipt_number: str,
        phone_number: str,
        email: Optional[str] = None,
        parent_phone: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Send payment confirmation via SMS and/or Email.
        
        Args:
            student_name: Name of the student
            amount: Payment amount
            receipt_number: M-PESA receipt number
            phone_number: Student/parent phone number for SMS
            email: Email address for notification (optional)
            parent_phone: Parent phone number if different from student
            
        Returns:
            Dictionary with status of SMS and email sending
        """
        results = {
            'sms_sent': False,
            'email_sent': False,
            'sms_error': None,
            'email_error': None
        }
        
        # Send SMS
        if phone_number or parent_phone:
            try:
                sms_result = NotificationService._send_sms(
                    phone_number=parent_phone or phone_number,
                    student_name=student_name,
                    amount=amount,
                    receipt_number=receipt_number
                )
                results['sms_sent'] = sms_result['success']
                results['sms_error'] = sms_result.get('error')
            except Exception as e:
                logger.error(f"Error sending SMS notification: {e}", exc_info=True)
                results['sms_error'] = str(e)
        
        # Send Email
        if email:
            try:
                email_result = NotificationService._send_email(
                    email=email,
                    student_name=student_name,
                    amount=amount,
                    receipt_number=receipt_number
                )
                results['email_sent'] = email_result['success']
                results['email_error'] = email_result.get('error')
            except Exception as e:
                logger.error(f"Error sending email notification: {e}", exc_info=True)
                results['email_error'] = str(e)
        
        return results
    
    @staticmethod
    def _send_sms(
        phone_number: str,
        student_name: str,
        amount: float,
        receipt_number: str
    ) -> Dict[str, Any]:
        """
        Send SMS using configured SMS gateway.
        
        Supports:
        - Africa's Talking
        - Twilio
        - Custom SMS gateway
        """
        # Format message
        message = (
            f"Payment Received!\n"
            f"Student: {student_name}\n"
            f"Amount: KES {amount:,.2f}\n"
            f"M-PESA Receipt: {receipt_number}\n"
            f"Thank you for your payment."
        )
        
        # Check which SMS provider is configured
        sms_provider = current_app.config.get('SMS_PROVIDER', 'africas_talking')
        
        if sms_provider == 'africas_talking':
            return NotificationService._send_via_africas_talking(phone_number, message)
        elif sms_provider == 'twilio':
            return NotificationService._send_via_twilio(phone_number, message)
        else:
            # Test mode - just log the message
            logger.info(f"TEST MODE - SMS to {phone_number}: {message}")
            return {'success': True, 'message': 'Test mode - SMS logged'}
    
    @staticmethod
    def _send_via_africas_talking(phone_number: str, message: str) -> Dict[str, Any]:
        """Send SMS via Africa's Talking API."""
        try:
            # Check if credentials are configured
            api_key = current_app.config.get('AFRICAS_TALKING_API_KEY')
            username = current_app.config.get('AFRICAS_TALKING_USERNAME')
            
            if not api_key or not username:
                logger.warning("Africa's Talking credentials not configured")
                return {
                    'success': False,
                    'error': 'SMS gateway not configured'
                }
            
            # Import Africa's Talking SDK
            try:
                import africastalking
            except ImportError:
                logger.warning("africastalking package not installed. Install: pip install africastalking")
                return {
                    'success': False,
                    'error': 'SMS SDK not installed'
                }
            
            # Initialize SDK
            africastalking.initialize(username, api_key)
            sms = africastalking.SMS
            
            # Send SMS
            response = sms.send(message, [phone_number])
            
            logger.info(f"SMS sent via Africa's Talking: {response}")
            return {
                'success': True,
                'response': response
            }
            
        except Exception as e:
            logger.error(f"Africa's Talking SMS error: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }
    
    @staticmethod
    def _send_via_twilio(phone_number: str, message: str) -> Dict[str, Any]:
        """Send SMS via Twilio API."""
        try:
            # Check if credentials are configured
            account_sid = current_app.config.get('TWILIO_ACCOUNT_SID')
            auth_token = current_app.config.get('TWILIO_AUTH_TOKEN')
            from_number = current_app.config.get('TWILIO_PHONE_NUMBER')
            
            if not account_sid or not auth_token or not from_number:
                logger.warning("Twilio credentials not configured")
                return {
                    'success': False,
                    'error': 'SMS gateway not configured'
                }
            
            # Import Twilio SDK
            try:
                from twilio.rest import Client
            except ImportError:
                logger.warning("twilio package not installed. Install: pip install twilio")
                return {
                    'success': False,
                    'error': 'SMS SDK not installed'
                }
            
            # Send SMS
            client = Client(account_sid, auth_token)
            message = client.messages.create(
                body=message,
                from_=from_number,
                to=phone_number
            )
            
            logger.info(f"SMS sent via Twilio: {message.sid}")
            return {
                'success': True,
                'message_sid': message.sid
            }
            
        except Exception as e:
            logger.error(f"Twilio SMS error: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }
    
    @staticmethod
    def _send_email(
        email: str,
        student_name: str,
        amount: float,
        receipt_number: str
    ) -> Dict[str, Any]:
        """Send email notification for payment confirmation."""
        try:
            # Check if email is configured
            smtp_server = current_app.config.get('MAIL_SERVER')
            smtp_port = current_app.config.get('MAIL_PORT', 587)
            smtp_username = current_app.config.get('MAIL_USERNAME')
            smtp_password = current_app.config.get('MAIL_PASSWORD')
            from_email = current_app.config.get('MAIL_DEFAULT_SENDER', smtp_username)
            
            if not smtp_server or not smtp_username or not smtp_password:
                logger.warning("Email SMTP settings not configured")
                return {
                    'success': False,
                    'error': 'Email not configured'
                }
            
            # Create email
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f'Payment Confirmation - KES {amount:,.2f}'
            msg['From'] = from_email
            msg['To'] = email
            
            # Plain text version
            text = f"""
Payment Received Successfully

Dear Parent/Guardian,

We have received your payment for {student_name}.

Payment Details:
- Amount: KES {amount:,.2f}
- M-PESA Receipt: {receipt_number}
- Date: {current_app.config.get('CURRENT_DATE', 'Today')}

Thank you for your prompt payment.

Regards,
{current_app.config.get('SCHOOL_NAME', 'Hillview School')}
{current_app.config.get('SCHOOL_PHONE', '')}
"""
            
            # HTML version
            html = f"""
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background-color: #14522f; color: white; padding: 20px; text-align: center; }}
        .content {{ background-color: #f9f9f9; padding: 20px; margin: 20px 0; }}
        .details {{ background-color: white; padding: 15px; margin: 10px 0; border-left: 4px solid #14522f; }}
        .footer {{ text-align: center; color: #666; font-size: 12px; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h2>Payment Confirmation</h2>
        </div>
        <div class="content">
            <p>Dear Parent/Guardian,</p>
            <p>We have successfully received your payment for <strong>{student_name}</strong>.</p>
            
            <div class="details">
                <h3>Payment Details:</h3>
                <p><strong>Amount:</strong> KES {amount:,.2f}</p>
                <p><strong>M-PESA Receipt:</strong> {receipt_number}</p>
                <p><strong>Student:</strong> {student_name}</p>
            </div>
            
            <p>Thank you for your prompt payment.</p>
        </div>
        <div class="footer">
            <p>{current_app.config.get('SCHOOL_NAME', 'Hillview School')}</p>
            <p>{current_app.config.get('SCHOOL_PHONE', '')}</p>
        </div>
    </div>
</body>
</html>
"""
            
            # Attach both versions
            part1 = MIMEText(text, 'plain')
            part2 = MIMEText(html, 'html')
            msg.attach(part1)
            msg.attach(part2)
            
            # Send email
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(smtp_username, smtp_password)
                server.send_message(msg)
            
            logger.info(f"Email sent to {email} for payment {receipt_number}")
            return {
                'success': True,
                'message': 'Email sent successfully'
            }
            
        except Exception as e:
            logger.error(f"Email sending error: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }
