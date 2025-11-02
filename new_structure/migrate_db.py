import pymysql

# Connect to database
connection = pymysql.connect(
    host='localhost',
    user='root',
    password='',
    database='hillview_demo001',
    charset='utf8mb4'
)

try:
    with connection.cursor() as cursor:
        # Add category column
        try:
            cursor.execute("ALTER TABLE fee_structure ADD COLUMN category VARCHAR(50) NULL")
            print("✅ Added category column")
        except Exception as e:
            print(f"⚠️  Category column: {str(e)[:100]}")
        
        # Add is_refundable column
        try:
            cursor.execute("ALTER TABLE fee_structure ADD COLUMN is_refundable BOOLEAN DEFAULT FALSE")
            print("✅ Added is_refundable column")
        except Exception as e:
            print(f"⚠️  is_refundable column: {str(e)[:100]}")
        
        # Add applies_to_grades column
        try:
            cursor.execute("ALTER TABLE fee_structure ADD COLUMN applies_to_grades TEXT NULL")
            print("✅ Added applies_to_grades column")
        except Exception as e:
            print(f"⚠️  applies_to_grades column: {str(e)[:100]}")
        
        # Update existing records with default category
        try:
            cursor.execute("""
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
            print(f"⚠️  Update categories: {str(e)[:100]}")
        
        connection.commit()
        print("\n🎉 Migration complete!")
        
finally:
    connection.close()
