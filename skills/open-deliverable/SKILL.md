---
name: open-deliverable
description: Open the primary local deliverable in its Windows default application after Codex creates or materially updates and verifies it. Use automatically for completed PDFs, presentations, documents, spreadsheets, images, HTML, Markdown, and other reviewable artifacts so the user does not need to navigate through Explorer or click a CLI link.
---

# Open Deliverable

Show the finished artifact immediately after it passes its format-specific checks.

## Workflow

1. Finish generation or editing.
2. Run the applicable content and visual or structural validation.
3. Identify one primary current deliverable.
4. Resolve its absolute path and confirm it is a non-empty regular file.
5. Run `scripts/open_deliverable.ps1`.
6. Keep a clickable file link in the final handoff as a fallback.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts/open_deliverable.ps1 `
  -Path "ABSOLUTE_OR_PROJECT_RELATIVE_FILE_PATH"
```

## Selection rules

- Open only the primary user-facing deliverable.
- Prefer the final PDF over rendered PNG pages or generation sources.
- Prefer the workbook over exported CSV previews when the workbook is the requested artifact.
- Do not open supporting notes, archives, test fixtures, or intermediate renders.
- If several deliverables are equally primary, open the one named by the user. Otherwise ask or open none.

## Safety and UX

- Open only after verification succeeds.
- Never auto-open executables, installers, scripts, shortcuts, archives, or unknown binary formats.
- Do not open anything in remote, server, CI, or headless environments.
- Do not reopen the same unchanged artifact repeatedly in one turn.
- Respect an explicit request not to open the file.
- Use a visible desktop application; this Skill exists for immediate human review.
- If opening fails, return the resolved path and a clickable link without retry loops.

## Test mode

Use `-WhatIf` to validate path and extension without launching an application:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts/open_deliverable.ps1 `
  -Path "FILE_PATH" `
  -WhatIf
```
