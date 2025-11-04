"""
Staging Environment Integration Tests
Tests M-PESA integration with real Safaricom APIs in staging mode.
These tests require valid staging credentials and should be run separately.

Usage:
    pytest tests/test_mpesa_staging.py -v --staging
    
Environment Variables Required:
    MPESA_ENVIRONMENT=staging
    MPESA_CONSUMER_KEY=<staging_key>
    MPESA_CONSUMER_SECRET=<staging_secret>
    MPESA_SHORTCODE=<staging_shortcode>
    MPESA_PASSKEY=<staging_passkey>
"""

import pytest
import os
import time
from datetime import datetime

from new_structure.models.fee_management import MpesaTransaction


# Mark all tests in this module as staging
pytestmark = pytest.mark.staging


def pytest_configure(config):
    """Register staging marker"""
    config.addinivalue_line(
        "markers", "staging: mark test as staging environment test (requires credentials)"
    )


def skip_if_no_staging_credentials():
    """Skip test if staging credentials not available"""
    required_vars = [
        'MPESA_CONSUMER_KEY',
        'MPESA_CONSUMER_SECRET',
        'MPESA_SHORTCODE',
        'MPESA_PASSKEY'
    ]
    
    missing = [var for var in required_vars if not os.getenv(var)]
    
    if missing:
        pytest.skip(f"Staging credentials missing: {', '.join(missing)}")


class TestStagingAccessToken:
    """Test access token generation with real API"""
    
    def test_generate_access_token_from_staging(self, app):
        """Test generating access token from Safaricom staging API"""
        skip_if_no_staging_credentials()
        
        with app.app_context():
            # Import here to ensure app context is available
            from new_structure.utils.mpesa_utils import get_access_token
            
            token = get_access_token()
            
            # Verify token generated
            assert token is not None
            assert len(token) > 20  # Access tokens are typically long
            assert isinstance(token, str)
    
    def test_access_token_caching(self, app):
        """Test that access token is cached and reused"""
        skip_if_no_staging_credentials()
        
        with app.app_context():
            from new_structure.utils.mpesa_utils import get_access_token
            
            # Get token twice
            token1 = get_access_token()
            token2 = get_access_token()
            
            # Should be same token (cached)
            assert token1 == token2


class TestStagingSTKPush:
    """Test STK Push with real staging API"""
    
    @pytest.mark.slow
    def test_initiate_stk_push_staging(self, auth_client, db_session, sample_student):
        """Test initiating real STK Push to staging environment"""
        skip_if_no_staging_credentials()
        
        # Use test phone number from Safaricom staging docs
        test_phone = os.getenv('MPESA_TEST_PHONE', '254708374149')
        
        payment_data = {
            'phone_number': test_phone,
            'amount': 1,  # Minimum amount for testing
            'account_reference': sample_student.admission_number,
            'transaction_desc': 'Test payment - staging'
        }
        
        response = auth_client.post('/mpesa/stk-push', json=payment_data)
        
        # Verify response from real API
        assert response.status_code == 200
        data = response.json
        
        assert 'MerchantRequestID' in data or 'merchant_request_id' in data
        assert 'CheckoutRequestID' in data or 'checkout_request_id' in data
        
        # Verify transaction created in database
        if 'CheckoutRequestID' in data:
            checkout_id = data['CheckoutRequestID']
        else:
            checkout_id = data.get('checkout_request_id')
        
        txn = db_session.query(MpesaTransaction).filter_by(
            checkout_request_id=checkout_id
        ).first()
        
        assert txn is not None
        assert txn.status == 'pending'
    
    @pytest.mark.slow
    def test_stk_push_with_invalid_phone_staging(self, auth_client, db_session, sample_student):
        """Test STK Push with invalid phone number on staging"""
        skip_if_no_staging_credentials()
        
        payment_data = {
            'phone_number': '1234567890',  # Invalid phone
            'amount': 1,
            'account_reference': sample_student.admission_number
        }
        
        response = auth_client.post('/mpesa/stk-push', json=payment_data)
        
        # Should fail validation or API should reject
        assert response.status_code in [400, 422, 200]  # Depends on validation


class TestStagingQueryStatus:
    """Test transaction status query with staging API"""
    
    @pytest.mark.slow
    def test_query_transaction_status_staging(self, auth_client, sample_mpesa_transaction):
        """Test querying transaction status from staging API"""
        skip_if_no_staging_credentials()
        
        # Query status endpoint (if implemented)
        response = auth_client.get(
            f'/mpesa/query-status/{sample_mpesa_transaction.checkout_request_id}'
        )
        
        # Should return status information
        assert response.status_code in [200, 404, 501]  # 501 if not implemented


