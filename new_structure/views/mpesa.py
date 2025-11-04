"""
M-PESA Integration Routes
Handles M-PESA configuration, STK Push payments, and callbacks.
"""
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required, current_user
from new_structure.extensions import db
from new_structure.models.fee_management import MpesaConfig, MpesaTransaction, Payment
from new_structure.models.academic import Student
from new_structure.utils.mpesa_client import (
    MpesaClient, 
    process_mpesa_callback, 
    get_mpesa_config,
    create_stk_push_transaction
)
from datetime import datetime
import json
import logging

# Initialize logger
logger = logging.getLogger(__name__)

mpesa_bp = Blueprint('mpesa', __name__, url_prefix='/mpesa')


@mpesa_bp.route('/config', methods=['GET'])
@login_required
def config():
    """M-PESA configuration page"""
    # Get existing configuration
    mpesa_config = get_mpesa_config()
    return render_template('mpesa/config.html', config=mpesa_config)


@mpesa_bp.route('/config/save', methods=['POST'])
@login_required
def save_config():
    """Save M-PESA configuration"""
    try:
        # Get existing config or create new
        config = get_mpesa_config()
        if not config:
            config = MpesaConfig()
            db.session.add(config)
        
        # Update configuration
        config.environment = request.form.get('environment', 'sandbox')
        config.consumer_key = request.form.get('consumer_key', '').strip()
        config.consumer_secret = request.form.get('consumer_secret', '').strip()
        config.shortcode = request.form.get('shortcode', '').strip()
        config.passkey = request.form.get('passkey', '').strip()
        config.callback_url = request.form.get('callback_url', '').strip()
        config.is_enabled = request.form.get('is_enabled') == 'on'
        
        db.session.commit()
        
        flash('M-PESA configuration saved successfully!', 'success')
        return redirect(url_for('mpesa.config'))
    
    except Exception as e:
        db.session.rollback()
        flash(f'Error saving configuration: {str(e)}', 'error')
        return redirect(url_for('mpesa.config'))


@mpesa_bp.route('/config/test', methods=['POST'])
@login_required
def test_config():
    """Test M-PESA configuration by generating access token"""
    try:
        config = get_mpesa_config()
        if not config:
            return jsonify({
                'success': False,
                'message': 'M-PESA not configured'
            })
        
        # Initialize client and test token generation
        client = MpesaClient(config)
        token = client.generate_access_token()
        
        if token:
            return jsonify({
                'success': True,
                'message': 'Connection successful! Access token generated.',
                'token_preview': token[:20] + '...'
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Failed to generate access token. Check your credentials.'
            })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error: {str(e)}'
        })


@mpesa_bp.route('/stk-push', methods=['POST'])
@login_required
def stk_push():
    """
    Initiate STK Push payment request.
    Called from record payment page.
    """
    try:
        # Get form data
        student_id = request.form.get('student_id')
        phone_number = request.form.get('phone_number')
        amount = float(request.form.get('amount'))
        
        # Get student for account reference
        student = Student.query.get(student_id)
        if not student:
            return jsonify({
                'success': False,
                'message': 'Student not found'
            })
        
        # Create account reference and description
        account_reference = student.admission_number or f"STU{student.id}"
        transaction_desc = f"Fee payment for {student.name}"
        
        # Create and initiate STK Push transaction
        transaction, response = create_stk_push_transaction(
            phone_number=phone_number,
            amount=amount,
            account_reference=account_reference,
            transaction_desc=transaction_desc,
            student_id=student_id
        )
        
        if response.get('ResponseCode') == '0':
            return jsonify({
                'success': True,
                'message': 'Payment request sent! Please check your phone and enter M-PESA PIN.',
                'transaction_id': transaction.id,
                'checkout_request_id': transaction.checkout_request_id
            })
        else:
            return jsonify({
                'success': False,
                'message': response.get('ResponseDescription') or response.get('errorMessage', 'Payment request failed')
            })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error: {str(e)}'
        })


