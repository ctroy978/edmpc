# Email Templates for Student Feedback

This directory contains Jinja2 templates used for emailing student feedback reports.

## Template Files

Each template must have both an HTML and plain text version:
- `{template_name}.html.j2` - HTML email version (rich formatting)
- `{template_name}.txt.j2` - Plain text version (fallback for email clients)

## Default Template

The `default_feedback` template is used when no custom template is specified.

## Available Template Variables

The following variables are available for use in all templates:

| Variable | Description | Example |
|----------|-------------|---------|
| `student_name` | Full name of the student | "John Doe" |
| `grade` | Overall score/grade | "92/100" or "A-" |
| `assignment_name` | Name of the assignment/job | "Essay 2 - Persuasive Writing" |
| `from_name` | Sender name from FROM_NAME env var | "Mr. Cooper's AI Krew" |

## Creating Custom Templates

1. Create two files in this directory:
   - `my_template.html.j2` (HTML version)
   - `my_template.txt.j2` (plain text version)

2. Use Jinja2 syntax to include variables:
   ```html
   <p>Dear {{ student_name }},</p>
   <p>Your grade: {{ grade }}</p>
   ```

3. Use the custom template when sending emails:
   ```python
   send_student_feedback_emails(
       job_id="job_123",
       template_name="my_template"
   )
   ```

## Template Best Practices

- **Keep it concise**: Students should focus on the PDF, not the email
- **Be encouraging**: Positive tone helps student engagement
- **Include instructions**: Let students know what's in the PDF
- **Provide contact info**: How students can ask questions
- **Test both versions**: Verify HTML and plain text render correctly

## Example Custom Template

### honors_english.html.j2
```html
<!DOCTYPE html>
<html>
<body style="font-family: Georgia, serif;">
    <h1>Honors English - Feedback Report</h1>
    <p>Dear {{ student_name }},</p>
    <p>Your {{ assignment_name }} has been graded. Score: <strong>{{ grade }}</strong></p>
    <p>Detailed feedback is attached. Review carefully and come prepared to discuss during our next seminar.</p>
    <p>Best regards,<br>{{ from_name }}</p>
</body>
</html>
```

### honors_english.txt.j2
```text
Honors English - Feedback Report

Dear {{ student_name }},

Your {{ assignment_name }} has been graded. Score: {{ grade }}

Detailed feedback is attached. Review carefully and come prepared to discuss during our next seminar.

Best regards,
{{ from_name }}
```

## Troubleshooting

**Template not found error:**
- Ensure both `.html.j2` and `.txt.j2` versions exist
- Check file names match exactly (case-sensitive)
- Verify files are in the correct directory

**Variables not rendering:**
- Check variable names match exactly (case-sensitive)
- Use double curly braces: `{{ variable_name }}`
- Don't add spaces inside braces: `{{variable}}` not `{{ variable }}`

**HTML not displaying correctly:**
- Test in multiple email clients (Gmail, Outlook, etc.)
- Use inline CSS styles, not external stylesheets
- Keep formatting simple for best compatibility
