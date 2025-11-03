"""
Fix students who have stream_id but missing grade_id
This syncs the grade_id based on their assigned stream's grade
"""
import sys
sys.path.insert(0, '.')

from new_structure.extensions import db
from new_structure.models.academic import Student, Stream, Grade
from new_structure.app import create_app

app = create_app()
with app.app_context():
    # Find all students with stream_id but no grade_id
    students_missing_grade = Student.query.filter(
        Student.stream_id.isnot(None),
        Student.grade_id.is_(None)
    ).all()
    
    print(f"Found {len(students_missing_grade)} students with stream but no grade")
    print()
    
    if students_missing_grade:
        updated_count = 0
        for student in students_missing_grade:
            stream = Stream.query.get(student.stream_id)
            if stream and stream.grade_id:
                old_grade_id = student.grade_id
                student.grade_id = stream.grade_id
                
                grade = Grade.query.get(stream.grade_id)
                grade_name = grade.name if grade else "Unknown"
                
                print(f"Updating {student.name} (ADM: {student.admission_number})")
                print(f"  Stream: {stream.name} (ID: {stream.id})")
                print(f"  Setting grade_id: {old_grade_id} -> {stream.grade_id} ({grade_name})")
                updated_count += 1
        
        if updated_count > 0:
            db.session.commit()
            print()
            print(f"✅ Successfully updated {updated_count} students!")
            print("Their grade_id now matches their stream's grade.")
        else:
            print("No students needed updating.")
    else:
        print("All students have proper grade assignments!")
