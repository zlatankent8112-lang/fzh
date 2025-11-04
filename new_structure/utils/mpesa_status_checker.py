"""
Real-time M-PESA transaction status checker.
Provides API endpoint for polling transaction status.
"""
from flask import jsonify
from ..models.fee_management import MpesaTransaction
import logging

logger = logging.getLogger(__name__)


def get_transaction_status(transaction_id):
    """
    Get the current status of an M-PESA transaction.
    
    Args:
        transaction_id: ID of the M-PESA transaction
        
    Returns:
        Dictionary with transaction status and details
    """
    try:
        transaction = MpesaTransaction.query.get(transaction_id)
        
        if not transaction:
            return {
                'success': False,
                'error': 'Transaction not found'
            }
        
        # Prepare response
        response = {
            'success': True,
            'transaction_id': transaction.id,
            'status': transaction.status,
            'amount': float(transaction.amount),
            'phone_number': transaction.phone_number,
            'created_at': transaction.created_at.isoformat() if transaction.created_at else None,
            'completed': transaction.status in ['success', 'failed', 'cancelled', 'timeout']
        }
        
        # Add status-specific details
        if transaction.status == 'success':
            response['receipt_number'] = transaction.mpesa_receipt_number
            response['transaction_date'] = transaction.transaction_date.isoformat() if transaction.transaction_date else None
            response['payment_id'] = transaction.payment_id
            response['message'] = 'Payment successful! Receipt: ' + (transaction.mpesa_receipt_number or 'N/A')
            
        elif transaction.status == 'failed':
            response['error_message'] = transaction.result_desc or 'Payment failed'
            response['message'] = 'Payment failed: ' + (transaction.result_desc or 'Unknown error')
            
        elif transaction.status == 'cancelled':
            response['message'] = 'Payment was cancelled'
            
        elif transaction.status == 'timeout':
            response['message'] = 'Payment request timed out. Please try again.'
            
        elif transaction.status == 'pending':
            response['message'] = 'Waiting for payment confirmation...'
            response['checkout_request_id'] = transaction.checkout_request_id
        
        return response
        
    except Exception as e:
        logger.error(f"Error getting transaction status: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e)
        }
