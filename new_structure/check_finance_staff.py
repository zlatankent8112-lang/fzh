from new_structure import create_app
from new_structure.models.user import Teacher

app = create_app()
app.app_context().push()

# Check for accountant
accountant = Teacher.query.filter_by(role='accountant').first()
print("Accountant:")
if accountant:
    print(f"  ID: {accountant.id}")
    print(f"  Username: {accountant.username}")
    print(f"  Name: {accountant.first_name} {accountant.last_name}")
    print(f"  Active: {accountant.is_active}")
else:
    print("  None found")

print()

# Check for secretary
secretary = Teacher.query.filter_by(role='secretary').first()
print("Secretary:")
if secretary:
    print(f"  ID: {secretary.id}")
    print(f"  Username: {secretary.username}")
    print(f"  Name: {secretary.first_name} {secretary.last_name}")
    print(f"  Active: {secretary.is_active}")
else:
    print("  None found")

print()

# List all teachers with their roles
print("All Teachers:")
teachers = Teacher.query.all()
for teacher in teachers:
    print(f"  {teacher.id}: {teacher.first_name} {teacher.last_name} ({teacher.username}) - Role: {teacher.role}, Active: {teacher.is_active}")
