#!/usr/bin/env node
import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { join } from "node:path";
import { tmpdir } from "node:os";

const root = await mkdtemp(join(tmpdir(), "ai-pm-e2e-"));
const project = join(root, "project");
const emptyProject = join(root, "empty-project");
const state = join(root, "state", "ledger.json");
const runner = new URL("./pm-run.mjs", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
const ledgerCli = new URL("./pm-ledger.mjs", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
const run = (script, args, expected = 0) => {
  const result = spawnSync(process.execPath, [script, ...args], { encoding: "utf8", windowsHide: true });
  assert.equal(result.status, expected, result.stderr || result.stdout);
  return result.stdout ? JSON.parse(result.stdout) : null;
};

try {
  await mkdir(join(project, "scripts"), { recursive: true });
  await mkdir(emptyProject, { recursive: true });
  await writeFile(join(project, "README.md"), "# E2E Project\n\n## Overview\n\nFixture.\n", "utf8");
  await writeFile(join(project, "ROADMAP.md"), "# Roadmap\n\n## Goal\n\nProve orchestration.\n\n## Milestones\n\n### E2E\n", "utf8");
  await writeFile(join(project, "scripts", "prepare.mjs"), "import {writeFile} from 'node:fs/promises'; await writeFile('prepared.txt','ready');\n", "utf8");
  await writeFile(join(project, "scripts", "finish.mjs"), "import {writeFile} from 'node:fs/promises'; await writeFile('finished.txt','done');\n", "utf8");
  await writeFile(join(project, "TODO.md"), `# Tasks

## Action Plan

- [ ] Prepare automatically
  - Status: Todo
  - Assignee-Type: agent
  - Auto: true
  - Command: ["node","scripts/prepare.mjs"]
  - Verification: file:prepared.txt
  - Expected-Output: prepared.txt
  - Ticket: true

- [ ] Confirm external date
  - Status: Todo
  - Assignee-Type: human
  - Assignee: masa
  - Due: 2026-07-26
  - Priority: high
  - Estimated-Minutes: 3
  - Reason: External commitment requires human authority.
  - Expected-Output: Confirmed date
  - Ticket: true

- [ ] Finish after confirmation
  - Status: Todo
  - Assignee-Type: agent
  - Auto: true
  - Dependencies: Confirm external date
  - Command: ["node","scripts/finish.mjs"]
  - Verification: file:finished.txt
  - Expected-Output: finished.txt

- [ ] Publish tracking ticket
  - Status: Todo
  - Assignee-Type: service
  - Assignee: github
  - Expected-Output: Issue URL
  - Verification: issue URL recorded
  - Ticket: true
`, "utf8");

  run(ledgerCli, ["init", "--state", state]);
  run(ledgerCli, ["add-project", "--state", state, "--id", "e2e", "--name", "E2E", "--path", project, "--objective", "Prove orchestration"]);
  run(ledgerCli, ["add-project", "--state", state, "--id", "empty", "--name", "Empty", "--path", emptyProject, "--objective", "Normalize planning"]);

  const first = run(runner, ["--state", state, "--now", "2026-07-27T09:00:00+09:00", "--execute"]);
  assert.deepEqual(first.advanced, ["e2e-prepare-automatically"]);
  assert.deepEqual(first.humanQueue, ["e2e-confirm-external-date"]);
  assert.deepEqual(first.reminders, ["e2e-confirm-external-date"]);
  assert.equal(first.normalized.find((item) => item.projectId === "empty").documents.length, 3);
  assert.equal(await readFile(join(project, "prepared.txt"), "utf8"), "ready");
  await assert.rejects(readFile(join(project, "finished.txt"), "utf8"));
  const outbox = JSON.parse(await readFile(join(root, "state", "ticket-outbox.json"), "utf8"));
  assert.equal(outbox.tickets[0].id, "e2e-confirm-external-date");
  assert.equal(outbox.tickets.length, 2);
  const serviceOutbox = JSON.parse(await readFile(join(root, "state", "service-outbox.json"), "utf8"));
  assert.equal(serviceOutbox.tasks[0].id, "e2e-publish-tracking-ticket");
  assert.equal(serviceOutbox.tasks[0].service, "github");
  assert.equal(serviceOutbox.tasks[0].requiresExplicitDispatchApproval, true);
  const queue = await readFile(join(root, "state", "human-queue.md"), "utf8");
  assert.match(queue, /Confirm external date/);
  assert.match(queue, /3 minutes/);

  run(ledgerCli, ["transition", "--state", state, "--id", "e2e-confirm-external-date", "--status", "done", "--evidence", "User confirmed 2026-08-01"]);
  const normalizationProposal = first.normalized.find((item) => item.projectId === "empty").proposal;
  const second = run(runner, ["--state", state, "--now", "2026-07-27T10:00:00+09:00", "--execute", "--apply-proposal", normalizationProposal]);
  assert.deepEqual(second.advanced, ["e2e-finish-after-confirmation"]);
  assert.equal(await readFile(join(project, "finished.txt"), "utf8"), "done");
  for (const name of ["README.md", "ROADMAP.md", "TODO.md"]) assert.ok(await readFile(join(emptyProject, name), "utf8"));
  const finalLedger = JSON.parse(await readFile(state, "utf8"));
  assert.equal(finalLedger.tasks.find((task) => task.id === "e2e-finish-after-confirmation").status, "done");
  assert.equal(finalLedger.events.filter((event) => event.kind === "human-reminded").length, 1);

  const staleProject = join(root, "stale-project");
  await mkdir(staleProject, { recursive: true });
  run(ledgerCli, ["add-project", "--state", state, "--id", "stale", "--name", "Stale", "--path", staleProject, "--objective", "Reject stale proposals"]);
  const staleScan = run(runner, ["--state", state, "--project", "stale", "--now", "2026-07-27T11:00:00+09:00"]);
  await writeFile(join(staleProject, "README.md"), "# Concurrent edit\n", "utf8");
  const staleApply = spawnSync(process.execPath, [runner, "--state", state, "--project", "stale", "--now", "2026-07-27T11:01:00+09:00", "--apply-proposal", staleScan.normalized[0].proposal], { encoding: "utf8", windowsHide: true });
  assert.notEqual(staleApply.status, 0);
  assert.match(staleApply.stderr, /stale/i);

  process.stdout.write(`${JSON.stringify({
    ok: true,
    observedProjects: first.observed.length,
    autoTasksCompleted: first.advanced.length + second.advanced.length,
    humanTasksCompleted: 1,
    reminders: first.reminders.length,
    normalizationDocumentsApplied: second.normalized.find((item) => item.projectId === "empty").applied,
    ticketsPrepared: outbox.tickets.length,
    serviceTasksPrepared: serviceOutbox.tasks.length,
    staleProposalRejected: true,
  }, null, 2)}\n`);
} finally {
  await rm(root, { recursive: true, force: true });
}
