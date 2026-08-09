# Criteria Index

- `drift-coverage-completeness` [active] Every synced directory and every path referenced from outside the workspace must be covered by the drift validator, with no hardcoded allowlists that silently exclude new entries.
- `ledger-schema-before-use` [active] Any machine-consumed ledger or contract directory must have a documented schema and a validator before its first entry is written.
- `validator-signal-hygiene` [active] Validator warning baselines must be zero or explicitly acknowledged per finding, because a permanently noisy warn channel masks every new warning.
