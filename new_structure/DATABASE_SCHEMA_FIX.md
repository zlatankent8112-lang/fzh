# Database Schema Fix: password_hash Column

## Issue

When trying to create a new teacher/secretary/accountant via the manage_teachers interface, the following error occurred:

```
pymysql.err.OperationalError: (1364, "Field 'password_hash' doesn't have a default value")
```

## Root Cause

- The database table `teacher` has a legacy `password_hash` column with NOT NULL constraint
- The `Teacher` model in `models/user.py` only defined the `password` column
- When SQLAlchemy performed an INSERT, it didn't provide a value for `password_hash`, causing MySQL to reject the query

## Solution Applied

Updated `models/user.py` to maintain backward compatibility with the existing database schema:

### 1. Added password_hash Column Definition

```python
# Canonical password column
password = db.Column(db.String(255), nullable=False)
# Legacy column that still exists in database (keep for backward compatibility)
password_hash = db.Column(db.String(255), nullable=False)
```

### 2. Updated set_password() Method

```python
def set_password(self, password):
    hashed = generate_password_hash(pwd)
    self.password = hashed
    # Also set password_hash for backward compatibility with existing database schema
    self.password_hash = hashed
```

## How It Works

- Both `password` and `password_hash` columns store the same hashed password
- This maintains compatibility with the existing database schema
- No database migration required
- Existing teacher accounts continue to work
- New accounts can now be created successfully

## Testing Steps

1. Go to **Manage Teachers** interface
2. Click **Add New Teacher**
3. Fill in the form:
   - Username: `kevink`
   - First Name: `Kevin`
   - Last Name: `Kamau`
   - Role: **Accountant**
   - Employee ID: `EMP012`
   - Password: Choose a secure password
4. Click **Add Teacher**
5. Should see success message: "Teacher added successfully!"

## Files Modified

- `models/user.py`:
  - Line 23: Added `password_hash` column definition
  - Line 82-83: Updated `set_password()` to set both columns

## Future Improvements

Consider these options for long-term cleanup:

### Option A: Keep Both Columns (Current Solution)

- **Pros**: No migration needed, backward compatible
- **Cons**: Redundant data storage

### Option B: Database Migration to Drop password_hash

Create an Alembic migration:

```python
def upgrade():
    op.drop_column('teacher', 'password_hash')

def downgrade():
    op.add_column('teacher', sa.Column('password_hash', sa.String(255), nullable=False))
```

### Option C: Make password_hash Nullable

```sql
ALTER TABLE teacher MODIFY COLUMN password_hash VARCHAR(255) NULL;
```

## Recommendation

**Keep the current solution** (Option A) because:

- It works immediately without database changes
- No risk of breaking existing accounts
- Simple rollback if needed
- Can migrate later when convenient

## Related Documentation

- See `FEE_STAFF_ACCESS_GUIDE.md` for adding secretary/accountant users
- See `AUTHENTICATION_FIX.md` for authentication system overview