@mpesa_bp.route('/callback', methods=['POST'])
def callback():
    """
    M-PESA callback endpoint.
    Receives payment notifications from Safaricom.
    This endpoint is called by Safaricom servers, not by users.
    """
    try:
        # Security: Validate callback source (Safaricom IPs)
        # Safaricom callback IPs (update with official list from Safaricom docs)
        SAFARICOM_IPS = [
            '196.201.214.200',  # Safaricom primary callback IP
            '196.201.214.206',  # Safaricom secondary callback IP
            '196.201.213.114',  # Safaricom tertiary callback IP
            '127.0.0.1',        # Localhost for testing (remove in production)
            '::1'               # IPv6 localhost for testing (remove in production)
        ]
        
        # Get client IP (handle proxy headers if behind load balancer)
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        if ',' in client_ip:
            client_ip = client_ip.split(',')[0].strip()
        
        # Validate IP (skip for localhost/testing)
        if client_ip not in SAFARICOM_IPS and client_ip != '127.0.0.1':
            logger.warning(f"Unauthorized M-PESA callback attempt from IP: {client_ip}")
            return jsonify({
                'ResultCode': 1,
                'ResultDesc': 'Unauthorized'
            }), 403
        
        # Get callback data
        callback_data = request.get_json()
        
        # Log callback
        logger.info(f"M-PESA Callback received from {client_ip}")
        logger.debug(f"Callback data: {json.dumps(callback_data, indent=2)}")
        
        # Process callback
        success = process_mpesa_callback(callback_data)
        
        if success:
            # Auto-reconciliation: Create payment record if successful
            body = callback_data.get('Body', {})
            stk_callback = body.get('stkCallback', {})
            checkout_request_id = stk_callback.get('CheckoutRequestID')
            result_code = stk_callback.get('ResultCode')
            
            if result_code == 0:
                # Find transaction
                transaction = MpesaTransaction.query.filter_by(
                    checkout_request_id=checkout_request_id
                ).first()
                
                if transaction and not transaction.payment_id:
                    # Get M-PESA payment method (assuming method_id=2 is M-PESA)
                    # You can query PaymentMethod.query.filter_by(name='M-PESA').first() if needed
                    
                    # Create payment record
                    payment = Payment(
                        student_id=transaction.student_id,
                        amount=transaction.amount,
                        payment_date=transaction.transaction_date or datetime.now(),
                        method_id=2,  # M-PESA payment method ID
                        reference=transaction.mpesa_receipt_number,
                        recorded_by=None,  # System/Auto payment - no specific teacher
                        notes=f"M-PESA payment: {transaction.transaction_desc}",
                        allocation_mode='auto'
                    )
                    
                    db.session.add(payment)
                    db.session.commit()
                    
                    # Link payment to transaction
                    transaction.payment_id = payment.id
                    db.session.commit()
                    
                    logger.info(f"Auto-reconciliation successful: Payment #{payment.id} created for transaction #{transaction.id}, Receipt: {transaction.mpesa_receipt_number}")
                    
                    # Send notifications
                    try:
                        from ..utils.notification_service import NotificationService
                        
                        # Get student details
                        student = Student.query.get(transaction.student_id)
                        if student:
                            # Get parent phone and email if available
                            parent_phone = None
                            parent_email = None
                            if hasattr(student, 'parent') and student.parent:
                                parent_phone = student.parent.phone_number
                                parent_email = student.parent.email
                            
                            # Send notification
                            notification_result = NotificationService.send_payment_notification(
                                student_name=student.name,
                                amount=transaction.amount,
                                receipt_number=transaction.mpesa_receipt_number,
                                phone_number=transaction.phone_number,
                                email=parent_email,
                                parent_phone=parent_phone
                            )
                            
                            if notification_result['sms_sent'] or notification_result['email_sent']:
                                logger.info(f"Payment notification sent for transaction #{transaction.id}: SMS={notification_result['sms_sent']}, Email={notification_result['email_sent']}")
                            else:
                                logger.warning(f"Failed to send notification for transaction #{transaction.id}: SMS Error={notification_result.get('sms_error')}, Email Error={notification_result.get('email_error')}")
                    except Exception as notify_error:
                        logger.error(f"Error sending payment notification: {notify_error}", exc_info=True)
            
            return jsonify({
                'ResultCode': 0,
                'ResultDesc': 'Success'
            })
        else:
            logger.error("Failed to process M-PESA callback")
            return jsonify({
                'ResultCode': 1,
                'ResultDesc': 'Failed to process callback'
            })
    
    except Exception as e:
        logger.error(f"Error in M-PESA callback: {str(e)}", exc_info=True)
        return jsonify({
            'ResultCode': 1,
            'ResultDesc': f'Error: {str(e)}'
        })


