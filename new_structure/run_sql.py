import pymysql

# Connect using pymysql directly
try:
    conn = pymysql.connect(
        host='localhost',
        user='root',
        password='2078@lk//K.',
        database='hillview_demo001'
    )
    
    cursor = conn.cursor()
    
    print("🔧 Connected to database. Adding columns...")
    
    # Add category column
    try:
        cursor.execute("ALTER TABLE fee_structure ADD COLUMN category VARCHAR(50) NULL")
        conn.commit()
        print("✅ Added category column")
    except pymysql.err.OperationalError as e:
        if "Duplicate column" in str(e):
            print("⏭️  category column already exists")
        else:
            print(f"❌ category: {e}")
    
    # Add is_refundable column
    try:
        cursor.execute("ALTER TABLE fee_structure ADD COLUMN is_refundable BOOLEAN DEFAULT FALSE")
        conn.commit()
        print("✅ Added is_refundable column")
    except pymysql.err.OperationalError as e:
        if "Duplicate column" in str(e):
            print("⏭️  is_refundable column already exists")
        else:
            print(f"❌ is_refundable: {e}")
    
    # Add applies_to_grades column
    try:
        cursor.execute("ALTER TABLE fee_structure ADD COLUMN applies_to_grades TEXT NULL")
        conn.commit()
        print("✅ Added applies_to_grades column")
    except pymysql.err.OperationalError as e:
        if "Duplicate column" in str(e):
            print("⏭️  applies_to_grades column already exists")
        else:
            print(f"❌ applies_to_grades: {e}")
    
    cursor.close()
    conn.close()
    
    print("\n🎉 Done! Now restart your Flask server.")
    
except pymysql.err.OperationalError as e:
    print(f"❌ Connection failed: {e}")
    print("\nTrying with different connection methods...")
    
    # Try with socket
    try:
        conn = pymysql.connect(
            unix_socket='/tmp/mysql.sock',
            user='root',
            password='',
            database='hillview_demo001'
        )
        print("✅ Connected via socket!")
        # Repeat the ALTER commands...
    except:
        print("❌ Socket connection also failed")
        print("\n📝 Please run these commands manually in MySQL Workbench:")
        print("ALTER TABLE fee_structure ADD COLUMN category VARCHAR(50) NULL;")
        print("ALTER TABLE fee_structure ADD COLUMN is_refundable BOOLEAN DEFAULT FALSE;")
        print("ALTER TABLE fee_structure ADD COLUMN applies_to_grades TEXT NULL;")
