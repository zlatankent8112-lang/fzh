"""
Remove duplicate fee structures from the database
"""
import pymysql
from collections import defaultdict

# Database configuration
config = {
    'host': 'localhost',
    'user': 'root',
    'password': '2078@lk//K.',
    'database': 'hillview_demo001',
    'cursorclass': pymysql.cursors.DictCursor
}

def remove_duplicates():
    connection = pymysql.connect(**config)
    
    try:
        with connection.cursor() as cursor:
            # Get all pre-primary fees
            cursor.execute("""
                SELECT id, fee_type_name, amount, frequency, allocation_priority, 
                       education_level, term, academic_year, grade_id,
                       is_mandatory, category
                FROM fee_structure 
                WHERE education_level = 'pre_primary'
                ORDER BY fee_type_name, amount, frequency, allocation_priority, id
            """)
            
            fees = cursor.fetchall()
            print(f"Total Pre-Primary fees found: {len(fees)}")
            print("\n" + "="*80)
            
            # Group by unique characteristics
            groups = defaultdict(list)
            for fee in fees:
                # Key: everything except ID
                key = (
                    fee['fee_type_name'],
                    fee['amount'],
                    fee['frequency'],
                    fee['allocation_priority'],
                    fee['education_level'],
                    fee['term'],
                    fee['academic_year'],
                    fee['grade_id'],
                    fee['is_mandatory'],
                    fee['category']
                )
                groups[key].append(fee)
            
            # Find and remove duplicates
            duplicates_removed = 0
            for key, fee_list in groups.items():
                if len(fee_list) > 1:
                    print(f"\n🔍 Found {len(fee_list)} duplicates:")
                    print(f"   Name: {key[0]}")
                    print(f"   Amount: {key[1]}")
                    print(f"   Frequency: {key[2]}")
                    print(f"   Priority: {key[3]}")
                    
                    # Keep the first one, delete the rest
                    keep_id = fee_list[0]['id']
                    delete_ids = [f['id'] for f in fee_list[1:]]
                    
                    print(f"   ✅ Keeping ID: {keep_id}")
                    print(f"   ❌ Deleting IDs: {delete_ids}")
                    
                    for delete_id in delete_ids:
                        cursor.execute("DELETE FROM fee_structure WHERE id = %s", (delete_id,))
                        duplicates_removed += 1
            
            connection.commit()
            
            print("\n" + "="*80)
            print(f"\n✅ Successfully removed {duplicates_removed} duplicate fee structures!")
            
            # Show remaining fees
            cursor.execute("""
                SELECT id, fee_type_name, amount, frequency, allocation_priority, category
                FROM fee_structure 
                WHERE education_level = 'pre_primary'
                ORDER BY allocation_priority, fee_type_name
            """)
            
            remaining = cursor.fetchall()
            print(f"\n📊 Remaining Pre-Primary fees: {len(remaining)}")
            for fee in remaining:
                print(f"   ID {fee['id']}: {fee['fee_type_name']} - {fee['category']} - "
                      f"KES {fee['amount']:,.2f} ({fee['frequency']}) - Priority {fee['allocation_priority']}")
            
    except Exception as e:
        print(f"\n❌ Error: {e}")
        connection.rollback()
    finally:
        connection.close()

if __name__ == "__main__":
    remove_duplicates()
