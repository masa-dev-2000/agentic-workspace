---
name: archive-deliverables
description: Organize generated documents, slides, PDFs, reports, and other deliverables after a current version is created or approved. Use automatically when a task produces a replacement artifact while older drafts remain in the same output area, or when the user asks to archive, clean up,整理,版管理, or leave only the latest deliverable visible.
---

# Archive Deliverables

Keep the current deliverable easy to find and preserve superseded versions without deletion.

## Workflow

1. Inspect the output directory and identify candidate deliverables.
2. Classify each file as:
   - `current`: the latest approved or explicitly requested artifact;
   - `supporting`: notes, source evidence, internal cost basis, or generation inputs still in use;
   - `superseded`: older drafts replaced by the current artifact;
   - `uncertain`: files whose status cannot be established from names, timestamps, content, or conversation.
3. Keep `current` and active `supporting` files in the visible output directory.
4. Move `superseded` files into `archive/YYYY-MM-DD_<reason>/`.
5. Create or update `INDEX.md` in the visible output directory with:
   - the current deliverable;
   - supporting files and whether they are client-facing or internal;
   - archive locations;
   - the date and basis used to identify the current version.
6. Verify that every moved file exists at its destination and that the current artifact still exists.
7. Report the current artifact and archive path.
8. In the final handoff, provide clickable links to the current artifact and its containing index or folder so the user does not need to navigate through Explorer manually.
9. After successful generation and verification on the user's local Windows machine, open the single primary current artifact with PowerShell so the user can review it immediately:

```powershell
$artifact = (Resolve-Path -LiteralPath "ABSOLUTE_OR_PROJECT_RELATIVE_PATH").Path
Start-Process -FilePath $artifact
```

Open only the primary deliverable, not supporting files or archived versions. Do not open intermediate drafts. Skip automatic opening in remote or headless environments, when no desktop application is available, or when the user asks not to open it.

## Automatic behavior

Apply this workflow without waiting for a separate cleanup request when all are true:

- a new artifact replaces an earlier artifact in the same workstream;
- the current version is unambiguous from the user's decision or a successful generation step;
- moving the older files is reversible and remains inside the project.

Do not interrupt the main task merely to announce routine archiving. Mention it in the final handoff.

## Safety rules

- Never delete archived files.
- Resolve and validate absolute source and destination paths before recursive moves.
- Move only files inside the user-authorized project.
- Do not archive contracts, invoices, signed documents, client-provided files, source evidence, or unrelated deliverables merely because they are older.
- Do not infer that a sent or signed artifact is obsolete from timestamp alone.
- Do not overwrite an existing archive file. Add a stable suffix when names collide.
- Preserve generation sources unless the user explicitly asks to archive them.
- Verify the final artifact exists and passed its format-specific checks before opening it.
- If `current` is ambiguous or moving a file could change legal, contractual, or external records, ask for confirmation.

## Naming

Prefer:

```text
output/
  CURRENT_DELIVERABLE.pdf
  INDEX.md
  supporting-notes.md
  archive/
    2026-07-30_drafts/
      superseded-version.pdf
```

Use the date the version was superseded, not the file creation date, when known.

## Deterministic helper

For multiple explicit files, use `scripts/archive_files.py`.

```powershell
python -X utf8 scripts/archive_files.py `
  --base "ABSOLUTE_OUTPUT_DIRECTORY" `
  --archive-label "YYYY-MM-DD_drafts" `
  --files "old-a.pdf" "old-b.pdf"
```

The helper refuses paths outside `--base`, avoids overwrites, and prints a JSON result. Determine file status before invoking it; the script does not decide which version is current.
