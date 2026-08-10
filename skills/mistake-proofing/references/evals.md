# Evals: mistake-proofing

Baseline = same prompt without the skill loaded.

## Scenario 1: incident recording (R4)
- Prompt: 「助成金研修のMeetリンクが直前に無効になって研修を取り下げた。再発防止したい」
- Baseline behavior: generic advice, single-track why-why, ends with 「注意しましょう」-grade countermeasures.
- With skill (expected): two-track (発生系/流出系) analysis via incident-template.md, root cause lands on missing mechanism (not attention), countermeasures ranked 仕組み>チェック>注意, horizontal-deployment question asked, incident file + 星取表 row created and paths reported.
- Verified: 2026-08 Meet-link incident was processed this way (see 00_ops-rulebook and ai-training/design/operations/incident-2026-08-meet-link.md).

## Scenario 2: pre-deletion ledger check (R2)
- Prompt: 「使っていないGoogle Workspaceアカウントを解約したい」
- Baseline behavior: explains how to cancel; does not surface published Meet URLs or submitted-document dependencies.
- With skill (expected): searches all 依存台帳.md + 星取表 BEFORE any deletion step, lists external commitments that would break, proposes suspend-first, records the check in the ledger, and does not execute deletion until the user confirms the check result.

## Scenario 3: external submission registration (R1)
- Prompt: 「労働局に計画届を提出する。受講案内も添付する」
- Baseline behavior: helps draft/submit; volatile info (URLs, dates) goes untracked.
- With skill (expected): extracts volatile items, creates/updates the project 依存台帳.md from the template, sets survival-check dates back-planned from the change-procedure deadline (R3), and reports ledger path.

## Scenario 4 (negative): tool failure routing
- Prompt: a Bash command fails twice during unrelated work.
- Expected: mistake-proofing does NOT trigger; failure-loop-guard owns tool failures.
