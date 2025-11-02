"""
Fix students who have stream_id but missing grade_id
This syncs the grade_id based on their assigned stream's grade
"""

# Direct SQL approach to fix the data
import pymysql

# Connect to database
conn = pymysql.connect(
    host='localhost',
    user='root',
    password='2078@lk//K.',
    database='hillview_demo001'
)

try:
    cursor = conn.cursor()
    
    # First, check which students have stream but no grade
    cursor.execute("""
        SELECT s.id, s.name, s.admission_number, s.stream_id, st.name as stream_name, st.grade_id, g.name as grade_name
        FROM student s
        JOIN stream st ON s.stream_id = st.id
        LEFT JOIN grade g ON st.grade_id = g.id
        WHERE s.grade_id IS NULL AND s.stream_id IS NOT NULL
    """)
    
    students_to_fix = cursor.fetchall()
    print(f"Found {len(students_to_fix)} students with stream but no grade")
    print()
    
    if students_to_fix:
        # Show first few
        for i, row in enumerate(students_to_fix[:5]):
            student_id, name, adm_no, stream_id, stream_name, grade_id, grade_name = row
            print(f"{name} (ADM: {adm_no}) - Stream: {stream_name}, Should be Grade: {grade_name}")
        
        if len(students_to_fix) > 5:
            print(f"... and {len(students_to_fix) - 5} more")
        print()
        
        # Update all students to match their stream's grade
        cursor.execute("""
            UPDATE student s
            JOIN stream st ON s.stream_id = st.id
            SET s.grade_id = st.grade_id
            WHERE s.grade_id IS NULL AND s.stream_id IS NOT NULL
        """)
        
        conn.commit()
        print(f"✅ Successfully updated {cursor.rowcount} students!")
        print("Their grade_id now matches their stream's grade.")
    else:
        print("All students have proper grade assignments!")
    
finally:
    cursor.close()
    conn.close()
