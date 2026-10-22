# Timetrack — Design Plan

Status: implementation specification, version 0.1  
Date: 2026-10-21

## 1. Purpose

Timetrack is a lightweight, portable Python desktop application that measures time spent on a primary task versus alternate activities. Tracking starts immediately without requiring a task name. A single toggle switches between **On task** and **Off task**. Labels can be assigned later.

## 2. Agreed requirements

- Python desktop application with a compact interface.
- No database, account, server, or network dependency.
- Active timing remains in memory until the next toggle or session end.
- Completed segments are appended through one central write function.
- Automatically create `TEMP` beneath the application's launch working directory.
- Store completed segments as JSON Lines (`.jsonl`): one JSON object per line, without an enclosing array. This permits append-only storage without rewriting earlier records.
- End the session and export CSV to a location chosen by the user.
- Retain temporary data if export is cancelled or fails; delete it only after successful export.
- No periodic saves or timer-driven disk writes.

## 3. Session workflow

| Action | Result | Disk activity |
| --- | --- | --- |
| Launch | Show Stopped; offer recovery of unexported sessions | Inspect TEMP if present |
| Start session | Begin On task; initialize timers in memory | Create TEMP and a uniquely named empty session file |
| Toggle to Off task | Close On task segment; begin Off task at the same boundary | Append one completed segment |
| Toggle to On task | Close Off task segment; begin On task at the same boundary | Append one completed segment |
| Change label | Update the current label in memory | None |
| End session | Close final segment; stop timing; open review/export flow | Append final segment once |
| Export | Save reviewed segments to chosen CSV | Write CSV; remove session file after success |

There is no paused state in the first release. Off task is tracked time; Stopped is excluded time. File dialogs and review after End session do not increase session duration. A label applies to its whole segment; changing activity mid-segment requires a toggle or later split.

## 4. Main interface

Suggested starting size: approximately 420 × 360 pixels, resizable with system font scaling.

| Control | Purpose |
| --- | --- |
| Timetrack title | Application identity |
| Status text | Stopped, On task, or Off task |
| Current segment timer | HH:MM:SS; hours may exceed 24 |
| Primary button | Start session, Switch to Off task, or Switch to On task |
| Session totals | On task, Off task, total tracked time, On task percentage |
| Optional label field | Blank by default; never required to track |
| End session | Enabled only while tracking |
| Recover/export sessions | Access retained TEMP files |

Use teal for On task and amber for Off task, with explicit text in both states. Keep keyboard focus visible. Space activates the focused button through normal UI behaviour; do not intercept typing in label fields. Closing while tracking offers **End session and export**, **End session and keep for later**, or **Cancel**. Closing after stopping retains unexported data.

## 5. Review and labels

After stopping, show an editable segment table with start, end, duration, state, and label. Support assigning labels to selected rows, changing state, splitting segments, and moving shared boundaries while preserving chronology and preventing overlaps or gaps. Keep the session's outer boundaries fixed in the first release.

Edits remain in memory until CSV export; the capture log stays append-only. Tell users that unexported review edits are lost when the application closes, although original captured segments remain available. Do not clear pending edits silently. Persistent history after export lives in the CSV documents; a database-backed history browser is outside scope.

## 6. Storage contract

Resolve the base path once at launch using the working directory (`Path.cwd()`), not the operating system's temporary directory or a bundled executable's extraction directory. Shortcuts must set their working directory deliberately. Show the resolved TEMP path in storage errors. If it is unwritable, block session start with an actionable message rather than silently changing locations.

Filename: `TEMP/session_YYYYMMDD_HHMMSS_<session-id>.jsonl`. Use a random session ID and exclusive creation to prevent collisions. Each process owns a separate session file.

Example completed segment (one physical line in the actual file):

```json
{"schema_version":1,"session_id":"f985268a-9ac3-4bc0-998f-50fb126c22d0","segment_id":1,"start":"2026-10-21T14:00:00-04:00","end":"2026-10-21T14:25:00-04:00","duration_seconds":1500.0,"state":"on_task","label":"","session_end":false}
```

| Field | Rule |
| --- | --- |
| schema_version | Integer format version; initially 1 |
| session_id | Unique ID, repeated on each segment |
| segment_id | Increasing integer within a session |
| start / end | ISO 8601 timestamps including UTC offset |
| duration_seconds | Nonnegative elapsed seconds; retain precision until display/export |
| state | Exactly `on_task` or `off_task` |
| label | String; empty means unlabelled |
| session_end | True only on the final segment written by End session |

## 7. Timing and the single writer

Use a monotonic clock for ordinary elapsed durations and timezone-aware timestamps for display and export. Refresh the visible timer roughly four times per second without touching disk. Capture a transition boundary once and reuse it as the previous end and next start.

All segment persistence goes through `append_segment(session_path, segment)`. It serializes the full object plus newline and appends it in one logical operation, then flushes and closes the file. An `fsync` at segment boundaries may improve durability without adding periodic saves. A single Python write call is not a guarantee of atomicity or power-loss safety.

