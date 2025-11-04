"""
M-PESA Transaction Timeout Handler
Automatically marks pending transactions as timed out after 2 minutes.
Run this as a background job (e.g., cron job or celery task).
"""
from datetime import datetime, timedelta
from new_structure import create_app
from new_structure.extensions import db
from new_structure.models.fee_management import MpesaTransaction
import logging

logger = logging.getLogger(__name__)

def handle_transaction_timeouts(timeout_minutes=2):
    """
    Mark pending transactions as timed out if they haven't received a callback.
    
    Args:
        timeout_minutes: Number of minutes before marking as timeout (default: 2)
    """
    app = create_app()
    
    with app.app_context():
        try:
            # Calculate timeout threshold
            timeout_threshold = datetime.utcnow() - timedelta(minutes=timeout_minutes)
            
            # Find pending transactions older than threshold
            timed_out_transactions = MpesaTransaction.query.filter(
                MpesaTransaction.status == 'pending',
                MpesaTransaction.created_at < timeout_threshold,
                MpesaTransaction.callback_received == False
            ).all()
            
            if not timed_out_transactions:
                logger.info("No timed out transactions found")
                return 0
            
            # Mark as timeout
            count = 0
            for transaction in timed_out_transactions:
                transaction.status = 'timeout'
                transaction.result_desc = 'Transaction timed out - no callback received'
                transaction.updated_at = datetime.utcnow()
                count += 1
                logger.warning(f"Transaction #{transaction.id} marked as timeout (created: {transaction.created_at})")
            
            db.session.commit()
            logger.info(f"Marked {count} transactions as timed out")
            return count
            
        except Exception as e:
            logger.error(f"Error handling transaction timeouts: {str(e)}", exc_info=True)
            db.session.rollback()
            return 0

if __name__ == "__main__":
    # Run directly for testing
    logging.basicConfig(level=logging.INFO)
    print("🔍 Checking for timed out M-PESA transactions...")
    count = handle_transaction_timeouts()
    print(f"✅ Processed {count} timed out transactions")
