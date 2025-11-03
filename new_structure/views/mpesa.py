"""
M-PESA Integration Routes
Handles M-PESA configuration, STK Push payments, and callbacks.
"""
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required, current_user
from new_structure.extensions import db
from new_structure.models.fee_management import MpesaConfig, MpesaTransaction, Payment
from new_structure.models.student_management import Student
from new_structure.utils.mpesa_client import (
    MpesaClient, 
    process_mpesa_callback, 
    get_mpesa_config,
    create_stk_push_transaction
)
from datetime import datetime
import json

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
        transaction_desc = f"Fee payment for {student.full_name}"
        
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
        # Get callback data
        callback_data = request.get_json()
        
        # Log callback for debugging
        print(f"M-PESA Callback received: {json.dumps(callback_data, indent=2)}")
        
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
                    # Create payment record
                    payment = Payment(
                        student_id=transaction.student_id,
                        amount=transaction.amount,
                        payment_date=transaction.transaction_date or datetime.now(),
                        payment_method='mpesa',
                        reference_number=transaction.mpesa_receipt_number,
                        recorded_by=1,  # System (could be improved)
                        notes=f"M-PESA payment: {transaction.transaction_desc}"
                    )
                    
                    db.session.add(payment)
                    db.session.commit()
                    
                    # Link payment to transaction
                    transaction.payment_id = payment.id
                    db.session.commit()
                    
                    print(f"Auto-reconciliation: Payment {payment.id} created for transaction {transaction.id}")
            
            return jsonify({
                'ResultCode': 0,
                'ResultDesc': 'Success'
            })
        else:
            return jsonify({
                'ResultCode': 1,
                'ResultDesc': 'Failed to process callback'
            })
    
    except Exception as e:
        print(f"Error in M-PESA callback: {str(e)}")
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
