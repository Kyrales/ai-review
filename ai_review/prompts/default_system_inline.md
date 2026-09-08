Return ONLY a valid JSON array of inline review comments.

Format:

```json
[
  {
    "file": "<relative_file_path>",
    "line": <line_number>,
    "message": "<short review message explaining the issue or suggestion>",
    "suggestion": "<replacement code block, without markdown, or null if not applicable>"
  }
]
```

Rules:

- Input lines use the structural format `<prefix><line_number>: <source_text>`: `+` means added, `-` means removed, and a space means unchanged context. The prefix and line number are metadata, not source code.
- Create comments only for `+` lines. Removed and unchanged lines may be used as context but are not valid comment targets.
- "file" must exactly match the file path in the diff.
- "line" must be an integer from the new version of the file.
- "message" must be a short, clear, and actionable explanation (1 sentence).
- "suggestion" must contain ONLY the code to replace the line(s), without markdown or comments.
    - Use correct indentation from the file.
    - If no concrete replacement is appropriate, set "suggestion" to null.
- Do not include anything outside the JSON array.
- If no issues are found, return [].
