# Summary Followup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect new human replies under AI summary discussions, process each reply exactly once through `run-followup`, and make concrete summary risks identify their source location in text.

**Architecture:** Reuse the existing v2 followup marker, knowledge extraction, trusted-reviewer rules, and publication state machine for both inline `finding` and general `summary` threads. Keep summary comments general in GitFlic; require a file and 1C metadata object/method in their text when the risk is concrete. Do not add another LLM call or a new marker format.

**Tech Stack:** Python 3.12+, Pydantic, pytest/pytest-asyncio, PowerShell 7, GitFlic discussions API.

**Spec:** User-approved bounded design in the 2026-09-14 conversation; no separate specification document.

## Global Constraints

- Preserve current inline followup, trusted-reviewer dismissal, knowledge extraction, idempotency, and discussion-closing behavior.
- In the origin thread, a reply is new only until its ID appears in a trusted valid v1/v2 followup marker's `covered` field. In a recovery/continuation thread, only a v2 marker with the matching `origin` may cover it.
- Use `create_summary_reply` for summary threads and `create_inline_reply` for inline threads.
- For a summary thread, give the followup LLM current whole-MR diff context, capped to the existing prompt-size discipline.
- Process only matching pairs: `INLINE` + `FINDING` and `SUMMARY` + `SUMMARY`.
- Do not attempt to convert summary comments into GitFlic inline comments.
- Keep the solution provider-neutral; GitFlic-specific discovery remains in the project launcher.

---

### Task 1: Process summary discussions in the core followup runner

**Files:**
- Modify: `ai_review/services/review/runner/followup.py`
- Modify: `ai_review/services/review/runner/followup_publication.py`
- Test: `ai_review/tests/suites/services/review/runner/test_followup.py`
- Test: `ai_review/tests/suites/services/review/runner/test_followup_publication.py`

**Interfaces:**
- Consumes: `ReviewThreadSchema.kind`, `VCSClientProtocol.get_general_threads()`, `GitServiceProtocol.get_diff()`.
- Produces: `FollowupReviewRunner.run()` handling roots marked `finding` or `summary`; publication selects the matching VCS reply method.

- [x] **Step 1: Add failing tests for summary discovery and idempotency**

  Add a summary thread containing a trusted AI `summary` root and one human reply. Assert that `run()` calls the LLM and publishes one reply. Add existing v1 and v2 followups covering that reply and assert that repeated runs publish nothing. Add negative tests proving mismatched thread/marker kinds are ignored.

- [x] **Step 2: Run the focused tests and verify the expected failure**

  Run:

  ```powershell
  $env:AI_REVIEW_CONFIG_FILE_YAML = '.\ai_review\tests\configs\config-test.yaml'
  .\.venv\Scripts\python.exe -m pytest ai_review/tests/suites/services/review/runner/test_followup.py -q
  ```

  Expected: the new summary test fails because `run()` currently invokes `_process()` only for inline `finding` roots.

- [x] **Step 3: Add failing tests for summary publication, closing and context**

  Assert that every publication branch (open, direct reply to resolved, reopen/recovery and ambiguous publication recovery) routes summary through `create_summary_reply` and never through `create_inline_reply`. Cover summary terminal verdicts (`fixed` and `withdrawn`), retry of an unfinished close without another LLM call, trusted silent dismissal, closing after knowledge extraction, and refusal to close after a concurrent human reply. For context, assert the exact `git.get_diff(base_sha, head_sha, unified=20)` call, absence of file-specific git calls, truncation to the existing limit, and safe operation without a git service.

- [x] **Step 4: Implement the minimal generic followup path**

  In `FollowupReviewRunner.run()`, read inline and general threads once and process only the matching trusted marker pairs `INLINE` + `FINDING` and `SUMMARY` + `SUMMARY`. In `_context()`, retain file-local context for inline threads and use the capped whole-MR diff for summary threads. Add one kind-aware thread lookup and use it in all retry/silent-dismissal/final-close refreshes. In `FollowupPublicationStateMachine`, centralize the reply operation selected from `origin.kind` and use it in every publication and recovery branch.

