"""Fix mpesa_transaction table by adding missing columns"""
import sys
sys.path.insert(0, 'c:/Users/MKT/desktop/fzh')
from new_structure import create_app
from new_structure.extensions import db
from sqlalchemy import text

app = create_app()

with app.app_context():
    try:
        # Add school_id column if it doesn't exist
        with db.engine.connect() as conn:
            conn.execute(text("""
                ALTER TABLE mpesa_transaction 
                ADD COLUMN school_id INT NULL AFTER id
            """))
            conn.commit()
        print("✓ Added school_id column")
    except Exception as e:
        if 'Duplicate column' in str(e):
            print("✓ school_id column already exists")
        else:
            print(f"Error with school_id: {e}")

    try:
        # Add config_id column if it doesn't exist
        with db.engine.connect() as conn:
            conn.execute(text("""
                ALTER TABLE mpesa_transaction 
                ADD COLUMN config_id INT NULL AFTER school_id
            """))
            conn.commit()
        print("✓ Added config_id column")
    except Exception as e:
        if 'Duplicate column' in str(e):
            print("✓ config_id column already exists")
        else:
            print(f"Error with config_id: {e}")

    print("\n✓ Table fix completed!")
