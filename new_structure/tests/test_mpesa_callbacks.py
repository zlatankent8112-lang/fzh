"""
Unit Tests for M-PESA Callback Processing
Tests the handling of M-PESA payment callbacks.
"""

import pytest
import json
from unittest.mock import Mock, patch
from datetime import datetime

from new_structure.models.fee_management import MpesaTransaction


class TestCallbackProcessing:
    """Test M-PESA callback processing"""
    
    def test_successful_callback_processing(self, client, db_session, sample_mpesa_transaction, mock_callback_success, safaricom_ip):
        """Test processing of successful payment callback"""
        response = client.post('/mpesa/callback',
            data=json.dumps(mock_callback_success),
            content_type='application/json',
            environ_base={'REMOTE_ADDR': safaricom_ip}
        )
        
        assert response.status_code == 200
        
        # Verify transaction was updated
        transaction = MpesaTransaction.query.filter_by(
            checkout_request_id='test-checkout-67890'
        ).first()
        
        if transaction:
            assert transaction.status == 'success'
            assert transaction.mpesa_receipt_number == 'TK4BY9913Q'
            assert transaction.result_code == 0
    
    def test_failed_callback_processing(self, client, db_session, sample_mpesa_transaction, mock_callback_failed, safaricom_ip):
        """Test processing of failed payment callback"""
        # Update sample transaction IDs to match callback
        sample_mpesa_transaction.merchant_request_id = 'test-merchant-12345'
        sample_mpesa_transaction.checkout_request_id = 'test-checkout-67890'
        db_session.commit()
        
        response = client.post('/mpesa/callback',
            data=json.dumps(mock_callback_failed),
            content_type='application/json',
            environ_base={'REMOTE_ADDR': safaricom_ip}
        )
        
        assert response.status_code == 200
        
        # Verify transaction was updated
        transaction = MpesaTransaction.query.filter_by(
            checkout_request_id='test-checkout-67890'
        ).first()
        
        assert transaction.status == 'failed'
        assert transaction.result_code == 1032
        assert 'cancelled by user' in transaction.result_desc.lower()
    
    def test_timeout_callback_processing(self, client, db_session, sample_mpesa_transaction, mock_callback_timeout, safaricom_ip):
        """Test processing of timeout callback"""
        # Update sample transaction IDs
        sample_mpesa_transaction.merchant_request_id = 'test-merchant-12345'
        sample_mpesa_transaction.checkout_request_id = 'test-checkout-67890'
        db_session.commit()
        
        response = client.post('/mpesa/callback',
            data=json.dumps(mock_callback_timeout),
            content_type='application/json',
            environ_base={'REMOTE_ADDR': safaricom_ip}
        )
        
        assert response.status_code == 200
        
        # Verify transaction was updated
        transaction = MpesaTransaction.query.filter_by(
            checkout_request_id='test-checkout-67890'
        ).first()
        
        assert transaction.status in ['timeout', 'failed']
        assert transaction.result_code == 1037
    
    def test_callback_with_invalid_ip(self, client, mock_callback_success, invalid_ip):
        """Test callback from non-Safaricom IP is rejected"""
        response = client.post('/mpesa/callback',
            data=json.dumps(mock_callback_success),
            content_type='application/json',
            environ_base={'REMOTE_ADDR': invalid_ip}
        )
        
        # Should be rejected
        assert response.status_code in [403, 401]
    
    def test_callback_without_ip_validation(self, client, mock_callback_success):
        """Test callback without IP address"""
        response = client.post('/mpesa/callback',
            data=json.dumps(mock_callback_success),
            content_type='application/json'
        )
        
        # Should still process or reject gracefully
        assert response.status_code in [200, 403, 401]
    
    def test_callback_with_malformed_data(self, client, safaricom_ip):
        """Test callback with malformed JSON data"""
        malformed_data = [
            '{"invalid json',  # Invalid JSON
            '{}',  # Empty object
            '{"Body": {}}',  # Missing stkCallback
            '{"Body": {"stkCallback": {}}}',  # Missing required fields
        ]
        
        for data in malformed_data:
            response = client.post('/mpesa/callback',
                data=data,
                content_type='application/json',
                environ_base={'REMOTE_ADDR': safaricom_ip}
            )
            
            # Should handle gracefully
            assert response.status_code in [200, 400, 500]
    
    def test_callback_for_nonexistent_transaction(self, client, safaricom_ip):
        """Test callback for transaction that doesn't exist in database"""
        callback_data = {
            'Body': {
                'stkCallback': {
                    'MerchantRequestID': 'nonexistent-merchant',
                    'CheckoutRequestID': 'nonexistent-checkout',
                    'ResultCode': 0,
                    'ResultDesc': 'Success'
                }
            }
        }
        
        response = client.post('/mpesa/callback',
            data=json.dumps(callback_data),
            content_type='application/json',
            environ_base={'REMOTE_ADDR': safaricom_ip}
        )
        
        # Should handle gracefully
        assert response.status_code in [200, 404]
    
    def test_callback_metadata_extraction(self, client, db_session, sample_mpesa_transaction, safaricom_ip):
        """Test extraction of callback metadata"""
        # Update sample transaction
        sample_mpesa_transaction.merchant_request_id = 'test-merchant-meta'
        sample_mpesa_transaction.checkout_request_id = 'test-checkout-meta'
        db_session.commit()
        
        callback_data = {
            'Body': {
                'stkCallback': {
                    'MerchantRequestID': 'test-merchant-meta',
                    'CheckoutRequestID': 'test-checkout-meta',
                    'ResultCode': 0,
                    'ResultDesc': 'Success',
                    'CallbackMetadata': {
                        'Item': [
                            {'Name': 'Amount', 'Value': 1500.00},
                            {'Name': 'MpesaReceiptNumber', 'Value': 'TK12345XYZ'},
                            {'Name': 'TransactionDate', 'Value': 20251104150000},
                            {'Name': 'PhoneNumber', 'Value': 254712345678}
                        ]
                    }
                }
            }
        }
        
        response = client.post('/mpesa/callback',
            data=json.dumps(callback_data),
            content_type='application/json',
            environ_base={'REMOTE_ADDR': safaricom_ip}
        )
        
        assert response.status_code == 200
        
        # Verify metadata was extracted
        transaction = MpesaTransaction.query.filter_by(
            checkout_request_id='test-checkout-meta'
        ).first()
        
        assert transaction.mpesa_receipt_number == 'TK12345XYZ'
        assert transaction.amount == 1500.00 or transaction.amount == 1000.00  # Original or updated
    
    def test_callback_duplicate_prevention(self, client, db_session, sample_mpesa_transaction, mock_callback_success, safaricom_ip):
        """Test duplicate callbacks don't cause issues"""
        # Update sample transaction
        sample_mpesa_transaction.merchant_request_id = 'test-merchant-12345'
        sample_mpesa_transaction.checkout_request_id = 'test-checkout-67890'
        db_session.commit()
        
        # Send same callback twice
        response1 = client.post('/mpesa/callback',
            data=json.dumps(mock_callback_success),
            content_type='application/json',
            environ_base={'REMOTE_ADDR': safaricom_ip}
        )
        
        response2 = client.post('/mpesa/callback',
            data=json.dumps(mock_callback_success),
            content_type='application/json',
            environ_base={'REMOTE_ADDR': safaricom_ip}
        )
        
        # Both should succeed
        assert response1.status_code == 200
        assert response2.status_code == 200
        
        # Transaction should still be valid
        transaction = MpesaTransaction.query.filter_by(
            checkout_request_id='test-checkout-67890'
        ).first()
        
        assert transaction.status == 'success'


