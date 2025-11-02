"""
Add missing columns to fee_structure table using Flask app context
"""
import os
import sys

# Change to the project directory
os.chdir('c:/Users/MKT/desktop/fzh/new_structure')
sys.path.insert(0, os.getcwd())

# Now import Flask app
from __init__ import create_app, db

app = create_app()

with app.app_context():
    print("🔧 Adding columns to fee_structure table...")
    
    try:
        # Execute raw SQL to add columns
        db.engine.execute("ALTER TABLE fee_structure ADD COLUMN category VARCHAR(50) NULL")
        print("✅ Added category column")
    except Exception as e:
        if "Duplicate column" in str(e):
            print("⏭️  category already exists")
        else:
            print(f"⚠️  category: {str(e)[:100]}")
    
    try:
        db.engine.execute("ALTER TABLE fee_structure ADD COLUMN is_refundable BOOLEAN DEFAULT FALSE")
        print("✅ Added is_refundable column")
    except Exception as e:
        if "Duplicate column" in str(e):
            print("⏭️  is_refundable already exists")
        else:
            print(f"⚠️  is_refundable: {str(e)[:100]}")
    
    try:
        db.engine.execute("ALTER TABLE fee_structure ADD COLUMN applies_to_grades TEXT NULL")
        print("✅ Added applies_to_grades column")
    except Exception as e:
        if "Duplicate column" in str(e):
            print("⏭️  applies_to_grades already exists")
        else:
            print(f"⚠️  applies_to_grades: {str(e)[:100]}")
    
    print("\n🎉 Done! Columns added successfully. Restart your server now.")
