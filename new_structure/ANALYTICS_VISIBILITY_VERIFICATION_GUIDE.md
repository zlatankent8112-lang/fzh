# Visual Verification Guide - Analytics Visibility Fix

## How to Verify the Fix

### 1. Navigate to the Headteacher Dashboard

- URL: `http://127.0.0.1:8080/headteacher/`
- Scroll down to the **Advanced Analytics** section

### 2. Performance Heatmap Verification

#### What to Check:

✅ **Grade & Stream labels** (e.g., "Grade 9 G") should be clearly visible in light color
✅ **Percentage values** (e.g., "52.1%") should be large, bold, and readable
✅ **Subtitle text** (e.g., "Class Average • 43 students") should be visible but slightly less prominent
✅ **Background colors** should vary based on performance (gradient from red/orange to yellow/green)
✅ **Hover effect** should lift the tile slightly with a shadow

#### Expected Colors:

- Text: Light cream/beige (`#fdf6e3`)
- Values: Large, bold, light cream with text shadow
- Background: Semi-transparent colored tiles based on performance percentage

### 3. Detailed Analytics Section

#### What to Check:

✅ **Section title** "Detailed Analytics" should be bright and readable
✅ **Subtitle** "Click on any chart element above for detailed analysis" should be visible
✅ **Container background** should be slightly lighter than the page background
✅ **All metrics** should be clearly visible

#### When Clicking a Heatmap Tile:

✅ **Class name heading** (e.g., "Grade 9 G") should appear in large, light text
✅ **Metric labels** should use `<strong>` tags and be clearly visible:

- Class Average: XX.X%
- Mean Score: XXX.XX
- Excellence Rate: X.X%
- Mastery Rate: XX.X%
- Approaching Rate: XX.X%
- Below Expectation Rate: X.X%
- Students: XX

### 4. Performance Alerts Section

#### What to Check:

✅ **Alert cards** should have colored left borders (green/yellow/red)
✅ **Alert text** should be clearly readable in light color
✅ **"No alerts" message** should be visible if no alerts exist
✅ **Refresh button** should be visible and functional

### 5. Dropdown Selector

#### What to Check:

✅ **Dropdown** for "Class Average", "Excellence Rate", "Mastery Rate" should be:

- Visible with light text
- Readable on hover
- Functional when clicked
  ✅ **Options** in dropdown should have proper contrast

### 6. Interactive Elements

#### Hover States:

- Heatmap tiles should lift up slightly (`translateY(-2px)`)
- Analytics cards should lift up slightly
- Alert cards should shift right slightly (`translateX(4px)`)
- Shadows should increase on hover

#### Click States:

- Clicking a heatmap tile should update the Detailed Analytics section below
- The section should scroll into view smoothly

## Before vs After Comparison

### BEFORE (Issues):

```
Performance Heatmap:
┌─────────────────────┐
│ [DARK TEXT]         │ ← Nearly invisible
│ [DARK NUMBER]       │ ← Can't read percentage
│ [DARK SUBTITLE]     │ ← Can't read details
└─────────────────────┘

Detailed Analytics:
"Grade 9 G" ← Dark text, invisible
• Class Average: 52.1% ← Dark text, invisible
• Mean Score: 469.13 ← Dark text, invisible
```

### AFTER (Fixed):

```
Performance Heatmap:
┌─────────────────────┐
│ Grade 9 G           │ ← Light, clear text
│ 52.1%              │ ← Large, bold, visible
│ Class Average • 43  │ ← Clear subtitle
└─────────────────────┘
     ↓ (hover effect)
┌─────────────────────┐
│ Grade 9 G           │ ← Lifts up
│ 52.1%              │ ← Enhanced shadow
│ Class Average • 43  │
└─────────────────────┘

Detailed Analytics:
Grade 9 G ← Large, light heading
Class Average • 52.1% ← Clear subtitle

• Class Average: 52.1% ← All metrics
• Mean Score: 469.13   ← clearly visible
• Excellence Rate: 2.3% ← with strong labels
• Mastery Rate: 79.1%  ← and good spacing
```

## Color Reference for Verification

### Text Colors (from lightest to darkest):

1. **base3** `#fdf6e3` - Main text on dark backgrounds ✅
2. **base2** `#eee8d5` - Secondary text on dark backgrounds ✅
3. **base1** `#93a1a1` - Comments (not used in this section)
4. **base0** `#839496` - Body text (not used in this section)

### Background Colors:

- Analytics section: `rgba(0, 0, 0, 0.15)` - Slightly darker than page
- Cards: `rgba(255, 255, 255, 0.08)` - Semi-transparent white
- Drill-down container: `rgba(255, 255, 255, 0.08)` - Semi-transparent white
- Heatmap tiles: Dynamic based on performance (with transparency)

### Alert Colors:

- **Success** (Green): `rgba(76, 217, 100, 0.18)` with border `#4cd964`
- **Warning** (Yellow): `rgba(255, 204, 0, 0.18)` with border `#ffcc00`
- **Danger** (Red): `rgba(255, 69, 58, 0.18)` with border `#ff453a`

## Common Issues & Solutions

### Issue: Text still hard to read

**Solution:** Clear browser cache (Ctrl+Shift+Delete) and hard refresh (Ctrl+F5)

### Issue: Styles not applying

**Solution:** Check that both files were saved:

- `templates/headteacher.html`
- `static/css/headteacher.css`

### Issue: Dropdown not visible

**Solution:** Verify the select element styles in `headteacher.css`

### Issue: Heatmap shows "No data available"

**Solution:** This is expected if no performance data exists yet. The message should still be visible with light text.

## Screenshots to Take (for documentation)

1. **Full Advanced Analytics section** - showing both Performance Heatmap and Performance Alerts
2. **Heatmap tile closeup** - showing text clearly visible
3. **Detailed Analytics expanded** - after clicking a heatmap tile
4. **Hover state** - showing tile lift effect
5. **Dropdown opened** - showing options clearly
6. **Mobile view** - if testing responsive design

## Browser Testing

Recommended browsers to test:

- ✅ Chrome/Edge (latest)
- ✅ Firefox (latest)
- ✅ Safari (latest)
- ✅ Mobile Safari (iOS)
- ✅ Chrome Mobile (Android)

## Accessibility Testing

Use browser DevTools to verify:

1. **Contrast ratio:** Should be at least 4.5:1 for normal text
2. **Text shadow:** Should enhance, not reduce readability
3. **Focus indicators:** Should be visible when tabbing through
4. **Screen reader:** Should announce all text properly

## Performance Testing

Check in DevTools:

1. **Paint times:** Should be under 16ms
2. **Layout shift:** Should be minimal
3. **Memory usage:** Should not increase significantly
4. **Smooth animations:** 60fps on hover effects

---

**Last Updated:** October 31, 2025
**Status:** ✅ Ready for Verification
