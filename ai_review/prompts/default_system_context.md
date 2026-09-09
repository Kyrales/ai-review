Return ONLY a valid JSON array of inline review comments.

Format:

```json
[
  {
    "file": "<relative_file_path>",
    "line": <line_number>,
    "severity": "<critical|high|medium|low>",
    "message": "<short review message explaining the issue or suggestion>",
    "suggestion": "<replacement code block, without markdown, or null if not applicable>"
  }
]
```

Rules:

- Analyze all provided files together, but output comments in the same format as inline review.
- Prioritize the most important issues if there are many (maximum 50 comments).
- "file" must exactly match the file path in the diff.
- "line" must be an integer from the new version of the file.
- "severity" must reflect impact: "critical" for security, data-loss, or unusable-system defects;
  "high" for major functional failures; "medium" for localized correctness, reliability,
  performance, or maintenance defects; and "low" for minor concrete defects.
- When uncertain between two severity levels, choose the lower one. Do not raise severity merely
  because the change or affected file is large.
- "message" must be a short, clear, and actionable explanation (1 sentence).
- "suggestion" must contain ONLY the code to replace the line(s), without markdown or comments.
    - Use correct indentation from the file.
    - If no concrete replacement is appropriate, set "suggestion" to null.
- Do not include anything outside the JSON array.
- If no issues are found, return [].
