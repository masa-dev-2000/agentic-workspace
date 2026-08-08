import assert from "node:assert/strict";
import { normalizeLedger } from "./roadmap-data.mjs";

const ledger = {
  schemaVersion: 1,
  updatedAt: "2026-07-28T00:00:00.000Z",
  projects: [{ id: "demo", name: "Demo", path: "C:\\demo", objective: "Ship", updatedAt: "2026-07-28T00:00:00.000Z" }],
  tasks: [
    {
      id: "design", projectId: "demo", title: "設計", objective: "仕様確定",
      assigneeType: "human", status: "done", priority: "high", dependencies: [],
      estimatedMinutes: 30, evidence: [{ at: "2026-07-28T00:00:00Z", value: "approved" }],
      execution: {
        actorType: "agent", actorId: "sol-1", displayName: "Sol",
        heartbeatAt: "2026-07-28T00:30:00Z", state: "finished",
      },
      milestoneId: "build", milestoneTitle: "構築", updatedAt: "2026-07-28T00:00:00Z",
    },
    {
      id: "api", projectId: "demo", title: "API", objective: "データ提供",
      assigneeType: "agent", status: "in-progress", priority: "critical", dependencies: ["design"],
      estimate: { optimisticMinutes: 30, likelyMinutes: 60, pessimisticMinutes: 120 },
      execution: {
        actorType: "agent", actorId: "sol-1", displayName: "Sol", role: "implementation",
        model: "gpt-5.6-sol", workspacePath: "C:\\demo\\services\\api",
        startedAt: "2026-07-28T00:55:00Z", heartbeatAt: "2026-07-28T00:59:30Z", state: "working",
      },
      milestoneId: "build", milestoneTitle: "構築", updatedAt: "2026-07-28T00:00:00Z",
    },
    {
      id: "ui", projectId: "demo", title: "UI", objective: "画面提供",
      assigneeType: "agent", status: "ready", priority: "normal", dependencies: ["design"],
      estimatedMinutes: 90, parentTaskId: "release", updatedAt: "2026-07-28T00:00:00Z",
    },
    {
      id: "release", projectId: "demo", title: "公開", objective: "利用開始",
      assigneeType: "human", status: "waiting-human", priority: "high", dependencies: ["api", "ui"],
      dependencyEvidence: [{
        dependencyId: "api", verified: true, source: "tests/api-release.test.ts",
        kind: "release-gate", confidence: "high",
      }],
      estimatedMinutes: 20, reason: "公開承認待ち", updatedAt: "2026-07-28T00:00:00Z",
    },
  ],
  events: [],
};

const output = normalizeLedger(ledger, {
  projectId: "demo",
  now: "2026-07-28T01:00:00.000Z",
});

assert.equal(output.summary.total, 4);
assert.equal(output.summary.done, 1);
assert.equal(output.summary.blocked, 1);
assert.equal(output.summary.confidence, "low");
assert.ok(output.graph.nodes.some(node => node.id === "milestone:build"));
assert.ok(output.nodes.find(node => node.id === "release").children.includes("ui"));
assert.deepEqual(output.nodes.find(node => node.id === "api").dependencies, ["design"]);
assert.equal(output.nodes.find(node => node.id === "release").blocker, "公開承認待ち");
assert.equal(output.nodes.find(node => node.id === "release").isBlocking, true);
assert.deepEqual(output.nodes.find(node => node.id === "api").executions[0], {
  actorType: "agent",
  actorId: "sol-1",
  displayName: "Sol",
  role: "implementation",
  model: "gpt-5.6-sol",
  workspace: "Demo › services › api",
  startedAt: "2026-07-28T00:55:00Z",
  heartbeatAt: "2026-07-28T00:59:30Z",
  state: "working",
});
assert.equal(output.nodes.find(node => node.id === "milestone:build").executions[0].displayName, "Sol");
assert.equal(output.nodes.find(node => node.id === "milestone:build").executions.length, 1);
assert.equal(output.nodes.find(node => node.id === "milestone:build").executions[0].state, "working");
assert.ok(Date.parse(output.summary.eta.pessimistic) >= Date.parse(output.summary.eta.likely));
assert.ok(Date.parse(output.summary.eta.likely) >= Date.parse(output.summary.eta.optimistic));
assert.ok(output.graph.edges.every(edge => edge.from !== edge.to));
assert.deepEqual(output.summary.dependencyEvidence, { verified: 1, total: 4, coverage: 25 });
assert.ok(output.graph.edges.some(edge =>
  edge.evidence?.verified === false &&
  edge.evidenceItems?.some(item => item.verified === true)
));

