"""
M-PESA Daraja API Client for Lipa Na M-PESA Online (STK Push)
Handles authentication, STK Push requests, and callback processing.
Documentation: https://developer.safaricom.co.ke/APIs/MpesaExpressSimulate
"""
import requests
import base64
from datetime import datetime
from new_structure.extensions import db
from new_structure.models.fee_management import MpesaConfig, MpesaTransaction
import json


class MpesaClient:
    """
    Client for interacting with Safaricom M-PESA Daraja API.
    Handles STK Push payments and callback processing.
    """
    
    # API Endpoints
    SANDBOX_BASE_URL = "https://sandbox.safaricom.co.ke"
    PRODUCTION_BASE_URL = "https://api.safaricom.co.ke"
    
    def __init__(self, config: MpesaConfig):
        """
        Initialize M-PESA client with configuration.
        
        Args:
            config: MpesaConfig instance with API credentials
        """
        self.config = config
        self.base_url = self.SANDBOX_BASE_URL if config.environment == 'sandbox' else self.PRODUCTION_BASE_URL
        self.access_token = None
    
    def generate_access_token(self):
        """
        Generate OAuth access token for API authentication.
        Token is valid for 1 hour.
        
        Returns:
            str: Access token
        """
        url = f"{self.base_url}/oauth/v1/generate?grant_type=client_credentials"
        
        # Create Basic Auth credentials
        credentials = f"{self.config.consumer_key}:{self.config.consumer_secret}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        
        headers = {
            "Authorization": f"Basic {encoded_credentials}"
        }
        
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            self.access_token = data.get('access_token')
            return self.access_token
        except requests.exceptions.RequestException as e:
            print(f"Error generating access token: {str(e)}")
            return None
    
    def generate_password(self):
        """
        Generate password for STK Push request.
        Password = Base64(Shortcode + Passkey + Timestamp)
        
        Returns:
            tuple: (password, timestamp)
        """
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        password_str = f"{self.config.shortcode}{self.config.passkey}{timestamp}"
        password = base64.b64encode(password_str.encode()).decode()
        return password, timestamp
    
    def initiate_stk_push(self, phone_number, amount, account_reference, transaction_desc, callback_url=None):
        """
        Initiate STK Push payment request (Lipa Na M-PESA Online).
        Sends payment prompt to customer's phone.
        
        Args:
            phone_number (str): Customer phone number (format: 254XXXXXXXXX)
            amount (float): Amount to charge
            account_reference (str): Account reference (e.g., student admission number)
            transaction_desc (str): Transaction description
            callback_url (str, optional): Custom callback URL
        
        Returns:
            dict: Response with MerchantRequestID, CheckoutRequestID, ResponseCode, etc.
        """
        # Ensure access token is available
        if not self.access_token:
            self.generate_access_token()
        
        # Generate password and timestamp
        password, timestamp = self.generate_password()
        
        # Use config callback URL if not provided
        if not callback_url:
            callback_url = self.config.callback_url or "https://yourdomain.com/api/mpesa/callback"
        
        # Format phone number (remove + or 0 prefix, ensure 254 prefix)
        phone = phone_number.replace('+', '').replace(' ', '')
        if phone.startswith('0'):
            phone = '254' + phone[1:]
        elif not phone.startswith('254'):
            phone = '254' + phone
        
        # API endpoint
        url = f"{self.base_url}/mpesa/stkpush/v1/processrequest"
        
        # Request headers
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
        
        # Request payload
        payload = {
            "BusinessShortCode": self.config.shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",  # For Paybill, use "CustomerBuyGoodsOnline" for Till
            "Amount": int(amount),  # Must be integer
            "PartyA": phone,  # Customer phone
            "PartyB": self.config.shortcode,  # Business shortcode
            "PhoneNumber": phone,  # Customer phone (same as PartyA)
            "CallBackURL": callback_url,
            "AccountReference": account_reference,
            "TransactionDesc": transaction_desc
        }
        
        try:
            response = requests.post(url, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error initiating STK Push: {str(e)}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"Response: {e.response.text}")
            return {
                'ResponseCode': '1',
                'ResponseDescription': f'Request failed: {str(e)}',
                'errorMessage': str(e)
            }
    
    def query_stk_push_status(self, checkout_request_id):
        """
        Query the status of an STK Push transaction.
        Useful when callback is delayed or not received.
        
        Args:
            checkout_request_id (str): CheckoutRequestID from STK Push response
        
        Returns:
            dict: Transaction status response
        """
        # Ensure access token is available
        if not self.access_token:
            self.generate_access_token()
        
        # Generate password and timestamp
        password, timestamp = self.generate_password()
        
        # API endpoint
        url = f"{self.base_url}/mpesa/stkpushquery/v1/query"
        
        # Request headers
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
        
        # Request payload
        payload = {
            "BusinessShortCode": self.config.shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "CheckoutRequestID": checkout_request_id
        }
        
        try:
            response = requests.post(url, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error querying STK Push status: {str(e)}")
            return {
                'ResponseCode': '1',
                'ResponseDescription': f'Query failed: {str(e)}'
            }


def process_mpesa_callback(callback_data):
    """
    Process M-PESA callback data and update transaction status.
    Called by the callback endpoint when Safaricom sends payment confirmation.
    
    Args:
        callback_data (dict): Full callback JSON from Safaricom
    
    Returns:
        bool: True if processing successful, False otherwise
    """
    try:
        # Extract result from callback
        body = callback_data.get('Body', {})
        stk_callback = body.get('stkCallback', {})
        
        merchant_request_id = stk_callback.get('MerchantRequestID')
        checkout_request_id = stk_callback.get('CheckoutRequestID')
        result_code = stk_callback.get('ResultCode')
        result_desc = stk_callback.get('ResultDesc')
        
        # Find the transaction
        transaction = MpesaTransaction.query.filter_by(
            checkout_request_id=checkout_request_id
        ).first()
        
        if not transaction:
            print(f"Transaction not found: {checkout_request_id}")
            return False
        
        # Update transaction with callback data
        transaction.callback_received = True
        transaction.callback_data = json.dumps(callback_data)
        transaction.result_code = str(result_code)
        transaction.result_desc = result_desc
        
        # Check result code
        if result_code == 0:
            # Success - extract metadata
            callback_metadata = stk_callback.get('CallbackMetadata', {})
            items = callback_metadata.get('Item', [])
            
            # Extract metadata fields
            metadata = {}
            for item in items:
                name = item.get('Name')
                value = item.get('Value')
                metadata[name] = value
            
            # Update transaction with success data
            transaction.status = 'success'
            transaction.mpesa_receipt_number = metadata.get('MpesaReceiptNumber')
            transaction.amount = metadata.get('Amount')
            transaction.phone_number = str(metadata.get('PhoneNumber'))
            
            # Parse transaction date (format: YYYYMMDDHHMMSS)
            trans_date_str = str(metadata.get('TransactionDate'))
            if trans_date_str and len(trans_date_str) == 14:
                transaction.transaction_date = datetime.strptime(trans_date_str, '%Y%m%d%H%M%S')
            
            print(f"M-PESA payment successful: {transaction.mpesa_receipt_number}")
            
        else:
            # Failed transaction
            transaction.status = 'failed'
            print(f"M-PESA payment failed: {result_desc}")
        
        db.session.commit()
        return True
        
    except Exception as e:
        print(f"Error processing M-PESA callback: {str(e)}")
        db.session.rollback()
        return False


def get_mpesa_config(school_id=None):
    """
    Get M-PESA configuration for a school.
    
    Args:
        school_id (int, optional): School ID (for multi-school setup)
    
    Returns:
        MpesaConfig: Configuration object or None
    """
    query = MpesaConfig.query.filter_by(is_enabled=True)
    
    if school_id:
        query = query.filter_by(school_id=school_id)
    else:
        query = query.filter_by(school_id=None)
    
    return query.first()


def create_stk_push_transaction(phone_number, amount, account_reference, transaction_desc, student_id=None, school_id=None):
    """
    Create and initiate an STK Push transaction.
    Combines transaction record creation and API call.
    
    Args:
        phone_number (str): Customer phone number
        amount (float): Amount to charge
        account_reference (str): Account reference
        transaction_desc (str): Transaction description
        student_id (int, optional): Student ID for reconciliation
        school_id (int, optional): School ID
    
    Returns:
        tuple: (transaction, api_response)
    """
    # Get M-PESA configuration
    config = get_mpesa_config(school_id)
    if not config:
        return None, {
            'ResponseCode': '1',
            'ResponseDescription': 'M-PESA not configured for this school'
        }
    
    # Create transaction record
    transaction = MpesaTransaction(
        school_id=school_id,
        config_id=config.id,
        transaction_type='stk_push',
        phone_number=phone_number,
        amount=amount,
        account_reference=account_reference,
        transaction_desc=transaction_desc,
        student_id=student_id,
        status='pending'
    )
    
    db.session.add(transaction)
    db.session.commit()
    
    # Initialize M-PESA client
    client = MpesaClient(config)
    
    # Initiate STK Push
    response = client.initiate_stk_push(
        phone_number=phone_number,
        amount=amount,
        account_reference=account_reference,
        transaction_desc=transaction_desc
    )
    
    # Update transaction with response data
    if response.get('ResponseCode') == '0':
        transaction.merchant_request_id = response.get('MerchantRequestID')
        transaction.checkout_request_id = response.get('CheckoutRequestID')
        db.session.commit()
    else:
        # Mark as failed if request failed
        transaction.status = 'failed'
        transaction.result_desc = response.get('ResponseDescription') or response.get('errorMessage')
        db.session.commit()
    
    return transaction, response
