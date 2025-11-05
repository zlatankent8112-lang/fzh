"""
M-PESA Integration Routes
Handles M-PESA configuration, STK Push payments, and callbacks.
"""
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, current_app
from flask_login import login_required, current_user
from new_structure.extensions import db, limiter
from new_structure.models.fee_management import MpesaConfig, MpesaTransaction, Payment
from new_structure.models.academic import Student
from new_structure.utils.mpesa_client import (
    MpesaClient, 
    process_mpesa_callback, 
    get_mpesa_config,
    create_stk_push_transaction
)
from datetime import datetime
import requests  # Exposed for tests that patch views.mpesa.requests
import json
import logging

# Initialize logger
logger = logging.getLogger(__name__)

mpesa_bp = Blueprint('mpesa', __name__, url_prefix='/mpesa')


def maybe_login_required(f):
    """Apply login_required - in testing mode, check for explicit bypass flag per-test."""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            # Only bypass if EXPLICITLY requested via BYPASS_AUTH_FOR_TEST flag
            if current_app.config.get('BYPASS_AUTH_FOR_TEST'):
                return f(*args, **kwargs)
        except Exception:
            pass
        # Require login by default (even in testing, unless explicitly bypassed)
        from flask_login import current_user
        if not current_user.is_authenticated:
            return redirect(url_for('auth.teacher_login'))
        return f(*args, **kwargs)
    return decorated_function


def _stk_push_rate_limit():
    """Return rate limit string or disable based on config."""
    if current_app.config.get('BYPASS_RATE_LIMIT_FOR_TEST'):
        return "1000 per minute"
    return "10 per minute"

@mpesa_bp.route('/config', methods=['GET'])
@maybe_login_required
def config():
    """M-PESA configuration page"""
    # Get existing configuration
    mpesa_config = get_mpesa_config()
    return render_template('mpesa/config.html', config=mpesa_config)


@mpesa_bp.route('/config/save', methods=['POST'])
@maybe_login_required
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
@maybe_login_required
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


def normalize_phone_number(phone):
    """
    Normalize phone number to 254XXXXXXXXX format.
    Accepts: 0712345678, 712345678, 254712345678, +254712345678
    """
    if not phone:
        return None
    
    # Remove spaces, dashes, and plus sign
    phone = str(phone).replace(' ', '').replace('-', '').replace('+', '')
    
    # Remove leading zeros and add 254
    if phone.startswith('0'):
        phone = '254' + phone[1:]
    elif not phone.startswith('254'):
        phone = '254' + phone
    
    return phone


def validate_phone_number(phone):
    """Validate Kenyan phone number format"""
    if not phone:
        return False
    
    # Normalize first
    normalized = normalize_phone_number(phone)
    
    # Must be 12 digits starting with 254
    if not normalized or len(normalized) != 12:
        return False
    
    if not normalized.startswith('254'):
        return False
    
    # Must be all digits
    if not normalized.isdigit():
        return False
    
    return True


def validate_amount(amount):
    """Validate payment amount"""
    try:
        amount_float = float(amount)
        # M-PESA minimum is 1 KES
        if amount_float < 1:
            return False, "Amount must be at least 1 KES"
        # Maximum per transaction (M-PESA limit)
        if amount_float > 150000:
            return False, "Amount exceeds M-PESA limit of 150,000 KES"
        return True, None
    except (ValueError, TypeError):
        return False, "Invalid amount format"