@mpesa_bp.route('/transactions', methods=['GET'])
@login_required
def transactions():
    """M-PESA transactions dashboard"""
    # Get all transactions with pagination
    page = request.args.get('page', 1, type=int)
    per_page = 50
    
    # Filters
    status = request.args.get('status')
    student_id = request.args.get('student_id')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    
    # Build query
    query = MpesaTransaction.query
    
    if status:
        query = query.filter_by(status=status)
    
    if student_id:
        query = query.filter_by(student_id=student_id)
    
    if date_from:
        date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
        query = query.filter(MpesaTransaction.created_at >= date_from_obj)
    
    if date_to:
        date_to_obj = datetime.strptime(date_to, '%Y-%m-%d')
        query = query.filter(MpesaTransaction.created_at <= date_to_obj)
    
    # Order by most recent first
    query = query.order_by(MpesaTransaction.created_at.desc())
    
    # Paginate
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    transactions = pagination.items
    
    # Calculate statistics
    stats = {
        'total_transactions': MpesaTransaction.query.count(),
        'successful': MpesaTransaction.query.filter_by(status='success').count(),
        'pending': MpesaTransaction.query.filter_by(status='pending').count(),
        'failed': MpesaTransaction.query.filter_by(status='failed').count(),
        'total_amount': db.session.query(db.func.sum(MpesaTransaction.amount)).filter_by(status='success').scalar() or 0
    }
    
    return render_template('mpesa/transactions.html', 
                         transactions=transactions, 
                         pagination=pagination,
                         stats=stats)


@mpesa_bp.route('/transaction/<int:transaction_id>', methods=['GET'])
@login_required
def transaction_detail(transaction_id):
    """View detailed transaction information"""
    transaction = MpesaTransaction.query.get_or_404(transaction_id)
    
    # Parse callback data if available
    callback_data = None
    if transaction.callback_data:
        try:
            callback_data = json.loads(transaction.callback_data)
        except:
            pass
    
    return render_template('mpesa/transaction_detail.html', 
                         transaction=transaction,
                         callback_data=callback_data)


@mpesa_bp.route('/transaction/<int:transaction_id>/check-status', methods=['POST'])
@login_required
def check_transaction_status(transaction_id):
    """
    Manually check transaction status.
    Useful when callback is delayed or not received.
    """
    try:
        transaction = MpesaTransaction.query.get_or_404(transaction_id)
        
        if not transaction.checkout_request_id:
            return jsonify({
                'success': False,
                'message': 'No checkout request ID available'
            })
        
        # Get config
        config = get_mpesa_config()
        if not config:
            return jsonify({
                'success': False,
                'message': 'M-PESA not configured'
            })
        
        # Initialize client and query status
        client = MpesaClient(config)
        response = client.query_stk_push_status(transaction.checkout_request_id)
        
        # Update transaction based on response
        if response.get('ResultCode') == '0':
            transaction.status = 'success'
            transaction.result_code = response.get('ResultCode')
            transaction.result_desc = response.get('ResultDesc')
            db.session.commit()
            
            return jsonify({
                'success': True,
                'message': 'Transaction successful!',
                'status': 'success',
                'result_desc': response.get('ResultDesc')
            })
        elif response.get('ResultCode'):
            transaction.status = 'failed'
            transaction.result_code = response.get('ResultCode')
            transaction.result_desc = response.get('ResultDesc')
            db.session.commit()
            
            return jsonify({
                'success': False,
                'message': response.get('ResultDesc', 'Transaction failed'),
                'status': 'failed'
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Unable to query transaction status',
                'response': response
            })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error: {str(e)}'
        })


@mpesa_bp.route('/api/stk-push', methods=['POST'])
def api_stk_push():
    """
    API endpoint for STK Push (can be used by external integrations).
    Requires API key authentication (not implemented yet).
    """
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['phone_number', 'amount', 'account_reference', 'transaction_desc']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'success': False,
                    'message': f'Missing required field: {field}'
                }), 400
        
        # Create and initiate STK Push
        transaction, response = create_stk_push_transaction(
            phone_number=data['phone_number'],
            amount=float(data['amount']),
            account_reference=data['account_reference'],
            transaction_desc=data['transaction_desc'],
            student_id=data.get('student_id')
        )
        
        if response.get('ResponseCode') == '0':
            return jsonify({
                'success': True,
                'message': 'Payment request sent',
                'transaction': transaction.to_dict()
            })
        else:
            return jsonify({
                'success': False,
                'message': response.get('ResponseDescription', 'Payment request failed'),
                'transaction': transaction.to_dict() if transaction else None
            }), 400
    
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error: {str(e)}'
        }), 500


