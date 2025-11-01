-- Migration: Add invoice_id to fee_reminder table
-- Date: 2025-11-01
-- Purpose: Fix schema mismatch - model expects invoice_id but table didn't have it

ALTER TABLE fee_reminder 
ADD COLUMN invoice_id INT NULL AFTER status,
ADD INDEX idx_invoice_id (invoice_id);

-- This allows fee reminders to be linked to specific invoices
