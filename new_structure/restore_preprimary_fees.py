"""
Restore Pre-Primary fee structures
"""
import pymysql
from datetime import datetime

config = {
    'host': 'localhost',
    'user': 'root',
    'password': '2078@lk//K.',
    'database': 'hillview_demo001'
}

# Pre-Primary fees to restore
fees = [
    {
        'name': 'Tuition,stationery and 10 oclock tea',
        'category': 'tuition',
        'amount': 26000.00,
        'frequency': 'per_term',
        'priority': 1,
        'is_mandatory': True,
        'is_refundable': False
    },
    {
        'name': 'lunch',
        'category': 'meals',
        'amount': 3000.00,
        'frequency': 'per_term',
        'priority': 2,
        'is_mandatory': True,
        'is_refundable': False
    },
    {
        'name': 'Registration fee',
        'category': 'admission',
        'amount': 5000.00,
        'frequency': 'one_time',
        'priority': 3,
        'is_mandatory': False,
        'is_refundable': False
    },
    {
        'name': 'new parent fee',
        'category': 'admission',
        'amount': 4000.00,
        'frequency': 'one_time',
        'priority': 4,
        'is_mandatory': False,
        'is_refundable': False
    }
]

connection = pymysql.connect(**config)

try:
    with connection.cursor() as cursor:
        for fee in fees:
            cursor.execute("""
                INSERT INTO fee_structure (
                    fee_type_name, category, amount, frequency, 
                    allocation_priority, is_mandatory, is_refundable,
                    academic_year, term, education_level,
                    allow_partial_payment, is_active,
                    created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
            """, (
                fee['name'],
                fee['category'],
                fee['amount'],
                fee['frequency'],
                fee['priority'],
                fee['is_mandatory'],
                fee['is_refundable'],
                '2025-2026',
                'All Terms',
                'pre_primary',
                True,
                True,
                datetime.now(),
                datetime.now()
            ))
            print(f"✅ Created: {fee['name']} - KES {fee['amount']:,.2f}")
        
        connection.commit()
        print(f"\n🎉 Successfully restored {len(fees)} Pre-Primary fee structures!")
        
except Exception as e:
    print(f"❌ Error: {e}")
    connection.rollback()
finally:
    connection.close()