@mpesa_bp.route('/stk-push', methods=['POST'])
@limiter.limit(_stk_push_rate_limit)
@maybe_login_required
def stk_push():
    """
    Initiate STK Push payment request.
    Accepts both JSON and form data.
    """
    try:
        # Get data from JSON or form
        if request.is_json:
            data = request.get_json()
            phone_number = data.get('phone_number')
            amount = data.get('amount')
            account_reference = data.get('account_reference')
            transaction_desc = data.get('transaction_desc', 'Fee payment')
            student_id = data.get('student_id')
        else:
            phone_number = request.form.get('phone_number')
            amount = request.form.get('amount')
            account_reference = request.form.get('account_reference')
            transaction_desc = request.form.get('transaction_desc', 'Fee payment')
            student_id = request.form.get('student_id')
        
        # Validate required fields
        if not phone_number or not amount or not account_reference:
            return jsonify({
                'success': False,
                'message': 'Missing required fields: phone_number, amount, account_reference'
            }), 400
        
        # Validate phone number
        if not validate_phone_number(phone_number):
            return jsonify({
                'success': False,
                'message': 'Invalid phone number format. Use format: 0712345678 or 254712345678'
            }), 400
        
        # Normalize phone number
        phone_number = normalize_phone_number(phone_number)

        # Mask phone for logging (avoid exposing full number)
        masked_phone = f"{phone_number[:6]}***{phone_number[-2:]}" if phone_number and len(phone_number) >= 8 else 'unknown'
        
        # Validate amount
        amount_valid, amount_error = validate_amount(amount)
        if not amount_valid:
            return jsonify({
                'success': False,
                'message': amount_error
            }), 400
        
        # Convert amount to float
        amount = float(amount)
        
        # Sanitize account_reference (prevent SQL injection, XSS)
        account_reference = str(account_reference).strip()[:50]  # Limit length
        
        # Sanitize transaction description
        transaction_desc = str(transaction_desc).strip()[:100]  # Limit length
        
        # If student_id provided but no account_reference, get from student
        if student_id and not account_reference:
            student = Student.query.get(student_id)
            if not student:
                return jsonify({
                    'success': False,
                    'message': 'Student not found'
                }), 404
            
            account_reference = student.admission_number or f"STU{student.id}"
            if not transaction_desc or transaction_desc == 'Fee payment':
                transaction_desc = f"Fee payment for {student.name}"
        
        logger.info(
            "Initiating STK Push for %s amount KES %.2f reference %s",
            masked_phone,
            amount,
            account_reference
        )

        # Create and initiate STK Push transaction
        transaction, response = create_stk_push_transaction(
            phone_number=phone_number,
            amount=amount,
            account_reference=account_reference,
            transaction_desc=transaction_desc,
            student_id=student_id,
            requests_module=requests
        )
        
        if response.get('ResponseCode') == '0':
            logger.info(
                "STK Push queued successfully for %s (CheckoutRequestID=%s)",
                masked_phone,
                response.get('CheckoutRequestID')
            )
            return jsonify({
                'success': True,
                'message': 'Payment request sent! Please check your phone and enter M-PESA PIN.',
                'transaction_id': transaction.id,
                'checkout_request_id': transaction.checkout_request_id
            })
        else:
            logger.warning(
                "STK Push request failed for %s: %s",
                masked_phone,
                response.get('ResponseDescription') or response.get('errorMessage')
            )
            return jsonify({
                'success': False,
                'message': response.get('ResponseDescription') or response.get('errorMessage', 'Payment request failed')
            }), 400
    
    except ValueError as e:
        return jsonify({
            'success': False,
            'message': f'Invalid amount: {str(e)}'
        }), 400
    except Exception as e:
        logger.error(f"Error initiating STK Push: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'message': f'Error: {str(e)}'
        }), 500


@mpesa_bp.route('/callback', methods=['POST'])
def callback():
    """
    M-PESA callback endpoint.
    Receives payment notifications from Safaricom.
    This endpoint is called by Safaricom servers, not by users.
    """
    try:
        # Security: Validate callback source (Safaricom IPs)
        # Official Safaricom callback IP addresses
        SAFARICOM_IPS = [
            '196.201.214.200',
            '196.201.214.206',
            '196.201.213.114',
            '196.201.214.207',
            '196.201.214.208',
        ]
        
        # Prefer REMOTE_ADDR for validation to avoid trusting spoofable headers
        remote_ip = request.remote_addr or ''
        forwarded_header = request.headers.get('X-Forwarded-For', '')
        forwarded_ip = forwarded_header.split(',')[0].strip() if forwarded_header else ''

        # Allow tests to opt-in to forwarded header usage when validation bypassed
        client_ip = remote_ip
        if current_app.config.get('BYPASS_IP_VALIDATION_FOR_TEST') and forwarded_ip:
            client_ip = forwarded_ip
        
        # Validate IP - must be from Safaricom (bypass only if explicitly requested)
        if not current_app.config.get('BYPASS_IP_VALIDATION_FOR_TEST'):
            if client_ip not in SAFARICOM_IPS:
                logger.warning(f"Unauthorized M-PESA callback attempt from IP: {client_ip}")
                return jsonify({
                    'ResultCode': 1,
                    'ResultDesc': 'Unauthorized'
                }), 403
        
        # Get callback data
        callback_data = request.get_json()
        
        # Log callback
        if forwarded_ip and forwarded_ip != remote_ip:
            logger.info(f"M-PESA Callback received from {client_ip} (forwarded header: {forwarded_ip})")
        else:
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
            try:
                result_code_int = int(result_code) if result_code is not None else None
            except (ValueError, TypeError):
                result_code_int = None
            
            if result_code_int == 0:
                # Find transaction
                transaction = MpesaTransaction.query.filter_by(
                    checkout_request_id=checkout_request_id
                ).first()
                
                if transaction and not transaction.payment_id:
                    # Get M-PESA payment method (assuming method_id=2 is M-PESA)
                    # You can query PaymentMethod.query.filter_by(name='M-PESA').first() if needed
                    
                    # Only auto-create payment if we have a student_id
                    if transaction.student_id:
                        try:
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
                        except Exception as pay_err:
                            logger.error(f"Auto-reconciliation skipped due to error: {pay_err}", exc_info=True)
                            db.session.rollback()
                    else:
                        logger.info("Skipping auto-reconciliation: transaction has no linked student_id")
                    
                    # Send notifications
                    try:
                        from new_structure.utils.notification_service import NotificationService
                        
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
@maybe_login_required
def transactions():
    """
    M-PESA transactions dashboard.
    Returns HTML template for browser, JSON for API requests.
    """
    # Get all transactions with pagination
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    
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
        try:
            date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
            query = query.filter(MpesaTransaction.created_at >= date_from_obj)
        except ValueError:
            pass
    
    if date_to:
        try:
            date_to_obj = datetime.strptime(date_to, '%Y-%m-%d')
            query = query.filter(MpesaTransaction.created_at <= date_to_obj)
        except ValueError:
            pass
    
    # Order by most recent first
    query = query.order_by(MpesaTransaction.created_at.desc())
    
    # Paginate
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    transactions_list = pagination.items
    
    # Calculate statistics
    stats = {
        'total_transactions': MpesaTransaction.query.count(),
        'successful': MpesaTransaction.query.filter_by(status='success').count(),
        'pending': MpesaTransaction.query.filter_by(status='pending').count(),
        'failed': MpesaTransaction.query.filter_by(status='failed').count(),
        'total_amount': float(db.session.query(db.func.sum(MpesaTransaction.amount)).filter_by(status='success').scalar() or 0)
    }
    
    # Return JSON for API requests (Accept: application/json header or ?format=json)
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify({
            'success': True,
            'transactions': [
                {
                    'id': t.id,
                    'merchant_request_id': t.merchant_request_id,
                    'checkout_request_id': t.checkout_request_id,
                    'status': t.status,
                    'amount': float(t.amount) if t.amount else None,
                    'phone_number': t.phone_number,
                    'mpesa_receipt_number': t.mpesa_receipt_number,
                    'result_code': t.result_code,
                    'result_desc': t.result_desc,
                    'transaction_date': t.transaction_date.isoformat() if t.transaction_date else None,
                    'created_at': t.created_at.isoformat() if t.created_at else None
                }
                for t in transactions_list
            ],
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': pagination.total,
                'pages': pagination.pages,
                'has_next': pagination.has_next,
                'has_prev': pagination.has_prev
            },
            'stats': stats
        })
    
    # Return HTML template for browser requests
    return render_template('mpesa/transactions.html', 
                         transactions=transactions_list, 
                         pagination=pagination,
                         stats=stats)


