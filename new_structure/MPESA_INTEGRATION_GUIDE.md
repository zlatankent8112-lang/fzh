# M-PESA Integration Guide

## 🚀 Overview

The Hillview School Management System now supports **M-PESA Daraja API** integration for automated mobile payment collection. Parents can pay school fees directly from their phones using Lipa Na M-PESA Online (STK Push).

## ✨ Features Implemented

### 1. **Database Schema**
- ✅ `mpesa_config` table - Store API credentials per school
- ✅ `mpesa_transaction` table - Track all M-PESA transactions
- ✅ Foreign keys for student and payment linkage
- ✅ Indexes for performance optimization

### 2. **M-PESA Client Library** (`utils/mpesa_client.py`)
- ✅ OAuth token generation
- ✅ STK Push request (Lipa Na M-PESA Online)
- ✅ Transaction status queries
- ✅ Callback processing with auto-reconciliation

### 3. **Configuration Management**
- ✅ Web UI for entering API credentials
- ✅ Sandbox + Production environment support
- ✅ Test connection functionality
- ✅ Enable/disable toggle

### 4. **Transaction Management**
- ✅ Transaction dashboard with filters
- ✅ Status tracking (pending/success/failed/cancelled/timeout)
- ✅ Detailed transaction view
- ✅ Manual status check for pending transactions
- ✅ Statistics (total, successful, pending, amount)

### 5. **Payment Integration**
- ✅ STK Push button in record payment page
- ✅ Auto-reconciliation on successful callback
- ✅ Automatic payment record creation
- ✅ Student linkage

### 6. **Routes Created**
```
GET  /mpesa/config                     → Configuration page
POST /mpesa/config/save                → Save credentials
POST /mpesa/config/test                → Test connection
POST /mpesa/stk-push                   → Initiate STK Push (from UI)
POST /mpesa/callback                   → Callback handler (from Safaricom)
GET  /mpesa/transactions               → Transaction dashboard
GET  /mpesa/transaction/<id>           → Transaction details
POST /mpesa/transaction/<id>/check-status → Check transaction status
POST /mpesa/api/stk-push               → API endpoint (external)
```

---

## 📋 Setup Instructions

### Step 1: Get Sandbox Credentials (Free Testing)

1. **Visit Safaricom Developer Portal**
   - Go to https://developer.safaricom.co.ke/
   - Create account and login

2. **Create App**
   - Click "My Apps" → "Create New App"
   - Select "Lipa Na M-PESA Online"
   - Enter app name and description
   - Submit

3. **Get Test Credentials**
   - Click on your app
   - Go to "Test Credentials" tab
   - Copy:
     - Consumer Key
     - Consumer Secret
     - Shortcode (174379 for sandbox)
     - Passkey (long alphanumeric string)

4. **Configure in System**
   - Navigate to: `/mpesa/config`
   - Environment: Sandbox
   - Paste credentials
   - Callback URL: `https://yourdomain.com/mpesa/callback` (use ngrok for local testing)
   - Enable M-PESA: ✓
   - Click "Save Configuration"
   - Click "Test Connection" to verify

### Step 2: Local Testing with Ngrok

For local development, Safaricom needs a public URL to send callbacks:

```bash
# Install ngrok
https://ngrok.com/download

# Start ngrok tunnel
ngrok http 5000

# Copy the HTTPS URL (e.g., https://abc123.ngrok.io)
# Update callback URL in M-PESA config: https://abc123.ngrok.io/mpesa/callback
```

### Step 3: Test Payment Flow

1. **Record Payment**
   - Go to: `/fees/record-payment`
   - Select a student
   - Enter amount
   - Payment Method: Select "M-PESA"
   - Enter test phone: `254708374149`
   - Click "Send M-PESA Request"

2. **Simulate Payment (Sandbox)**
   - STK Push sent ✓
   - Enter any 4-digit PIN (sandbox accepts anything)
   - Payment processed ✓
   - Callback received ✓
   - Payment auto-recorded ✓

3. **View Transaction**
   - Navigate to: `/mpesa/transactions`
   - See transaction status
   - Check payment linkage

### Step 4: Production Setup (Live Payments)

1. **Apply for M-PESA**
   - Contact: business@safaricom.co.ke or 0711 222 222
   - Apply for Lipa Na M-PESA Paybill or Till number
   - Submit KYC documents:
     - Certificate of Registration (CR12)
     - KRA PIN Certificate
     - ID copies of directors
     - Bank details

2. **Get Production Credentials**
   - Safaricom will provide:
     - Consumer Key (production)
     - Consumer Secret (production)
     - Shortcode (your Paybill/Till number)
     - Passkey (production)

3. **Update Configuration**
   - Environment: Production
   - Enter production credentials
   - Callback URL: Must be HTTPS (no ngrok in production)
   - Enable M-PESA: ✓

