from extensions import db
from models.student import Student
from app import create_app

app = create_app()
with app.app_context():
    # Get all students
    students = Student.query.all()
    print(f'Total students in database: {len(students)}')
    print()
    
    # Check students with 'No Grade'
    no_grade = Student.query.filter((Student.grade == None) | (Student.grade == '') | (Student.grade == 'No Grade')).all()
    print(f'Students with no grade or "No Grade": {len(no_grade)}')
    if no_grade:
        print('Sample students with no grade:')
        for s in no_grade[:10]:
            print(f'  - {s.full_name} (ADM: {s.admission_number}, Grade: "{s.grade}", Stream: "{s.stream}")')
    print()
    
    # Check Grade 9 Stream G students
    grade9g = Student.query.filter_by(grade='Grade 9', stream='G').all()
    print(f'Students in Grade 9 Stream G: {len(grade9g)}')
    if grade9g:
        print('First 5 Grade 9 G students:')
        for s in grade9g[:5]:
            print(f'  - {s.full_name} (ADM: {s.admission_number})')
    print()
    
    # Check all unique grades
    from sqlalchemy import distinct
    grades = db.session.query(distinct(Student.grade)).all()
    print(f'Unique grades in database: {len(grades)}')
    for g in sorted([gr[0] for gr in grades if gr[0]]):
        count = Student.query.filter_by(grade=g).count()
        print(f'  - "{g}": {count} students')
    print()
    
    # Search for the specific students mentioned
    print("Searching for specific students mentioned:")
    peter_students = Student.query.filter(Student.full_name.like('%PETER%')).all()
    for s in peter_students:
        print(f'  - {s.full_name} (ADM: {s.admission_number}, Grade: "{s.grade}", Stream: "{s.stream}")')