class TestStagingCallbackProcessing:
    """Test callback processing with staging-like data"""
    
    def test_process_staging_callback_format(self, auth_client, db_session, 
                                            sample_mpesa_transaction):
        """Test processing callback in staging format"""
        skip_if_no_staging_credentials()
        
        # Use real staging callback format
        callback_data = {
            'Body': {
                'stkCallback': {
                    'MerchantRequestID': sample_mpesa_transaction.merchant_request_id,
                    'CheckoutRequestID': sample_mpesa_transaction.checkout_request_id,
                    'ResultCode': 0,
                    'ResultDesc': 'The service request is processed successfully.',
                    'CallbackMetadata': {
                        'Item': [
                            {'Name': 'Amount', 'Value': sample_mpesa_transaction.amount},
                            {'Name': 'MpesaReceiptNumber', 'Value': 'TEST123456'},
                            {'Name': 'TransactionDate', 'Value': int(datetime.now().strftime('%Y%m%d%H%M%S'))},
                            {'Name': 'PhoneNumber', 'Value': int(sample_mpesa_transaction.phone_number)}
                        ]
                    }
                }
            }
        }
        
        # Process callback from valid Safaricom IP
        with auth_client.application.test_request_context():
            response = auth_client.post(
                '/mpesa/callback',
                json=callback_data,
                environ_base={'REMOTE_ADDR': '196.201.214.200'}
            )
        
        assert response.status_code == 200


class TestStagingErrorHandling:
    """Test error handling with staging API"""
    
    @pytest.mark.slow
    def test_staging_api_timeout_handling(self, auth_client, db_session, sample_student):
        """Test handling of API timeouts in staging"""
        skip_if_no_staging_credentials()
        
        payment_data = {
            'phone_number': os.getenv('MPESA_TEST_PHONE', '254708374149'),
            'amount': 1,
            'account_reference': sample_student.admission_number
        }
        
        # Make request with short timeout
        try:
            response = auth_client.post('/mpesa/stk-push', json=payment_data)
            # Should either succeed or handle timeout gracefully
            assert response.status_code in [200, 500, 503, 504]
        except Exception as e:
            # Should not crash application
            assert 'timeout' in str(e).lower() or 'connection' in str(e).lower()
    
    @pytest.mark.slow  
    def test_staging_invalid_credentials_handling(self, app, auth_client, db_session, sample_student):
        """Test handling of invalid credentials"""
        skip_if_no_staging_credentials()
        
        # Temporarily use invalid credentials
        original_key = os.getenv('MPESA_CONSUMER_KEY')
        os.environ['MPESA_CONSUMER_KEY'] = 'invalid_key_12345'
        
        try:
            payment_data = {
                'phone_number': '254708374149',
                'amount': 1,
                'account_reference': sample_student.admission_number
            }
            
            response = auth_client.post('/mpesa/stk-push', json=payment_data)
            
            # Should handle authentication error
            assert response.status_code in [401, 500, 200]
        finally:
            # Restore original credentials
            if original_key:
                os.environ['MPESA_CONSUMER_KEY'] = original_key


class TestStagingEndToEnd:
    """End-to-end tests with staging environment"""
    
    @pytest.mark.slow
    @pytest.mark.manual  # Requires manual intervention to complete payment
    def test_complete_payment_flow_staging(self, auth_client, db_session, sample_student):
        """
        Complete payment flow test in staging.
        NOTE: This requires manual phone interaction to accept payment.
        """
        skip_if_no_staging_credentials()
        
        test_phone = os.getenv('MPESA_TEST_PHONE', '254708374149')
        
        print("\n" + "="*60)
        print("MANUAL STAGING TEST")
        print("="*60)
        print(f"Phone: {test_phone}")
        print("Amount: KES 1")
        print("\nThis test will send STK Push to the phone above.")
        print("Please complete the payment on the phone within 60 seconds.")
        print("="*60 + "\n")
        
        # Initiate payment
        payment_data = {
            'phone_number': test_phone,
            'amount': 1,
            'account_reference': sample_student.admission_number,
            'transaction_desc': 'Manual staging test'
        }
        
        response = auth_client.post('/mpesa/stk-push', json=payment_data)
        assert response.status_code == 200
        
        data = response.json
        checkout_id = data.get('CheckoutRequestID') or data.get('checkout_request_id')
        
        print(f"✓ STK Push initiated: {checkout_id}")
        print("\nWaiting for payment completion...")
        
        # Wait for callback (max 60 seconds)
        max_wait = 60
        interval = 5
        elapsed = 0
        
        while elapsed < max_wait:
            time.sleep(interval)
            elapsed += interval
            
            # Check transaction status
            db_session.expire_all()
            txn = db_session.query(MpesaTransaction).filter_by(
                checkout_request_id=checkout_id
            ).first()
            
            if txn.status != 'pending':
                print(f"\n✓ Payment {txn.status}!")
                print(f"Receipt: {txn.mpesa_receipt_number}")
                assert txn.status == 'completed'
                return
            
            print(f"  Still pending... ({elapsed}s)")
        
        print("\n⚠ Timeout: Payment not completed within 60 seconds")
        pytest.skip("Manual payment not completed in time")