4. **Go Live**
   - Test with small amount first
   - Monitor transactions dashboard
   - Check callback processing
   - Verify auto-reconciliation

---

## 💰 Cost Structure

### Transaction Fees
- **Charged to**: School (not developer)
- **Rate**: 0.5% - 1.5% depending on volume
- **Paid by**: Parent/customer making payment
- **Minimum**: KES 0 (no fixed charges in sandbox)

### API Usage
- **Developer cost**: KES 0 (FREE)
- **Sandbox testing**: FREE (no real money)
- **Production**: School pays transaction fees only

### Business Model
- **Software License**: KES 100,000 - 200,000 per school per year
- **Schools supported**: Unlimited (each has own credentials)
- **API reusability**: 100% (same code, different credentials)

---

## 🔄 Payment Flow

### STK Push Flow
```
1. User clicks "Send M-PESA Request" in record payment page
   ↓
2. System calls /mpesa/stk-push with student_id, amount, phone
   ↓
3. MpesaClient.initiate_stk_push() sends request to Safaricom
   ↓
4. Safaricom sends STK Push to customer's phone
   ↓
5. Customer enters M-PESA PIN
   ↓
6. Payment processed by Safaricom
   ↓
7. Safaricom calls /mpesa/callback with result
   ↓
8. System updates transaction status (success/failed)
   ↓
9. If successful: Auto-create Payment record
   ↓
10. Payment linked to student and transaction
   ↓
11. Receipt generated automatically
```

### Callback Processing
- Webhook URL: `/mpesa/callback`
- Method: POST
- Caller: Safaricom servers
- Payload: JSON with transaction result
- Processing:
  1. Extract checkout_request_id
  2. Find transaction in database
  3. Update status based on ResultCode
  4. If success (ResultCode=0):
     - Extract M-PESA receipt number
     - Create Payment record
     - Link payment to student
     - Link payment to transaction
  5. Return acknowledgment to Safaricom

---

## 🧪 Testing

### Sandbox Test Data
- **Test Phone**: 254708374149
- **Test PIN**: Any 4 digits
- **Shortcode**: 174379
- **Amount**: Any (no real money charged)

### Test Scenarios

#### ✅ Successful Payment
```
Phone: 254708374149
Amount: 1000
PIN: 1234
Expected: Status = success, Payment created
```

#### ❌ Cancelled Payment
```
Phone: 254708374149
Amount: 500
Action: Cancel on phone
Expected: Status = cancelled, No payment
```

#### ⏰ Timeout
```
Phone: 254708374149
Amount: 200
Action: Don't enter PIN
Expected: Status = timeout after 60 seconds
```

#### 🔍 Status Check
```
1. Create pending transaction
2. Wait 2 minutes (no callback received)
3. Click "Check Status" button
4. System queries Safaricom
5. Status updated
```

---

## 📊 Database Schema

### `mpesa_config`
```sql
CREATE TABLE mpesa_config (
    id INT PRIMARY KEY AUTO_INCREMENT,
    school_id INT NULL,
    environment ENUM('sandbox', 'production') NOT NULL,
    consumer_key VARCHAR(255) NOT NULL,
    consumer_secret VARCHAR(255) NOT NULL,
    shortcode VARCHAR(20) NOT NULL,
    passkey VARCHAR(255) NOT NULL,
    is_enabled BOOLEAN DEFAULT FALSE,
    callback_url VARCHAR(255),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
```

### `mpesa_transaction`
```sql
CREATE TABLE mpesa_transaction (
    id INT PRIMARY KEY AUTO_INCREMENT,
    school_id INT NULL,
    config_id INT,
    merchant_request_id VARCHAR(100),
    checkout_request_id VARCHAR(100),
    transaction_type ENUM('stk_push', 'b2c', 'c2b') DEFAULT 'stk_push',
    phone_number VARCHAR(20) NOT NULL,
    amount DECIMAL(10, 2) NOT NULL,
    account_reference VARCHAR(50),
    transaction_desc VARCHAR(200),
    mpesa_receipt_number VARCHAR(50),
    transaction_date DATETIME,
    status ENUM('pending', 'success', 'failed', 'cancelled', 'timeout') DEFAULT 'pending',
    result_code VARCHAR(10),
    result_desc VARCHAR(255),
    student_id INT,
    payment_id INT,
    callback_received BOOLEAN DEFAULT FALSE,
    callback_data TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (config_id) REFERENCES mpesa_config(id),
    FOREIGN KEY (student_id) REFERENCES student(id),
    FOREIGN KEY (payment_id) REFERENCES payment(id),
    INDEX idx_checkout_request (checkout_request_id),
    INDEX idx_mpesa_receipt (mpesa_receipt_number),
    INDEX idx_status (status),
    INDEX idx_student (student_id)
);
```

---

## 🔐 Security Best Practices

