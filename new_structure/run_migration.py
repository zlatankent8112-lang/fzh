"""
Run database migration to add new fields to fee_structure table
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from extensions import db
from __init__ import create_app

app = create_app()

with app.app_context():
    try:
        # Add category column
        db.engine.execute("ALTER TABLE fee_structure ADD COLUMN IF NOT EXISTS category VARCHAR(50) NULL")
        print("✅ Added category column")
    except Exception as e:
        print(f"⚠️ Category column: {e}")
    
    try:
        # Add is_refundable column
        db.engine.execute("ALTER TABLE fee_structure ADD COLUMN IF NOT EXISTS is_refundable BOOLEAN DEFAULT FALSE")
        print("✅ Added is_refundable column")
    except Exception as e:
        print(f"⚠️ is_refundable column: {e}")
    
    try:
        # Add applies_to_grades column
        db.engine.execute("ALTER TABLE fee_structure ADD COLUMN IF NOT EXISTS applies_to_grades TEXT NULL")
        print("✅ Added applies_to_grades column")
    except Exception as e:
        print(f"⚠️ applies_to_grades column: {e}")
    
    try:
        # Update existing records with default category
        db.engine.execute("""
            UPDATE fee_structure 
            SET category = CASE 
                WHEN fee_type_name LIKE '%Tuition%' THEN 'tuition'
                WHEN fee_type_name LIKE '%Lunch%' OR fee_type_name LIKE '%Feeding%' OR fee_type_name LIKE '%Snack%' THEN 'meals'
                WHEN fee_type_name LIKE '%Transport%' THEN 'transport'
                WHEN fee_type_name LIKE '%Admission%' OR fee_type_name LIKE '%Registration%' THEN 'admission'
                WHEN fee_type_name LIKE '%Activity%' OR fee_type_name LIKE '%Sport%' THEN 'activities'
                ELSE 'tuition'
            END
            WHERE category IS NULL
        """)
        print("✅ Updated existing records with default categories")
    except Exception as e:
        print(f"⚠️ Update categories: {e}")
    
    print("\n🎉 Migration complete!")
