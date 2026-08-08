# Token accounting

There are two different measurements and they must never be conflated.

## Codex runtime usage

The current local Skill and telemetry contracts do not expose the Codex conversation's input/output token counters. Do not estimate them from character count, response length, or model name. Record unavailable and explain the measurement gap.

## Provider-reported usage

When an orchestration worker calls a model provider through an API that returns usage, persist the provider response with scripts/usage_ledger.py. Store input tokens, output tokens, total tokens, model, provider, job, task, role, latency, and cost only when supplied by the provider. Missing values remain null.

Example:

    python scripts/usage_ledger.py record --job-id JOB --task-id TASK --role implementer --model MODEL --provider PROVIDER --input-tokens 1200 --output-tokens 800 --latency-ms 2400 --cost 0.02

Inspect a job:

    python scripts/usage_ledger.py list --job-id JOB

The ledger is separate from skill-telemetry because skill-telemetry explicitly forbids token and cost storage. It must not receive prompt, response, tool input, or tool output bodies.
