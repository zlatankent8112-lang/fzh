-- Migration: Enhance Fee Structure with Category and Frequency
-- Date: 2025-11-02
-- Description: Add category, frequency changes, is_refundable, and applies_to_grades fields

-- Add category field for fee grouping
ALTER TABLE fee_structure 
ADD COLUMN category VARCHAR(50) NULL COMMENT 'Fee category: tuition, meals, transport, remedial, admission, caution, admin, activities';

-- Update frequency field default value
ALTER TABLE fee_structure 
MODIFY COLUMN frequency VARCHAR(20) DEFAULT 'per_term' COMMENT 'per_term, annual, one_time, monthly';

-- Add is_refundable field for deposits/caution fees
ALTER TABLE fee_structure 
ADD COLUMN is_refundable BOOLEAN DEFAULT FALSE COMMENT 'Whether this fee can be refunded (e.g., caution fees)';

-- Add applies_to_grades for conditional grade application
ALTER TABLE fee_structure 
ADD COLUMN applies_to_grades TEXT NULL COMMENT 'JSON array of grade IDs: "[1,2,3]" for conditional application';

-- Update existing records to have default category
UPDATE fee_structure 
SET category = CASE 
    WHEN fee_type_name LIKE '%Tuition%' THEN 'tuition'
    WHEN fee_type_name LIKE '%Lunch%' OR fee_type_name LIKE '%Feeding%' OR fee_type_name LIKE '%Snack%' THEN 'meals'
    WHEN fee_type_name LIKE '%Transport%' THEN 'transport'
    WHEN fee_type_name LIKE '%Admission%' OR fee_type_name LIKE '%Registration%' THEN 'admission'
    WHEN fee_type_name LIKE '%Activity%' OR fee_type_name LIKE '%Sport%' THEN 'activities'
    ELSE 'tuition'
END
WHERE category IS NULL;

-- Update existing records to have default frequency
UPDATE fee_structure 
SET frequency = CASE 
    WHEN fee_type_name LIKE '%Admission%' OR fee_type_name LIKE '%Registration%' OR fee_type_name LIKE '%Caution%' THEN 'one_time'
    WHEN term IS NULL OR term = '' THEN 'annual'
    ELSE 'per_term'
END
WHERE frequency = 'termly' OR frequency IS NULL;

COMMIT;
