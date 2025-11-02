"""
Force delete fees and their associated student accounts
"""
import pymysql

config = {
    'host': 'localhost',
    'user': 'root',
    'password': '2078@lk//K.',
    'database': 'hillview_demo001'
}

def force_delete_all_fees():
    connection = pymysql.connect(**config)
    
    try:
        with connection.cursor() as cursor:
            # Step 1: Delete all payment allocations
            cursor.execute("DELETE FROM payment_allocation")
            deleted_allocations = cursor.rowcount
            print(f"✅ Deleted {deleted_allocations} payment allocations")
            
            # Step 2: Delete all student fee accounts
            cursor.execute("DELETE FROM student_fee_account")
            deleted_accounts = cursor.rowcount
            print(f"✅ Deleted {deleted_accounts} student fee accounts")
            
            # Step 3: Get all fee structures
            cursor.execute("SELECT id, fee_type_name FROM fee_structure")
            fees = cursor.fetchall()
            print(f"\n📋 Found {len(fees)} fee structures to delete:")
            for fee in fees:
                print(f"   - ID {fee[0]}: {fee[1]}")
            
            # Step 4: Delete all fee structures
            cursor.execute("DELETE FROM fee_structure")
            deleted_fees = cursor.rowcount
            print(f"\n✅ Deleted {deleted_fees} fee structures")
            
            connection.commit()
            
            print("\n" + "="*60)
            print("🎉 SUCCESS! All fees deleted. You can now start fresh!")
            print("="*60)
            
    except Exception as e:
        print(f"\n❌ Error: {e}")
        connection.rollback()
    finally:
        connection.close()

if __name__ == "__main__":
    print("⚠️  WARNING: This will delete ALL fee structures and related data!")
    print("="*60)
    confirm = input("Type 'DELETE ALL' to confirm: ")
    
    if confirm == "DELETE ALL":
        force_delete_all_fees()
    else:
        print("\n❌ Cancelled. No changes made.")