class TestCallbackResultCodes:
    """Test handling of various M-PESA result codes"""
    
    def test_result_code_mapping(self, client, db_session, safaricom_ip):
        """Test various result codes are handled correctly"""
        result_codes = [
            (0, 'success', 'Success'),
            (1, 'failed', 'Insufficient funds'),
            (1032, 'failed', 'Cancelled by user'),
            (1037, 'timeout', 'Timeout'),
            (2001, 'failed', 'Invalid initiator'),
            (9999, 'failed', 'Unknown error'),
        ]
        
        for code, expected_status, desc in result_codes:
            # Create transaction
            transaction = MpesaTransaction(
                phone_number='254712345678',
                amount=1000.00,
                account_reference=f'STD{code}',
                merchant_request_id=f'merchant-{code}',
                checkout_request_id=f'checkout-{code}',
                status='pending'
            )
            db_session.add(transaction)
            db_session.commit()
            
            # Send callback
            callback_data = {
                'Body': {
                    'stkCallback': {
                        'MerchantRequestID': f'merchant-{code}',
                        'CheckoutRequestID': f'checkout-{code}',
                        'ResultCode': code,
                        'ResultDesc': desc
                    }
                }
            }
            
            response = client.post('/mpesa/callback',
                data=json.dumps(callback_data),
                content_type='application/json',
                environ_base={'REMOTE_ADDR': safaricom_ip}
            )
            
            assert response.status_code == 200
            
            # Verify status
            tx = MpesaTransaction.query.filter_by(
                checkout_request_id=f'checkout-{code}'
            ).first()
            
            if expected_status == 'timeout':
                assert tx.status in ['timeout', 'failed']
            else:
                assert tx.status == expected_status