### Credentials Storage
- ⚠️ Store consumer_secret encrypted (implement encryption in production)
- ✅ Use environment variables for sensitive data
- ✅ Restrict access to config page (admin/finance only)
- ✅ HTTPS required for production callbacks

### Callback Validation
- ✅ Verify callback source (IP whitelist)
- ✅ Validate checkout_request_id exists
- ✅ Prevent duplicate processing
- ✅ Log all callbacks for audit

### Access Control
- ✅ Login required for all M-PESA routes (except callback)
- ✅ Role-based permissions (finance staff only)
- ✅ Transaction viewing restricted by school

---

## 🐛 Troubleshooting

### Issue 1: "Failed to generate access token"
**Cause**: Invalid consumer_key or consumer_secret  
**Solution**: 
- Verify credentials in Safaricom Developer Portal
- Check environment (sandbox vs production)
- Ensure no extra spaces in credentials

### Issue 2: "Callback not received"
**Cause**: Callback URL not accessible  
**Solution**:
- Use ngrok for local testing
- Ensure HTTPS in production
- Check firewall settings
- Verify callback URL in config

### Issue 3: "Transaction stuck in pending"
**Cause**: Customer didn't complete payment or callback failed  
**Solution**:
- Click "Check Status" button
- Manually query transaction status
- Check callback logs
- Verify phone number format

### Issue 4: "Payment not auto-recorded"
**Cause**: Auto-reconciliation failed  
**Solution**:
- Check callback_data in transaction detail
- Verify student_id linkage
- Check payment table for duplicates
- Review callback processing logs

---

## 📈 Next Steps

### Immediate
- [x] Database migration applied
- [x] Models created
- [x] Client library implemented
- [x] Routes registered
- [x] UI templates created
- [x] STK Push integration
- [x] Callback handler
- [x] Transaction dashboard

### Short Term (1-2 weeks)
- [ ] Test with sandbox credentials
- [ ] Setup ngrok for callback testing
- [ ] Test STK Push flow end-to-end
- [ ] Verify auto-reconciliation
- [ ] Test status queries
- [ ] Load testing (100+ concurrent requests)

### Medium Term (1-2 months)
- [ ] Apply for production credentials
- [ ] Deploy to production server
- [ ] Setup SSL/HTTPS
- [ ] Configure production callback URL
- [ ] Go-live with pilot school
- [ ] Monitor first 100 transactions
- [ ] User training and documentation

### Long Term (3-6 months)
- [ ] Implement B2C (bulk disbursements)
- [ ] Add C2B (paybill/till payments)
- [ ] Failed payment retry mechanism
- [ ] SMS notifications for payments
- [ ] Parent portal payment history
- [ ] Transaction reconciliation reports
- [ ] Multi-currency support (USD/KES)
- [ ] Mobile app integration

---

## 📞 Support

### Safaricom Support
- Email: apisupport@safaricom.co.ke
- Phone: 0711 044 000
- Portal: https://developer.safaricom.co.ke/support

### Documentation
- API Docs: https://developer.safaricom.co.ke/Documentation
- Postman Collection: https://developer.safaricom.co.ke/APIs
- Community Forum: https://developer.safaricom.co.ke/forum

---

## ✅ Success Criteria

A successful M-PESA integration should:
1. ✅ Accept sandbox test payments
2. ✅ Receive callbacks within 60 seconds
3. ✅ Auto-create payment records
4. ✅ Link payments to students
5. ✅ Generate receipts automatically
6. ✅ Handle failed payments gracefully
7. ✅ Support multiple schools (multi-tenant)
8. ✅ Scale to 1000+ transactions/day

---

## 📝 Implementation Log

### Phase 1: Foundation (Completed)
- [x] Database schema designed
- [x] Migration created and applied
- [x] SQLAlchemy models created
- [x] Relationships defined

### Phase 2: API Client (Completed)
- [x] MpesaClient class created
- [x] Token generation implemented
- [x] STK Push request function
- [x] Status query function
- [x] Callback processing function

### Phase 3: Routes & UI (Completed)
- [x] Blueprint registered
- [x] Configuration route
- [x] STK Push route
- [x] Callback route
- [x] Transactions route
- [x] Config UI template
- [x] Transactions dashboard
- [x] Transaction detail page

### Phase 4: Integration (Completed)
- [x] M-PESA button in record payment
- [x] Phone number input field
- [x] STK Push AJAX call
- [x] Auto-reconciliation logic
- [x] Payment record creation

### Phase 5: Testing (Next)
- [ ] Sandbox testing
- [ ] Callback testing
- [ ] Status check testing
- [ ] Auto-reconciliation testing
- [ ] Multi-school testing

---

**Last Updated**: 2024-01-XX  
**Developer**: Hillview Development Team  
**Status**: ✅ Phase 4 Complete - Ready for Testing
