# GitHub adapter contract

The adapter is the only layer allowed to translate the Skill's provider-neutral operations into GitHub API or connector calls.

## Required operations

- `fetch_repository(ref, fields)`
- `fetch_file(ref, path, revision)`
- `list_issues(ref, state, limit)`
- `list_pull_requests(ref, state, limit)`
- `fetch_ci(ref, revision, limit)`

The adapter receives actor identity, repository scope, permission level, deadline, and request limit from Gatekeeper. It must not receive raw credentials in model context. It returns bounded records plus opaque evidence references. It must reject write operations for this Skill before network dispatch.
