"""
Quick script to add missing columns to fee_structure table
Run this with: python add_columns.py
"""
from sqlalchemy import create_engine, text

# Database connection
engine = create_engine('mysql+pymysql://root:@localhost/hillview_demo001')

with engine.connect() as conn:
    # Add category column
    try:
        conn.execute(text("ALTER TABLE fee_structure ADD COLUMN category VARCHAR(50) NULL"))
        conn.commit()
        print("✅ Added category column")
    except Exception as e:
        if "Duplicate column" in str(e):
            print("⏭️  category column already exists")
        else:
            print(f"❌ Error adding category: {e}")
    
    # Add is_refundable column  
    try:
        conn.execute(text("ALTER TABLE fee_structure ADD COLUMN is_refundable BOOLEAN DEFAULT FALSE"))
        conn.commit()
        print("✅ Added is_refundable column")
    except Exception as e:
        if "Duplicate column" in str(e):
            print("⏭️  is_refundable column already exists")
        else:
            print(f"❌ Error adding is_refundable: {e}")
    
    # Add applies_to_grades column
    try:
        conn.execute(text("ALTER TABLE fee_structure ADD COLUMN applies_to_grades TEXT NULL"))
        conn.commit()
        print("✅ Added applies_to_grades column")
    except Exception as e:
        if "Duplicate column" in str(e):
            print("⏭️  applies_to_grades column already exists")
        else:
            print(f"❌ Error adding applies_to_grades: {e}")

print("\n🎉 Database schema updated!")
