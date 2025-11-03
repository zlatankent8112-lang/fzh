"""
Assess the fee structures that have been created
"""
import pymysql

conn = pymysql.connect(
    host='localhost',
    user='root',
    password='2078@lk//K.',
    database='hillview_demo001'
)

cursor = conn.cursor()

print('=== FEE STRUCTURES ASSESSMENT ===\n')

# Get all fee structures
cursor.execute('''
    SELECT 
        fs.id,
        fs.fee_type_name,
        fs.amount,
        fs.education_level,
        fs.category,
        fs.frequency,
        fs.academic_year,
        fs.term,
        fs.allocation_priority,
        fs.is_mandatory,
        fs.allow_partial_payment,
        fs.is_refundable,
        fs.is_active,
        g.name as grade_name
    FROM fee_structure fs
    LEFT JOIN grade g ON fs.grade_id = g.id
    ORDER BY fs.education_level, fs.allocation_priority, fs.fee_type_name
''')

structures = cursor.fetchall()
print(f'Total Fee Structures: {len(structures)}\n')

# Group by education level
by_level = {}
for row in structures:
    level = row[3] or 'No Level'
    if level not in by_level:
        by_level[level] = []
    by_level[level].append(row)

# Display by education level
for level in sorted(by_level.keys()):
    fees = by_level[level]
    total_amount = sum(f[2] for f in fees)
    print(f'\n📚 {level.upper().replace("_", " ")}')
    print(f'   Total Fees: {len(fees)} | Total Amount: KES {total_amount:,.2f}')
    print(f'   {"="*70}')
    
    for fee in fees:
        fee_id, name, amount, edu_level, category, freq, year, term, priority, mandatory, partial, refund, active, grade = fee
        status = '✅' if active else '❌'
        mand = 'MANDATORY' if mandatory else 'Optional'
        part = 'Partial OK' if partial else 'Full Payment'
        
        print(f'   {status} [{priority}] {name}')
        print(f'      Amount: KES {amount:,.2f} | {category} | {freq}')
        print(f'      {mand} | {part} | Year: {year} | Term: {term or "All Terms"}')
        if grade:
            print(f'      Specific Grade: {grade}')
        print()

# Get statistics
cursor.execute('SELECT COUNT(*) FROM fee_structure WHERE is_active = 1')
active_count = cursor.fetchone()[0]

cursor.execute('SELECT COUNT(*) FROM fee_structure WHERE is_mandatory = 1')
mandatory_count = cursor.fetchone()[0]

cursor.execute('SELECT SUM(amount) FROM fee_structure WHERE is_active = 1 AND education_level = "pre_primary"')
pp_total = cursor.fetchone()[0] or 0

cursor.execute('SELECT SUM(amount) FROM fee_structure WHERE is_active = 1 AND education_level = "upper_primary"')
up_total = cursor.fetchone()[0] or 0

cursor.execute('SELECT SUM(amount) FROM fee_structure WHERE is_active = 1 AND education_level = "junior_secondary"')
js_total = cursor.fetchone()[0] or 0

print(f'\n📊 SUMMARY STATISTICS')
print(f'   {"="*70}')
print(f'   Total Structures: {len(structures)}')
print(f'   Active: {active_count} | Mandatory: {mandatory_count}')
print(f'   Pre-Primary Total: KES {pp_total:,.2f}')
print(f'   Upper Primary Total: KES {up_total:,.2f}')
print(f'   Junior Secondary Total: KES {js_total:,.2f}')

# Check for best practices
print(f'\n✨ BEST PRACTICES CHECK')
print(f'   {"="*70}')

# Check allocation priorities
cursor.execute('''
    SELECT education_level, COUNT(DISTINCT allocation_priority) as priority_count
    FROM fee_structure
    WHERE is_active = 1
    GROUP BY education_level
''')
for level, count in cursor.fetchall():
    print(f'   {level}: {count} different priority levels')

# Check frequency distribution
cursor.execute('''
    SELECT frequency, COUNT(*) as count
    FROM fee_structure
    WHERE is_active = 1
    GROUP BY frequency
''')
print(f'\n   Frequency Distribution:')
for freq, count in cursor.fetchall():
    print(f'      {freq}: {count} fees')

# Check categories
cursor.execute('''
    SELECT category, COUNT(*) as count, SUM(amount) as total
    FROM fee_structure
    WHERE is_active = 1
    GROUP BY category
''')
print(f'\n   Category Breakdown:')
for cat, count, total in cursor.fetchall():
    print(f'      {cat}: {count} fees (KES {total:,.2f})')

cursor.close()
conn.close()
