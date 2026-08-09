# Repository layout manifest

Store the approved manifest as `.repo-layout.yaml` at the Git root. A proposal may live elsewhere
until approved.

```yaml
version: 1
repo_root: "."
workspace_root: ".."
roles:
  source: [apps, scripts]
  documentation: [docs]
  planning: [planning]
  tests: [tests]
  fixtures: [test_data]
  generated_working: [output]
  deliverables: [deliverables]
  cache_temp: [.pytest_cache, .tmp, tmp]
  archive: [output/_archive]
protected_external:
  - path: ../client-source
    access: read-only
deprecated_paths:
  outputs:
    replacement: output
    policy: no-new-writes
constraints:
  approval_required_for_moves: true
  prohibit_automatic_delete: true
  new_violation_policy: error
```

## Rules

- Use paths relative to `repo_root`; protected siblings may use `..`.
- Assign one path to one primary role. A nested archive may be listed under `archive` even when its
  parent is generated output.
- Set deprecated paths to `no-new-writes`, `migration-pending`, or `frozen`.
- Keep client inputs and authoritative external evidence under `protected_external`.
- Require approval for moves and prohibit automatic deletion for safe operation.
- Keep an accepted baseline beside the audit records, not inside the manifest.

