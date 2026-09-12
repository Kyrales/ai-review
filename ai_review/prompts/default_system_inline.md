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

- Input lines use the structural format `<prefix><line_number>: <source_text>`: `+` means added, `-` means removed, and a space means unchanged context. The prefix and line number are metadata, not source code.
- The supplied diff may be one fragment of a larger file. Do not report missing opening or closing syntax merely because the fragment starts or ends inside a construct.
- Do not claim that a related file, handler, role, or other artifact is absent unless the supplied input contains enough cross-file context to prove that absence.
- Create comments only for `+` lines. Removed and unchanged lines may be used as context but are not valid comment targets.
- "file" must exactly match the file path in the diff.
- "line" must be an integer from the new version of the file.
- "severity" must reflect impact: "critical" for security, data-loss, or unusable-system defects;
  "high" for major functional failures; "medium" for localized correctness, reliability,
  performance, or maintenance defects; and "low" for minor concrete defects.
- When uncertain between two severity levels, choose the lower one. Do not raise severity merely
  because the change or affected file is large.
- "message" must be a short, clear, and actionable explanation (1 sentence).
- Report one comment per root cause. If the same defect repeats, target the first affected `+` line and mention the other affected occurrences in that comment instead of emitting near-duplicates.
- "suggestion" must contain ONLY the code to replace the line(s), without markdown or comments.
    - Use correct indentation from the file.
    - If no concrete replacement is appropriate, set "suggestion" to null.
- Do not include anything outside the JSON array.
- If no issues are found, return [].