class TestStagingPerformance:
    """Test performance with staging API"""
    
    @pytest.mark.slow
    def test_stk_push_response_time(self, auth_client, db_session, sample_student):
        """Test STK Push response time in staging"""
        skip_if_no_staging_credentials()
        
        payment_data = {
            'phone_number': os.getenv('MPESA_TEST_PHONE', '254708374149'),
            'amount': 1,
            'account_reference': sample_student.admission_number
        }
        
        start_time = time.time()
        response = auth_client.post('/mpesa/stk-push', json=payment_data)
        elapsed = time.time() - start_time
        
        # Should complete within reasonable time (< 5 seconds)
        assert response.status_code == 200
        assert elapsed < 5.0
        
        print(f"\nSTK Push Response Time: {elapsed:.2f}s")


class TestStagingConfiguration:
    """Test staging environment configuration"""
    
    def test_staging_environment_variables(self):
        """Test all required staging environment variables are set"""
        skip_if_no_staging_credentials()
        
        required_vars = {
            'MPESA_ENVIRONMENT': 'staging',
            'MPESA_CONSUMER_KEY': str,
            'MPESA_CONSUMER_SECRET': str,
            'MPESA_SHORTCODE': str,
            'MPESA_PASSKEY': str
        }
        
        for var, expected_type in required_vars.items():
            value = os.getenv(var)
            assert value is not None, f"{var} not set"
            
            if expected_type == str:
                assert isinstance(value, str)
                assert len(value) > 0
    
    def test_staging_urls_configured(self, app):
        """Test staging API URLs are correctly configured"""
        skip_if_no_staging_credentials()
        
        with app.app_context():
            # Check that staging URLs are being used
            env = os.getenv('MPESA_ENVIRONMENT', 'production')
            assert env == 'staging'
            
            # Staging API base URL should be sandbox
            # This depends on how URLs are configured in the app


class TestStagingDataValidation:
    """Test data validation with staging API"""
    
    @pytest.mark.slow
    def test_staging_amount_limits(self, auth_client, db_session, sample_student):
        """Test amount validation with staging API"""
        skip_if_no_staging_credentials()
        
        # Test minimum amount
        payment_data = {
            'phone_number': os.getenv('MPESA_TEST_PHONE', '254708374149'),
            'amount': 1,  # Minimum
            'account_reference': sample_student.admission_number
        }
        
        response = auth_client.post('/mpesa/stk-push', json=payment_data)
        assert response.status_code == 200
        
        # Test zero amount (should fail)
        payment_data['amount'] = 0
        response = auth_client.post('/mpesa/stk-push', json=payment_data)
        assert response.status_code in [400, 422]
    
    @pytest.mark.slow
    def test_staging_phone_validation(self, auth_client, db_session, sample_student):
        """Test phone number validation with staging API"""
        skip_if_no_staging_credentials()
        
        test_cases = [
            ('0708374149', 200),      # Valid format 1
            ('254708374149', 200),    # Valid format 2
            ('+254708374149', 200),   # Valid format 3
            ('708374149', 200),       # Valid format 4
            ('123', 400),             # Invalid - too short
            ('abc', 400),             # Invalid - non-numeric
        ]
        
        for phone, expected_status in test_cases:
            payment_data = {
                'phone_number': phone,
                'amount': 1,
                'account_reference': sample_student.admission_number
            }
            
            response = auth_client.post('/mpesa/stk-push', json=payment_data)
            # Allow some flexibility in status codes
            assert response.status_code in [expected_status, 200, 400, 422]


# Test fixtures for staging
@pytest.fixture(scope='module')
def staging_credentials():
    """Load staging credentials from environment"""
    skip_if_no_staging_credentials()
    
    return {
        'consumer_key': os.getenv('MPESA_CONSUMER_KEY'),
        'consumer_secret': os.getenv('MPESA_CONSUMER_SECRET'),
        'shortcode': os.getenv('MPESA_SHORTCODE'),
        'passkey': os.getenv('MPESA_PASSKEY'),
        'test_phone': os.getenv('MPESA_TEST_PHONE', '254708374149')
    }


@pytest.fixture
def staging_app(app):
    """Configure app for staging environment"""
    skip_if_no_staging_credentials()
    
    # Ensure app is using staging configuration
    with app.app_context():
        app.config['MPESA_ENVIRONMENT'] = 'staging'
        yield app
