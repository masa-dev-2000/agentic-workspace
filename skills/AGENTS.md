# Skill maturity gate

- `skill-maturity-gate.json` is the source of truth for whether a new Skill may be created.
- While its `status` is `frozen`, do not create or initialize a new Skill. Improving an
  existing Skill, its tests, references, or scripts is allowed.
- A frozen gate may be bypassed only when the user explicitly approves the exact new Skill in
  the current request. Pass both `--user-approved-freeze-override` and
  `--override-reason "<short approval reference>"` to the system `init_skill.py`.
- Do not interpret a general request to continue, implement a plan, or improve Skills as an
  exception.
- Propose unfreezing only after every criterion in the gate file has verified evidence.

# Creating a new Skill

`.system/skill-creator` covers generic authoring craft but knows nothing about this
workspace's contracts. Apply these on top of it, in order:

1. **Earn the Skill first.** Follow the smallest-intervention order stated below
   (dictionary → `AGENTS.md` → existing Skill → Skill handoff → Hook → Runtime → new
   Skill). A single incident never justifies a Skill. State which existing Skill you
   considered and why it does not fit.
2. **Check the gate.** `skill-maturity-gate.json` must not be `frozen`.
3. **Write evals before prose.** At least three scenarios plus a baseline run without
   the Skill, so there is evidence the Skill changed an outcome.
4. **Keep the frontmatter portable.** Only `name`, `description`, and optionally
   `license` / `compatibility` / `metadata` / `allowed-tools`. `name` must equal the
   directory name; `description` ≤1024 chars, third person, stating what it does, when
   to use it, and when not to. Vendor-specific fields belong in the adapter layer, never
   in `SKILL.md` — `scripts/validate_workspace.py` enforces this and fails the push.
5. **Body under 500 lines.** Overflow goes to `references/`, one level deep, with a table
   of contents in any reference file over 100 lines. Put deterministic behavior in
   `scripts/`, not in prose.
6. **Register it.** Add the entry to `skill-registry.yaml` (capability, nonGoals,
   triggers positive/negative, authority, completion proof, `maturity`,
   `contractContentDigest`) and run `python -X utf8 scripts/validate_skill_registry.py`
   until valid. An unregistered Skill on disk is an error, not a warning.
7. **Declare its state.** Any path the Skill writes outside its own directory must appear
   in `config/wiring.json`. Ledgers, databases, and keys never live in the repo — it is
   public, and `check_no_ledgers_in_repo()` enforces that.
8. **Disambiguate.** If the description overlaps an existing Skill, add an explicit
   "Do not use for … (use `<other>` instead)" clause to both — description matching is
   the only routing signal.

# Canonical Skill registry

- `skill-registry.yaml` is the canonical responsibility, dependency, authority, and completion
  evidence registry for locally governed Skills.
- Update the registry when an existing Skill's responsibility, dependency, authority, or
  completion contract changes.
- Run `python -X utf8 scripts/validate_skill_registry.py` after registry changes.

# Local-first Skill development

- Treat `$CODEX_HOME/skills` (or `~/.codex/skills` when unset) as the editable source of truth
  for local Skill development. Do not require a Plugin version bump, package rebuild, or
  reinstall for an ordinary Skill improvement.
- Treat Plugin manifests and package layouts as generated distribution outputs. Generate them
  only from a validated registry snapshot when the user explicitly asks to package or release.
- Refer to dependencies by stable capability ID in the canonical registry. Resolve capability
  IDs to the current local or packaged Skill key at runtime; do not hard-code Plugin namespaces
  into reusable workflow contracts.
- Treat `contractFingerprint` as the one-release compatibility ID
  (`capability@version`), not as a content hash. Use the validated
  `contractContentDigest` for semantic drift, approval, resolution, and export evidence.
- Keep Skill, Runtime, and Hook responsibilities separate:
  - Skill: non-trivial semantic judgment and a bounded, independently verifiable outcome.
  - Runtime: deterministic capture, sanitization, deduplication, queues, leases, budgets,
    approvals, application, rollback, and migration.
  - Hook: write one event to a local spool and return quickly. Do not run an LLM, query domain
    ledgers, fan out child processes, or start detached workers from a Hook.
- Store detailed schemas and contracts outside `SKILL.md`. Keep `SKILL.md` concise and place
  deterministic behavior in scripts.
- For stakeholder materials whose purpose is a decision, approval, alignment, funding, or
  action, use `build-decision-ready-materials` as the semantic owner. Start from the reader,
  decision, evidence, causal story, and completion proof; invoke presentation, document, or PDF
  Skills only as format executors after the Decision Card is approved. Mechanical conversion or
  an isolated layout edit may go directly to the applicable format Skill.
- Verify changed behavior by executing the applicable tests, validators, build, or real artifact
  inspection in the current task. A response, plan, file existence, or self-report is not
  completion evidence.
- Forward-test complex Skill behavior with independent agents using task-like prompts and raw
  artifacts. Do not leak the intended answer or suspected defect into the evaluator context.
- After any tool, command, build, test, network, permission, or workflow failure, apply
  `failure-loop-guard`: preserve the failure signature and make the next attempt materially
  different. Never make a third equivalent attempt after the same failure occurs twice.
- On Windows, when a command fails before execution with `CreateProcessAsUserW failed: 5` while
  launching `WindowsApps\pwsh.exe`, classify the launcher shim as unavailable. Do not retry the
  same shell path. Use a non-shell file API or a verified real executable launched directly with
  an argument array (for example through `node_repl` `execFile`/`spawnSync`), keep windows hidden,
  and preserve the original launcher signature as failure evidence.
- Do not turn a single complaint, failure, or consultation into a new Skill. Preserve it as
  evidence, retain counterexamples and scope boundaries, and prefer the smallest intervention:
  dictionary, `AGENTS.md`, existing Skill, Skill handoff, Hook, Runtime, then new Skill.
- Automated learning may collect, analyze, stage, and test improvement proposals. It must not
  activate a persistent behavior change without exact human approval of the current proposal
  and target fingerprints.
- Runtime process locks are supported only on a local filesystem with working OS advisory locks.
  Do not activate the unified worker on a shared or network filesystem without a separately
  qualified lock backend.
- Before invoking Documents or Presentations helper scripts from the bundled OpenAI primary
  runtime on Windows, resolve the bundled interpreter and native Poppler directory with
  `.adaptive-system/runtime/bundled_runtime_resolver.py`. Use its `pythonPath` and
  `popplerBinDir`; do not invoke those helpers with the system Python or pass a `.cmd` wrapper
  directory to `pdf2image`.