@mpesa_bp.route('/handle-timeouts', methods=['GET', 'POST'])
@login_required
def handle_timeouts():
    """
    Manually trigger timeout handling for pending transactions.
    Marks transactions as timeout if no callback received after 2 minutes.
    """
    try:
        from datetime import timedelta
        
        # Get timeout threshold (default: 2 minutes)
        timeout_minutes = request.json.get('timeout_minutes', 2) if request.is_json else 2
        timeout_threshold = datetime.utcnow() - timedelta(minutes=timeout_minutes)
        
        # Find pending transactions older than threshold
        timed_out_transactions = MpesaTransaction.query.filter(
            MpesaTransaction.status == 'pending',
            MpesaTransaction.created_at < timeout_threshold,
            MpesaTransaction.callback_received == False
        ).all()
        
        if not timed_out_transactions:
            return jsonify({
                'success': True,
                'message': 'No timed out transactions found',
                'count': 0
            })
        
        # Mark as timeout
        count = 0
        transaction_ids = []
        for transaction in timed_out_transactions:
            transaction.status = 'timeout'
            transaction.result_desc = 'Transaction timed out - no callback received'
            transaction.updated_at = datetime.utcnow()
            transaction_ids.append(transaction.id)
            count += 1
            logger.warning(f"Transaction #{transaction.id} marked as timeout (created: {transaction.created_at})")
        
        db.session.commit()
        logger.info(f"Marked {count} transactions as timed out")
        
        return jsonify({
            'success': True,
            'message': f'Marked {count} transactions as timed out',
            'count': count,
            'transaction_ids': transaction_ids
        })
        
    except Exception as e:
        logger.error(f"Error handling transaction timeouts: {str(e)}", exc_info=True)
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'Error: {str(e)}'
        }), 500


@mpesa_bp.route('/status/<int:transaction_id>')
@login_required
def payment_status_page(transaction_id):
    """
    Display payment status page with real-time updates.
    Shows transaction details and polls for status changes.
    """
    try:
        transaction = MpesaTransaction.query.get(transaction_id)
        
        if not transaction:
            flash('Transaction not found', 'error')
            return redirect(url_for('fees.index'))
        
        return render_template('mpesa_status.html',
            transaction_id=transaction.id,
            amount=transaction.amount,
            phone_number=transaction.phone_number,
            status=transaction.status
        )
        
    except Exception as e:
        logger.error(f"Error displaying status page: {str(e)}", exc_info=True)
        flash(f'Error: {str(e)}', 'error')
        return redirect(url_for('fees.index'))


@mpesa_bp.route('/transaction-status/<int:transaction_id>', methods=['GET'])
@login_required
def transaction_status(transaction_id):
    """
    Get real-time status of an M-PESA transaction.
    Used for polling transaction status without page refresh.
    """
    try:
        from ..utils.mpesa_status_checker import get_transaction_status
        
        result = get_transaction_status(transaction_id)
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error checking transaction status: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@mpesa_bp.route('/analytics')
@login_required
def analytics_dashboard():
    """
    M-PESA Analytics Dashboard.
    Shows comprehensive statistics and trends for M-PESA payments.
    """
    try:
        from ..utils.mpesa_analytics import MpesaAnalytics
        from datetime import datetime
        
        # Get period from query params (default: 30 days)
        days = int(request.args.get('days', 30))
        
        # Get all analytics data
        stats = MpesaAnalytics.get_dashboard_stats(days=days)
        daily_trends = MpesaAnalytics.get_daily_trends(days=days)
        hourly_distribution = MpesaAnalytics.get_hourly_distribution()
        top_students = MpesaAnalytics.get_top_paying_students(limit=10, days=days)
        recent_transactions = MpesaAnalytics.get_recent_transactions(limit=15)
        failure_analysis = MpesaAnalytics.get_failure_analysis(days=days)
        
        return render_template('mpesa_analytics.html',
            stats=stats,
            daily_trends=daily_trends,
            hourly_distribution=hourly_distribution,
            top_students=top_students,
            recent_transactions=recent_transactions,
            failure_analysis=failure_analysis,
            selected_days=days,
            today_date=datetime.now()
        )
        
    except Exception as e:
        logger.error(f"Error loading analytics dashboard: {str(e)}", exc_info=True)
        flash(f'Error loading analytics: {str(e)}', 'error')
        return redirect(url_for('mpesa.transactions'))


@mpesa_bp.route('/test-notification', methods=['GET', 'POST'])
@login_required
def test_notification():
    """
    Test SMS/Email notification system.
    Send a test notification to verify configuration.
    """
    try:
        from ..utils.notification_service import NotificationService
        
        # Get parameters from request
        if request.method == 'POST':
            phone_number = request.form.get('phone_number')
            email = request.form.get('email')
        else:
            phone_number = request.args.get('phone_number')
            email = request.args.get('email')
        
        if not phone_number and not email:
            return jsonify({
                'success': False,
                'message': 'Please provide phone_number or email parameter'
            }), 400
        
        # Send test notification
        result = NotificationService.send_payment_notification(
            student_name="Test Student",
            amount=100.00,
            receipt_number="TEST123456",
            phone_number=phone_number,
            email=email
        )
        
        return jsonify({
            'success': True,
            'sms_sent': result['sms_sent'],
            'email_sent': result['email_sent'],
            'sms_error': result.get('sms_error'),
            'email_error': result.get('email_error'),
            'message': 'Test notification sent'
        })
        
    except Exception as e:
        logger.error(f"Error testing notifications: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'message': f'Error: {str(e)}'
        }), 500
