import assert from "node:assert/strict";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawn } from "node:child_process";

const directory = await mkdtemp(join(tmpdir(), "roadmap-live-"));
const ledgerPath = join(directory, "ledger.json");
const port = 14_000 + (process.pid % 10_000);
const ledger = updatedAt => ({
  schemaVersion: 1,
  updatedAt,
  projects: [{ id: "p1", name: "Live test" }],
  tasks: [{
    id: "t1", projectId: "p1", title: "Observe", status: "ready",
    estimatedMinutes: 10, dependencies: [],
    execution: {
      actorType: "agent", actorId: "agent-1", displayName: "Sol",
      role: "observer", workspacePath: "src", heartbeatAt: updatedAt, state: "working",
    },
  }],
  events: [],
});
await writeFile(ledgerPath, JSON.stringify(ledger("2026-07-28T00:00:00.000Z")));

const child = spawn(process.execPath, [
  new URL("./roadmap-server.mjs", import.meta.url).pathname.slice(1),
  "--ledger", ledgerPath, "--port", String(port),
], { stdio: "ignore", windowsHide: true });

try {
  let response;
  for (let attempt = 0; attempt < 30; attempt += 1) {
    try {
      response = await fetch(`http://127.0.0.1:${port}/api/state`);
      if (response.ok) break;
    } catch {}
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.equal(response?.ok, true, "state endpoint did not start");

  const controller = new AbortController();
  const events = await fetch(`http://127.0.0.1:${port}/events`, {
    signal: controller.signal,
  });
  assert.match(events.headers.get("content-type") || "", /text\/event-stream/);
  const reader = events.body.getReader();
  await reader.read();
  await writeFile(ledgerPath, JSON.stringify(ledger("2026-07-28T00:01:00.000Z")));

  const timeout = setTimeout(() => controller.abort(), 7000);
  let payload = "";
  while (!payload.includes("event: roadmap-update")) {
    const chunk = await reader.read();
    if (chunk.done) break;
    payload += new TextDecoder().decode(chunk.value);
  }
  clearTimeout(timeout);
  controller.abort();
  assert.match(payload, /event: roadmap-update/);
  const updated = await fetch(`http://127.0.0.1:${port}/api/state`).then(value => value.json());
  assert.equal(updated.ledgerUpdatedAt, "2026-07-28T00:01:00.000Z");
  assert.equal(updated.nodes.find(node => node.id === "t1").executions[0].displayName, "Sol");
  console.log(JSON.stringify({ ok: true, realtime: "SSE ledger update observed" }));
} finally {
  child.kill();
  await rm(directory, { recursive: true, force: true });
}
