# Adaptive Skill System

This directory contains the local-first, reusable infrastructure shared by the
Skills in this repository. It is source code, not an installed Plugin.

## Boundaries

- `skill-registry.yaml` is the canonical Skill contract registry.
- `component-registry.yaml` is the canonical reusable-component registry.
- `contracts/` contains versioned, content-free interchange schemas.
- `runtime/` contains deterministic local execution primitives.
- `evals/` defines cross-Skill evaluation evidence.
- `staging/` defines the disabled-by-default improvement boundary.
- `packaging/` may produce non-mutating export or cutover previews only.

The unified event worker and Hook cutover remain disabled and uninstalled.
Packaging, installation, activation, and persistent behavior changes require
their own verified gate and exact user approval.

## Verification

Run:

```powershell
python -B scripts/validate_skill_registry.py
python -B scripts/sync_skill_contract_digests.py
python -B -m unittest scripts.test_validate_skill_registry
python -B -m unittest discover -s .adaptive-system/tests -p "test_*.py"
```

OS-backed process locking is supported only on a local filesystem. Shared or
network filesystems require a separately qualified lock backend.