Transition sequence:

1. Capture the boundary and freeze the completed segment in memory.
2. Call the central writer once for that segment.
3. On success, begin the opposite state from the captured boundary, or stop for End session.
4. On failure, retain the frozen segment, show an error, and block further transitions until Retry or cancellation resolves it. Keep the intended next segment's start boundary in memory so retry latency is not lost or counted twice.

Serialize button events and disable transition controls during persistence. Retrying must check the session/segment ID and file tail to avoid duplicating a record if a write succeeded before a later flush error. A partial trailing line must be detected and repaired explicitly before appending; ordinary successful writes never rewrite previous records.

Totals include completed segments plus the current in-memory segment. On task percentage is `on_task / (on_task + off_task) × 100`; display 0% when total duration is zero.

## 8. Recovery and timing gaps

- On restart, recover only complete, valid records. Never invent elapsed time after the last saved segment.
- A crash loses the current unwritten segment. Review edits are also memory-only until export.
- An empty file contains no recoverable time and can be discarded with user confirmation.
- For a malformed trailing line, offer recovery of the intact prefix and retain the original until recovery/export succeeds. Errors in the middle of a file must be reported rather than silently skipped.
- Offer recovery only for files not owned by another running instance. Use a process/session lock or equivalent ownership check; a second instance must never delete an active file.
- Treat sleep and major wall-clock/monotonic discrepancies as unresolved gaps. Do not silently assign them to either state. Present the discrepancy for review and resolve it in memory before the next append/export. Do not add background disk checkpoints.
- Ordinary wall-clock changes must not produce negative durations. Elapsed duration is authoritative; retain observed timestamps and surface discrepancies for review.

Cross-platform suspend accounting is an implementation validation item because monotonic clocks differ in whether they include sleep.

## 9. CSV export

Open the native Save As dialog after session review. Suggest `Timetrack_YYYY-MM-DD_HHMMSS.csv`; the user chooses the destination. Export one row per reviewed segment using Python's `csv` module, with UTF-8 BOM for common spreadsheet compatibility and standard quoting for commas, quotes, and newlines.

Columns: `Session ID`, `Segment ID`, `Start`, `End`, `Duration Seconds`, `State`, `Label`.

Preserve timestamp offsets and export seconds to three decimal places. Leave labels blank when unset. Export label cells as safe text when a leading character could be interpreted as a spreadsheet formula. Do not add summary rows that break the row schema; totals remain available in the interface and can be calculated from CSV.

Write to a staging file in the chosen destination directory, close successfully, then replace the selected output atomically where supported. Confirm before replacing an existing CSV. Delete only this session's JSONL after the CSV is committed. If cleanup fails, report export success and retain the source. Cancelled or failed export must preserve both the capture log and in-memory review edits while the application remains open.

## 10. Implementation structure

| Module | Responsibility |
| --- | --- |
| `main.py` | Application launch and working-directory resolution |
| `ui.py` | Tkinter/ttk controls, timers, dialogs, and review table |
| `tracker.py` | Session state, transitions, totals, and timing |
| `models.py` | Session and segment data structures and validation |
| `storage.py` | Central append function, recovery parsing, ownership handling |
| `export.py` | CSV formatting, safe save, and successful-export cleanup |

Prefer standard-library modules: `tkinter`, `json`, `csv`, `time`, `datetime`, `pathlib`, `uuid`, and `dataclasses`. Keep UI callbacks separate from timing and file handling. Tkinter availability must be checked on target Linux installations. A bundled distribution should include its required runtime; build and validate separately on each target OS.

## 11. Implementation stages

1. **Core tracker:** Start, toggle, stop, in-memory timing, central JSONL append.
2. **Desktop interface:** Status, timers, totals, optional labels, close behaviour.
3. **Review and export:** Segment editing, Save As, CSV, cleanup on success.
4. **Recovery:** Incomplete files, write failures, retries, instance ownership, timing gaps.
5. **Packaging:** Portable builds on selected target systems.

First-release exclusions: cloud sync, accounts, automatic activity monitoring, reminders, idle detection, tray operation, global shortcuts, charts, and integrations.

## 12. Acceptance checks

- Start without typing a label; the first segment is On task.
- Run for several minutes: no segment writes occur before Toggle or End session.
- Toggle repeatedly: exactly one complete record per transition, with shared boundaries and alternating states.
- End session: final segment appears once, including when export is cancelled and retried.
- Verify totals against controlled durations, including hour/day boundaries.
- Simulate a write error and retry: no discarded or duplicated segments.
- Terminate the app mid-segment: earlier segments recover; the active segment is not fabricated.
- Recover a truncated final line without destroying valid earlier data.
- Export blank labels, Unicode, commas, quotes, newlines, and formula-like text safely.
- Cancel or fail export: TEMP data remains. Successful export removes only the relevant session file.
- Launch from a different working directory: TEMP is created beneath that directory.
- Check sleep/resume, wall-clock changes, and concurrent instances on each supported platform.

This document specifies the application; implementation and packaged binaries are separate deliverables.