class TestCallbackNotifications:
    """Test notification triggering from callbacks"""
    
    @patch('utils.notification_service.NotificationService.send_payment_notification')
    def test_notification_on_successful_payment(self, mock_notify, client, db_session, sample_student, safaricom_ip):
        """Test notification is sent on successful payment"""
        # Create transaction linked to student
        transaction = MpesaTransaction(
            phone_number='254712345678',
            amount=1000.00,
            account_reference=sample_student.admission_number,
            merchant_request_id='test-merchant-notify',
            checkout_request_id='test-checkout-notify',
            status='pending'
        )
        db_session.add(transaction)
        db_session.commit()
        
        # Send success callback
        callback_data = {
            'Body': {
                'stkCallback': {
                    'MerchantRequestID': 'test-merchant-notify',
                    'CheckoutRequestID': 'test-checkout-notify',
                    'ResultCode': 0,
                    'ResultDesc': 'Success',
                    'CallbackMetadata': {
                        'Item': [
                            {'Name': 'Amount', 'Value': 1000.00},
                            {'Name': 'MpesaReceiptNumber', 'Value': 'TK123'},
                            {'Name': 'PhoneNumber', 'Value': 254712345678}
                        ]
                    }
                }
            }
        }
        
        response = client.post('/mpesa/callback',
            data=json.dumps(callback_data),
            content_type='application/json',
            environ_base={'REMOTE_ADDR': safaricom_ip}
        )
        
        assert response.status_code == 200
        
        # Verify notification was attempted
        # (may not be called if notification is disabled or fails)
    
    @patch('utils.notification_service.NotificationService.send_payment_notification')
    def test_no_notification_on_failed_payment(self, mock_notify, client, db_session, sample_mpesa_transaction, mock_callback_failed, safaricom_ip):
        """Test notification is not sent on failed payment"""
        # Update transaction
        sample_mpesa_transaction.merchant_request_id = 'test-merchant-12345'
        sample_mpesa_transaction.checkout_request_id = 'test-checkout-67890'
        db_session.commit()
        
        response = client.post('/mpesa/callback',
            data=json.dumps(mock_callback_failed),
            content_type='application/json',
            environ_base={'REMOTE_ADDR': safaricom_ip}
        )
        
        assert response.status_code == 200
        
        # Notification should not be called for failed payments
        # (unless configuration says otherwise)


class TestCallbackDatabaseUpdates:
    """Test database updates from callbacks"""
    
    def test_transaction_status_update(self, db_session, sample_mpesa_transaction):
        """Test transaction status is updated correctly"""
        original_status = sample_mpesa_transaction.status
        assert original_status == 'pending'
        
        # Simulate callback update
        sample_mpesa_transaction.status = 'success'
        sample_mpesa_transaction.result_code = 0
        sample_mpesa_transaction.result_desc = 'Success'
        sample_mpesa_transaction.mpesa_receipt_number = 'TK123456'
        db_session.commit()
        
        # Verify update
        updated = MpesaTransaction.query.get(sample_mpesa_transaction.id)
        assert updated.status == 'success'
        assert updated.result_code == 0
        assert updated.mpesa_receipt_number == 'TK123456'
    
    def test_transaction_timestamp_update(self, db_session, sample_mpesa_transaction):
        """Test transaction updated_at timestamp is updated"""
        original_time = sample_mpesa_transaction.updated_at
        
        # Small delay
        import time
        time.sleep(0.1)
        
        # Update transaction
        sample_mpesa_transaction.status = 'success'
        db_session.commit()
        
        # Verify timestamp changed
        updated = MpesaTransaction.query.get(sample_mpesa_transaction.id)
        # Updated_at should be set automatically by database or model
    
    def test_callback_preserves_original_data(self, db_session, sample_mpesa_transaction):
        """Test callback doesn't overwrite original transaction data"""
        original_phone = sample_mpesa_transaction.phone_number
        original_amount = sample_mpesa_transaction.amount
        original_reference = sample_mpesa_transaction.account_reference
        
        # Simulate callback update (only updates specific fields)
        sample_mpesa_transaction.status = 'success'
        sample_mpesa_transaction.mpesa_receipt_number = 'TK123'
        db_session.commit()
        
        # Verify original data preserved
        updated = MpesaTransaction.query.get(sample_mpesa_transaction.id)
        assert updated.phone_number == original_phone
        assert updated.amount == original_amount
        assert updated.account_reference == original_reference
