# 📱 SMS & Email Notification Setup Guide

## Hillview School Management System

This guide will help you configure SMS and Email notifications for M-PESA payment alerts.

---

## 📋 Table of Contents

1. [Overview](#overview)
2. [SMS Setup - Africa's Talking](#sms-setup---africas-talking)
3. [SMS Setup - Twilio (Alternative)](#sms-setup---twilio-alternative)
4. [Email Setup - Gmail](#email-setup---gmail)
5. [Email Setup - Other Providers](#email-setup---other-providers)
6. [Testing Your Setup](#testing-your-setup)
7. [Going to Production](#going-to-production)
8. [Troubleshooting](#troubleshooting)

---

## 🎯 Overview

The system supports multiple notification channels:

- **SMS**: Africa's Talking or Twilio
- **Email**: Any SMTP provider (Gmail, Office 365, SendGrid, etc.)
- **Test Mode**: Logs notifications to console (for development)

### Current Status

✅ **Structure Complete** - All code is ready  
⏳ **Credentials Needed** - Just plug in your API keys  
🚀 **Plug & Play** - No code changes required

---

## 📱 SMS Setup - Africa's Talking

### Why Africa's Talking?

- ✅ Best for Kenya & Africa
- ✅ Competitive rates (KES 0.80 per SMS)
- ✅ Reliable delivery
- ✅ Free sandbox for testing

### Step 1: Create Account

1. Go to https://africastalking.com/
2. Click **"Sign Up"**
3. Verify your email
4. Choose Kenya as your country

### Step 2: Get Credentials

#### Sandbox (For Testing)

1. Login to https://account.africastalking.com/
2. Go to **Apps** > **Sandbox**
3. Note your credentials:
   - **Username**: `sandbox`
   - **API Key**: Click "Settings" > "API Key" to reveal

#### Production (Real SMS)

1. Go to **Apps** > **Production**
2. Complete business verification (KYC)
3. Add funds to your account
4. Get your production credentials:
   - **Username**: Your business name (e.g., `hillviewschool`)
   - **API Key**: From Settings > API Key

### Step 3: Register Sender ID

1. Go to **SMS** > **Sender IDs**
2. Click **"Request Sender ID"**
3. Enter your sender name (e.g., `HILLVIEW`)
   - Max 11 characters
   - Alphanumeric only
   - Must be approved by Africa's Talking
4. Wait for approval (usually 24-48 hours)

### Step 4: Add Credentials to .env

```bash
# Set provider to africas_talking
SMS_PROVIDER=africas_talking

# Add your credentials
AFRICAS_TALKING_USERNAME=sandbox  # or your production username
AFRICAS_TALKING_API_KEY=your_actual_api_key_here
AFRICAS_TALKING_SENDER_ID=HILLVIEW  # or your approved sender ID
```

### Step 5: Install SDK

```bash
pip install africastalking
```

### Pricing (Kenya)

- Standard SMS: KES 0.80 per message
- Premium SMS: KES 2.00 per message
- Free tier: KES 200 credit on first deposit

---

## 📱 SMS Setup - Twilio (Alternative)

### Why Twilio?

- ✅ Global coverage
- ✅ Enterprise-grade
- ✅ Free trial credits ($15)
- ❌ Higher cost for Kenya

### Step 1: Create Account

1. Go to https://www.twilio.com/try-twilio
2. Sign up and verify your phone number
3. Get free trial credits ($15)

### Step 2: Get Phone Number

1. Login to Twilio Console
2. Go to **Phone Numbers** > **Buy a Number**
3. Select Kenya (+254) or any country
4. Buy a number (costs from $1/month)

### Step 3: Get Credentials

1. Go to Twilio Console Dashboard
2. Note these values:
   - **Account SID**: AC... (long string)
   - **Auth Token**: Click to reveal

### Step 4: Add Credentials to .env

```bash
# Set provider to twilio
SMS_PROVIDER=twilio

# Add your credentials
TWILIO_ACCOUNT_SID=your_account_sid_here
TWILIO_AUTH_TOKEN=your_auth_token_here
TWILIO_PHONE_NUMBER=+1234567890  # Your Twilio number
```

### Step 5: Install SDK

```bash
pip install twilio
```

### Pricing

- SMS to Kenya: ~$0.03 (KES 4.50) per message
- Free trial: $15 credit

---

## 📧 Email Setup - Gmail

### Why Gmail?

- ✅ Free and reliable
- ✅ Easy to set up
- ✅ 500 emails/day limit (enough for most schools)

### Step 1: Create School Gmail Account

1. Go to https://accounts.google.com/signup
2. Create account: `parentportal@hillviewschool.com` (or similar)
3. Complete setup

### Step 2: Enable 2-Factor Authentication

1. Go to https://myaccount.google.com/security
2. Click **"2-Step Verification"**
3. Follow prompts to enable
4. ⚠️ **Required for app passwords**

### Step 3: Generate App Password

1. Go to https://myaccount.google.com/apppasswords
2. Select app: **"Mail"**
3. Select device: **"Other"**
4. Enter name: **"Hillview SMS"**
5. Click **"Generate"**
6. Copy the 16-character password (format: `xxxx xxxx xxxx xxxx`)
7. ⚠️ **Save this password - you can't see it again!**

### Step 4: Add Credentials to .env

```bash
# Gmail SMTP Settings
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=True

# Your credentials
MAIL_USERNAME=parentportal@hillviewschool.com
MAIL_PASSWORD=your_16_character_app_password_here

# Sender information
MAIL_DEFAULT_SENDER=noreply@hillviewschool.com
MAIL_REPLY_TO=info@hillviewschool.com
```

### Limits

- **500 emails/day** for free Gmail accounts
- **2000 emails/day** for Google Workspace accounts

---

## 📧 Email Setup - Other Providers

### Office 365 / Outlook.com

```bash
MAIL_SERVER=smtp.office365.com
MAIL_PORT=587
MAIL_USE_TLS=True
MAIL_USERNAME=your-email@outlook.com
MAIL_PASSWORD=your_password
```

### Yahoo Mail

```bash
MAIL_SERVER=smtp.mail.yahoo.com
MAIL_PORT=587
MAIL_USE_TLS=True
MAIL_USERNAME=your-email@yahoo.com
MAIL_PASSWORD=your_app_password
```

Note: Yahoo also requires app passwords

### SendGrid (Recommended for High Volume)

```bash
MAIL_SERVER=smtp.sendgrid.net
MAIL_PORT=587
MAIL_USE_TLS=True
MAIL_USERNAME=apikey
MAIL_PASSWORD=your_sendgrid_api_key
```

- Free tier: 100 emails/day
- Paid: From $15/month (40,000 emails)

### Mailgun

```bash
MAIL_SERVER=smtp.mailgun.org
MAIL_PORT=587
MAIL_USE_TLS=True
MAIL_USERNAME=postmaster@your-domain.mailgun.org
MAIL_PASSWORD=your_mailgun_password
```

---

## 🧪 Testing Your Setup

### Step 1: Install Validation Tool Dependencies

```bash
pip install python-dotenv colorama
```

### Step 2: Run Validation Script

```bash
python validate_notifications.py
```

This will check:

- ✅ `.env` file exists
- ✅ All required credentials are present
- ✅ SMS provider is correctly configured
- ✅ Email SMTP connection works
- ✅ School information is set

### Step 3: Test SMS (Interactive)

```bash
python validate_notifications.py --test-sms
```

You'll be prompted:

```
Enter phone number (format: 254712345678): 254712345678
Enter test message (or press Enter for default):
```

### Step 4: Test Email (Interactive)

```bash
python validate_notifications.py --test-email
```

You'll be prompted:

```
Enter email address: your-email@example.com
```

### Step 5: Test Through Web Interface

1. Start your server: `python run.py`
2. Login as admin
3. Go to: http://127.0.0.1:8080/mpesa/test-notification
4. Enter phone number and email
5. Click "Send Test Notification"

---

## 🚀 Going to Production

### Checklist

#### 1. Update SMS Provider

```bash
# Change from test to real provider
SMS_PROVIDER=africas_talking  # or twilio
```

#### 2. Add Real Credentials

```bash
# Africa's Talking Production
AFRICAS_TALKING_USERNAME=hillviewschool
AFRICAS_TALKING_API_KEY=your_production_api_key

# OR Twilio
TWILIO_ACCOUNT_SID=your_production_sid
TWILIO_AUTH_TOKEN=your_production_token
```

#### 3. Configure Email

```bash
MAIL_USERNAME=notifications@hillviewschool.com
MAIL_PASSWORD=your_app_specific_password
```

#### 4. Set School Information

```bash
SCHOOL_NAME=Hillview Academy
SCHOOL_PHONE=+254700123456
SCHOOL_EMAIL=info@hillviewschool.com
SCHOOL_ADDRESS=P.O. Box 12345, Nairobi, Kenya
```

#### 5. Enable Notifications

```bash
ENABLE_SMS_NOTIFICATIONS=True
ENABLE_EMAIL_NOTIFICATIONS=True
NOTIFY_ON_PAYMENT_SUCCESS=True
NOTIFY_ON_PAYMENT_FAILURE=False
NOTIFY_ON_PAYMENT_TIMEOUT=False
```

#### 6. Test in Production

1. Make a real M-PESA payment (small amount)
2. Verify SMS/Email received
3. Check logs for any errors
4. Monitor for 24 hours

---

## 🔧 Troubleshooting

### SMS Issues

#### Error: "africastalking module not found"

```bash
pip install africastalking
```

#### Error: "Invalid API Key"

- Check username and API key are correct
- Ensure no extra spaces in .env file
- Sandbox vs Production mismatch?

#### Error: "Sender ID not approved"

- Use default `AFRICASTALK` while waiting for approval
- Or leave blank to use random number

#### SMS Not Received

- Check phone number format (254712345678, no +)
- Ensure you have airtime/credit
- Check network signal
- Sandbox: Only works with registered test numbers

### Email Issues

#### Error: "SMTP Authentication failed"

**Gmail:**

- Are you using app password (not regular password)?
- Is 2FA enabled?
- Generate new app password and try again

**Other Providers:**

- Check username/password
- Verify SMTP server and port
- Check if account is locked

#### Error: "Connection timeout"

- Check firewall settings
- Verify MAIL_SERVER and MAIL_PORT
- Try different port (465 for SSL)

#### Emails Going to Spam

- Add SPF record to DNS
- Set up DKIM
- Use proper "From" address
- Don't use spammy words in subject

### General Issues

#### Test Mode Not Working

```bash
# Ensure test mode is set
SMS_PROVIDER=test
```

#### No Notifications Sent

Check:

1. `ENABLE_SMS_NOTIFICATIONS=True`
2. `ENABLE_EMAIL_NOTIFICATIONS=True`
3. `NOTIFY_ON_PAYMENT_SUCCESS=True`
4. Check logs: `tail -f logs/hillview.log`

#### Notifications Delayed

- Africa's Talking: Usually instant
- Twilio: Usually instant
- Email: Can take 30 seconds to 5 minutes
- Check provider status page

---

## 💡 Quick Reference

### File Structure

```
new_structure/
├── .env                          # Your credentials (DO NOT COMMIT!)
├── .env.example                  # Template with all variables
├── config.py                     # Loads environment variables
├── validate_notifications.py     # Testing tool
└── utils/
    └── notification_service.py   # Main notification code
```

### Testing Commands

```bash
# Validate configuration
python validate_notifications.py

# Test SMS
python validate_notifications.py --test-sms

# Test Email
python validate_notifications.py --test-email

# Check logs
tail -f logs/hillview.log
```

### Important URLs

- Africa's Talking: https://account.africastalking.com/
- Twilio Console: https://console.twilio.com/
- Gmail App Passwords: https://myaccount.google.com/apppasswords
- SendGrid: https://sendgrid.com/

---

## 📞 Support

Need help? Check:

1. This guide first
2. Validation script output
3. Application logs: `logs/hillview.log`
4. Provider documentation:
   - Africa's Talking: https://developers.africastalking.com/
   - Twilio: https://www.twilio.com/docs/
   - Gmail: https://support.google.com/mail/

---

**✅ Setup Complete!** Your notification system is now ready for production use.

When you get your credentials, just:

1. Edit `.env` file
2. Add your keys
3. Run `python validate_notifications.py`
4. Test and deploy! 🚀
