# Individual Report Synchronization Fix

## Problem
The individual student report PDF showed different marks and totals compared to the class report preview for the same student. For example:
- **Class Report**: AARON KIWELU GIFORO - Total: 475/900, Mean: 52.86%, ME2
- **Individual Report**: Same student - Total: 581/900, Mean: 52.83%, ME2

Subject marks also differed between the two reports.

## Root Cause
The `generate_student_report_pdf_bytes()` function was using two different data sources:
1. **First**: Called `get_class_report_data()` which returned unfiltered/non-composite processed data
2. **Then**: Called `ClassReportBuilder.build()` but only used `filtered_marks` from it, while still using totals and averages from the first call

This created a disconnect because:
- The preview (`preview_class_report`) uses ONLY `ClassReportBuilder.build()` which applies:
  - Subject filtering based on `session['selected_subjects']`
  - Composite subject aggregation
  - Proper mark calculations
  
- The PDF generation was mixing data from both sources, causing:
  - Wrong totals (using unfiltered total instead of `filtered_total`)
  - Wrong averages (using `average_percentage` instead of `filtered_average`)
  - Potential subject ordering/mapping issues

## Solution
Modified `generate_student_report_pdf_bytes()` to use **ONLY** `ClassReportBuilder.build()` as the single source of truth, exactly like the preview:

### Key Changes:

1. **Removed duplicate `get_class_report_data()` call**
   - Previously called both `get_class_report_data()` and `ClassReportBuilder.build()`
   - Now calls only `ClassReportBuilder.build()`

2. **Use filtered data from builder**
   - `filtered_total` instead of manually calculating from marks
   - `filtered_average` instead of `average_percentage` from old call
   - `filtered_marks` dictionary (already was using this, but now consistent)

3. **Extract student data from builder context**
   - `student_filtered` extracted from `builder_ctx['class_data']`
   - All calculations use data from this single source

4. **Ensure same subject filtering**
   - Uses `session.get('selected_subjects', [])` same as preview
   - Same subject filtering logic applied

## Code Changes

### Before:
```python
# Called get_class_report_data first
class_data_result = get_class_report_data(...)
student_data = class_data_result["class_data"][...]
avg_percentage = student_data.get("average_percentage", 0)

# Then called builder but mixed data sources
builder_ctx = ClassReportBuilder.build(...)
filtered_marks = student_filtered.get('filtered_marks', {})
total_marks = sum(mark for mark in filtered_marks.values())  # Manual calculation
```

### After:
```python
# Use ONLY ClassReportBuilder (single source of truth)
builder_ctx = ClassReportBuilder.build(...)
student_filtered = builder_ctx['class_data'][...]  # Extract from builder

# Use filtered data from builder
filtered_marks = student_filtered.get('filtered_marks', {})
filtered_total = student_filtered.get('filtered_total', 0)  # From builder
filtered_average = student_filtered.get('filtered_average', 0)  # From builder
total_marks = filtered_total  # Use builder's calculation
avg_percentage = filtered_average  # Use builder's calculation
```

## Result
The individual report PDF now shows **exactly the same** marks, totals, and averages as the class report preview because both use the identical data processing pipeline (`ClassReportBuilder.build()`).

## Testing
To verify the fix:
1. View class report preview: `/classteacher/preview_class_report/Grade%209/Stream%20G/term%203/midterm%203%202025`
2. Note marks and totals for a student (e.g., AARON KIWELU GIFORO)
3. Download individual report for the same student
4. Compare - marks, totals, and averages should match exactly

## Files Modified
- `new_structure/views/classteacher.py` - `generate_student_report_pdf_bytes()` function

## Related Documentation
- `CLASS_INDIVIDUAL_REPORT_SYNC_FIX.md` - Previous fix attempt (partial)
- `services/class_report_builder.py` - The unified report builder service

