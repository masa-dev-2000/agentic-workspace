# Improvement staging

This directory is the public contract boundary for disabled improvement
proposals and bounded experiment metadata. Private evidence remains in the
owning local ledger and is referenced only through privacy-safe opaque IDs.

Staged material must remain inactive until an exact, current approval binds the
proposal, target contract content digest, and ChangeSet digest. Approval of a
document does not authorize Hook cutover, Plugin packaging, installation, or
activation.