@mpesa_bp.route('/transaction/<int:transaction_id>', methods=['GET'])
@maybe_login_required
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
@maybe_login_required
def check_transaction_status(transaction_id):
    """
    Manually check transaction status (POST method).
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


@mpesa_bp.route('/query-status/<int:transaction_id>', methods=['GET'])
@maybe_login_required
def query_status(transaction_id):
    """
    Query M-PESA transaction status (GET method).
    Returns current status of transaction from database.
    For API/test usage.
    """
    try:
        transaction = MpesaTransaction.query.get(transaction_id)
        
        if not transaction:
            return jsonify({
                'success': False,
                'message': 'Transaction not found'
            }), 404
        
        # Return transaction details
        return jsonify({
            'success': True,
            'transaction': {
                'id': transaction.id,
                'merchant_request_id': transaction.merchant_request_id,
                'checkout_request_id': transaction.checkout_request_id,
                'status': transaction.status,
                'amount': float(transaction.amount) if transaction.amount else None,
                'phone_number': transaction.phone_number,
                'mpesa_receipt_number': transaction.mpesa_receipt_number,
                'result_code': transaction.result_code,
                'result_desc': transaction.result_desc,
                'transaction_date': transaction.transaction_date.isoformat() if transaction.transaction_date else None,
                'created_at': transaction.created_at.isoformat() if transaction.created_at else None,
                'updated_at': transaction.updated_at.isoformat() if transaction.updated_at else None
            }
        })
    
    except Exception as e:
        logger.error(f"Error querying transaction status: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'message': f'Error: {str(e)}'
        }), 500


@mpesa_bp.route('/api/stk-push', methods=['POST'])
@limiter.limit(_stk_push_rate_limit)
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
            student_id=data.get('student_id'),
            requests_module=requests
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
@maybe_login_required
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
@maybe_login_required
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
@maybe_login_required
def transaction_status(transaction_id):
    """
    Get real-time status of an M-PESA transaction.
    Used for polling transaction status without page refresh.
    """
    try:
        from new_structure.utils.mpesa_status_checker import get_transaction_status
        
        result = get_transaction_status(transaction_id)
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error checking transaction status: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@mpesa_bp.route('/analytics')
@maybe_login_required
def analytics_dashboard():
    """
    M-PESA Analytics Dashboard.
    Shows comprehensive statistics and trends for M-PESA payments.
    """
    try:
        from new_structure.utils.mpesa_analytics import MpesaAnalytics
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
@maybe_login_required
def test_notification():
    """
    Test SMS/Email notification system.
    Send a test notification to verify configuration.
    """
    try:
        from new_structure.utils.notification_service import NotificationService
        
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
