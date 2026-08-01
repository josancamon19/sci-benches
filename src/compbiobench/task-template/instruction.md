QUESTION: {{ question }}
{% if file_names %}
FILES: {{ file_names | join(', ') }}

Note: All files are located in your current working directory (workspace).
{% endif %}
INSTRUCTIONS:
- You have 120 minutes to complete this task.
- Do not read or access files outside the workspace.
- Get any files or tools you need from the internet.
- You are free to modify the current environment as needed.
- Keep all scripts and intermediate data in the workspace only.

OUTPUT CONTRACT (IMPORTANT):
- Before finishing, save exactly one line containing only the final answer to `/app/final_answer.txt`.
- The answer must use the format required by the question.
- Do not include explanations, reasoning, labels, prefixes, markdown, code fences, citations, or extra whitespace lines in that file.
- Verify `/app/final_answer.txt` contains exactly one line and nothing else.
