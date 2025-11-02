"""
Clean up incorrect fee structures
"""
import pymysql

config = {
    'host': 'localhost',
    'user': 'root',
    'password': '2078@lk//K.',
    'database': 'hillview_demo001'
}

connection = pymysql.connect(**config)
try:
    with connection.cursor() as cursor:
        # Delete the per_term versions of Registration fee and new parent fee
        cursor.execute('DELETE FROM fee_structure WHERE id IN (13, 14)')
        connection.commit()
        print(f'✅ Deleted {cursor.rowcount} incorrect fee structures (IDs 13, 14)')
        
        # Show remaining
        cursor.execute('''
            SELECT id, fee_type_name, amount, frequency, allocation_priority, category
            FROM fee_structure 
            WHERE education_level = "pre_primary"
            ORDER BY allocation_priority, fee_type_name
        ''')
        
        remaining = cursor.fetchall()
        print(f'\n📊 Remaining Pre-Primary fees: {len(remaining)}')
        for fee in remaining:
            print(f'   ID {fee[0]}: {fee[1]} - {fee[5]} - KES {fee[2]:,.2f} ({fee[3]}) - Priority {fee[4]}')
finally:
    connection.close()