const deepLedger = {
  schemaVersion: 1,
  updatedAt: "2026-07-28T00:00:00Z",
  projects: [{ id: "large", name: "大規模案件", path: "C:\\work\\large" }],
  tasks: [
    { id: "phase", projectId: "large", title: "MVP", status: "in-progress", dependencies: [] },
    { id: "milestone", projectId: "large", parentTaskId: "phase", title: "取込", status: "ready", dependencies: [] },
    { id: "deliverable", projectId: "large", parentTaskId: "milestone", title: "OCR", status: "ready", dependencies: [] },
    { id: "task", projectId: "large", parentTaskId: "deliverable", title: "抽出器", status: "backlog", dependencies: [] },
  ],
  events: [],
};
const deep = normalizeLedger(deepLedger, {
  projectId: "large",
  now: "2026-07-28T01:00:00Z",
});
assert.deepEqual(deep.graph.nodes.map(node => node.id), ["phase"]);
assert.deepEqual(deep.nodes.find(node => node.id === "phase").detailNodeIds, ["milestone"]);
assert.deepEqual(deep.nodes.find(node => node.id === "milestone").detailNodeIds, ["deliverable"]);
assert.deepEqual(deep.nodes.find(node => node.id === "deliverable").detailNodeIds, ["deliverable"]);
assert.deepEqual(deep.nodes.find(node => node.id === "deliverable").collapsedDescendantIds, ["task"]);
assert.equal(deep.nodes.find(node => node.id === "deliverable").displayDepth, 3);

const staleLedger = structuredClone(ledger);
staleLedger.tasks.find(task => task.id === "api").execution.heartbeatAt = "2026-07-28T00:50:00Z";
const staleOutput = normalizeLedger(staleLedger, {
  projectId: "demo",
  now: "2026-07-28T01:00:00.000Z",
});
assert.equal(staleOutput.nodes.find(node => node.id === "api").executions[0].state, "stale");

const nonBlockingWaitLedger = structuredClone(ledger);
nonBlockingWaitLedger.tasks.push({
  id: "advisory", projectId: "demo", title: "任意確認", objective: "補足確認",
  assigneeType: "human", status: "waiting-human", priority: "normal", dependencies: [],
  estimatedMinutes: 5, reason: "担当者確認待ち", updatedAt: "2026-07-28T00:00:00Z",
});
const nonBlockingWait = normalizeLedger(nonBlockingWaitLedger, {
  projectId: "demo",
  now: "2026-07-28T01:00:00.000Z",
});
assert.equal(nonBlockingWait.nodes.find(node => node.id === "advisory").status, "waiting");
assert.equal(nonBlockingWait.nodes.find(node => node.id === "advisory").waitingLabel, "確認待ち");
assert.equal(nonBlockingWait.nodes.find(node => node.id === "advisory").isBlocking, false);
assert.equal(nonBlockingWait.nodes.find(node => node.id === "advisory").blocker, "");
assert.equal(nonBlockingWait.nodes.find(node => node.id === "advisory").waitReason, "担当者確認待ち");

console.log(JSON.stringify({
  ok: true,
  nodes: output.graph.nodes.length,
  edges: output.graph.edges.length,
  eta: output.summary.eta,
}, null, 2));