- [x] **Step 5: Verify focused Python tests**

  Run:

  ```powershell
  $env:AI_REVIEW_CONFIG_FILE_YAML = '.\ai_review\tests\configs\config-test.yaml'
  .\.venv\Scripts\python.exe -m pytest ai_review/tests/suites/services/review/runner/test_followup.py ai_review/tests/suites/services/review/runner/test_followup_publication.py -q
  ```

  Expected: all focused tests pass, including exactly-once processing.

### Task 2: Detect summary replies in the СППР launcher

**Files:**
- Modify: `F:/1C/Projects/RT_VT/sppr_gitflic/tools/ai-review/AiReview.psm1`
- Test: `F:/1C/Projects/RT_VT/sppr_gitflic/tools/ai-review/tests/test-ai-review.ps1`

**Interfaces:**
- Consumes: GitFlic discussion `rootNote`, `replies`, and trusted AI markers.
- Produces: `Get-AiReviewClassification` returning `followup` for uncovered replies under either a `finding` or a `summary` root.

- [x] **Step 1: Add the MR 61 regression test and verify it fails**

  Model one resolved discussion with a trusted AI `summary` root and one human reply. Assert `Mode = 'followup'` and `PendingCount = 1`. Add v1 and v2 followup markers covering the reply and assert repeated classification returns `none`. Also cover retrying the unresolved close state after a terminal summary followup.

- [x] **Step 2: Generalize classification without changing marker parsing**

  Replace the `finding`-only root guard with a guard accepting `finding` and `summary`. Reuse the current covered-ID set and terminal-verdict retry logic; do not add summary-specific state.

- [x] **Step 3: Run launcher contract tests**

  Run:

  ```powershell
  pwsh -NoProfile -File F:/1C/Projects/RT_VT/sppr_gitflic/tools/ai-review/tests/test-ai-review.ps1
  pwsh -NoProfile -File F:/1C/Projects/RT_VT/sppr_gitflic/tools/ai-review/tests/test-ci-contract.ps1
  ```

  Expected: both scripts exit with code 0.

### Task 3: Make concrete summary risks traceable

**Files:**
- Modify: `ai_review/prompts/default_summary.md`
- Modify: `F:/1C/Projects/RT_VT/sppr_gitflic/ai-review/prompts/summary.md`
- Test: `ai_review/tests/suites/libs/config/test_prompt.py`
- Test: `F:/1C/Projects/RT_VT/sppr_gitflic/tools/ai-review/tests/test-ci-contract.ps1`

**Interfaces:**
- Produces: prompt rule requiring concrete summary risks to name the source file and, when identifiable, the 1C metadata object or method.

- [x] **Step 1: Add failing prompt-contract assertions**

  Assert both the generic default and СППР control prompt contain the location requirement. The required behavior is textual traceability, not an inline GitFlic position.

- [x] **Step 2: Add the minimal prompt instruction**

  Add an English rule to the generic prompt and its Russian equivalent to the project prompt: every concrete risk names the changed or affected file and, when known, the 1C metadata object or method. If an exact source location is impossible, require the available evidence and affected files instead of suppressing the risk.

- [x] **Step 3: Verify prompts and the complete suites**

  Run:

  ```powershell
  $env:AI_REVIEW_CONFIG_FILE_YAML = '.\ai_review\tests\configs\config-test.yaml'
  .\.venv\Scripts\python.exe -m ruff check ai_review
  .\.venv\Scripts\python.exe -m pytest -q
  pwsh -NoProfile -File F:/1C/Projects/RT_VT/sppr_gitflic/tools/ai-review/tests/test-ai-review.ps1
  pwsh -NoProfile -File F:/1C/Projects/RT_VT/sppr_gitflic/tools/ai-review/tests/test-ci-contract.ps1
  ```

  Expected: all checks exit with code 0.

- [x] **Step 4: Build and smoke-test the Windows release**

  Run:

  ```powershell
  .\.venv\Scripts\python.exe build_release.py
  .\artifacts\releases\win-x64\ai-review.exe --help
  ```

  Expected: both commands exit with code 0; generated binaries remain uncommitted.
