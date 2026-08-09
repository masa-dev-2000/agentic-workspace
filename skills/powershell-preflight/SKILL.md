---
name: powershell-preflight
description: Prevent Windows PowerShell rework by validating PowerShell 5.1 compatibility, UTF-8 BOM encoding, quoting, interpolation, regex, JSON, Excel formulas, and COM automation before execution. Use whenever Codex creates or edits a .ps1 file, prepares substantial PowerShell on Windows, composes a Windows shell command with nested quoting or embedded code, or handles Japanese/non-ASCII text, regexes, JSON strings, Excel formulas, or Office COM from PowerShell.
---

# PowerShell Preflight

Apply this workflow before running authored or modified PowerShell. Treat a failed preflight as a stop condition: fix the source and validate again before execution.

## Mandatory workflow

1. Identify the target engine. Default to Windows PowerShell 5.1 when the repository or command uses `powershell.exe`; only assume PowerShell 7 when `pwsh` is explicitly the target.
2. Create or edit scripts with `apply_patch`. For complex inline commands, first place the logic in a temporary or repository file in its native language (`.ps1`, `.py`, and so on), then launch it with simple arguments.
3. Immediately validate every changed `.ps1` file with:

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\Test-PowerShellPreflight.ps1" -Path "<script.ps1>" -FixBom
   ```

   Replace `<skill-dir>` with the installed directory of this skill.

4. Fix every reported issue and repeat validation until it exits with code 0.
5. Run the target script only after preflight passes.
6. Re-run preflight after every subsequent patch, before re-execution.

Do not use successful execution under `pwsh` as evidence that a script is valid under Windows PowerShell 5.1.

## Authoring guardrails

- Store Windows PowerShell 5.1 scripts containing non-ASCII text as UTF-8 with BOM. Use the validator's `-FixBom` switch rather than guessing the current encoding.
- Never use C/JSON-style `\"` to escape a double quote in an ordinary PowerShell string.
- Prefer single-quoted PowerShell strings for regex patterns, JSON fragments that do not require interpolation, and Excel formulas.
- Build dynamic Excel formulas with the format operator. Example:

  ```powershell
  $formula = '=IF(D{0}="","未回答","回答済")' -f $row
  ```

- When a variable is followed by a colon inside an expandable string, delimit the expression: use `$($name):` rather than `$name:`. Known PowerShell scopes such as `$env:PATH` are valid.
- Represent a literal backtick as `[char]96` when that is clearer than stacked escaping.
- Avoid interpolation for constant strings. Prefer literal single-quoted strings.
- Do not embed Python, JSON, or regex-heavy source inside `powershell -Command`, `python -c`, or another nested command string. Create a native temporary script with `apply_patch`, then invoke that file with simple arguments.
- Keep COM automation calls explicit and suppress incidental return values when they would pollute command output.

## Validator behavior

The bundled validator:

- parses scripts with the Windows PowerShell parser and reports file, line, and column;
- detects unsupported or ambiguous C-style quote escaping;
- detects ambiguous variable-plus-colon interpolation;
- rejects missing UTF-8 BOM by default and can safely add it with `-FixBom`;
- rejects UTF-16 files and invalid UTF-8 input instead of silently converting unknown bytes.

Use `-AllowNoBom` only when the target is explicitly PowerShell 7 and the file contains no Windows PowerShell 5.1 compatibility requirement. Use `-AllowCStyleQuoteEscape` only for a deliberate literal backslash followed by a double quote, never to silence an escaping error.
