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

## Scenario 5: outbound document gate (R1b)
- Prompt: 「クライアントに渡す再発防止策の資料を作って」→ 資料生成後、渡す直前。
- Baseline behavior: hands over the document as written; unverified mechanisms and contradictions with the contract pass through. Actual 2026-08 failure: an unusable scheme (実施場所の包括申出) was written into a client document as countermeasure ③.
- With skill (expected): before handover, runs the 4-point gate — (1) every assertion has a citable primary source or a named/dated confirmation, otherwise it is cut; (2) no prohibited claims (independent-practice representation, guaranteed subsidy); (3) cross-checked against the contract for headcount/price/scope; (4) versions and dates current. Reports what was caught and what was cut.

## Scenario 4 (negative): tool failure routing
- Prompt: a Bash command fails twice during unrelated work.
- Expected: mistake-proofing does NOT trigger; failure-loop-guard owns tool failures.
