#!/usr/bin/env node
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { homedir } from "node:os";

const VALID_STATUS = new Set(["backlog", "ready", "in-progress", "waiting-human", "waiting-service", "blocked", "verification", "done", "cancelled"]);
const VALID_ASSIGNEE = new Set(["agent", "human", "service"]);
const VALID_PRIORITY = new Set(["critical", "high", "normal", "low"]);
const DEFAULT_PATH = resolve(homedir(), ".codex", "ai-project-manager", "ledger.json");
const args = process.argv.slice(2);
const command = args.shift() || "help";

function option(name, fallback = "") {
  const index = args.indexOf(`--${name}`);
  return index >= 0 ? args[index + 1] ?? fallback : fallback;
}

function values(name) {
  const raw = option(name);
  return raw ? raw.split(",").map((v) => v.trim()).filter(Boolean) : [];
}

function now() {
  return new Date().toISOString();
}

function emptyLedger() {
  return { schemaVersion: 1, updatedAt: now(), projects: [], tasks: [], events: [] };
}

async function load(path) {
  try {
    const value = JSON.parse(await readFile(path, "utf8"));
    if (command !== "remove-task") validateLedger(value);
    return value;
  } catch (error) {
    if (error?.code === "ENOENT") return emptyLedger();
    throw error;
  }
}

async function save(path, ledger) {
  validateLedger(ledger);
  ledger.updatedAt = now();
  await mkdir(dirname(path), { recursive: true });
  const temporary = `${path}.${process.pid}.tmp`;
  await writeFile(temporary, `${JSON.stringify(ledger, null, 2)}\n`, "utf8");
  await rename(temporary, path);
}

function validateLedger(ledger) {
  if (!ledger || ledger.schemaVersion !== 1 || !Array.isArray(ledger.projects) || !Array.isArray(ledger.tasks) || !Array.isArray(ledger.events)) {
    throw new Error("Invalid ledger structure.");
  }
  const ids = new Set();
  const projectIds = new Set(ledger.projects.map((project) => project.id));
  for (const task of ledger.tasks) {
    if (!task.id || ids.has(task.id)) throw new Error(`Invalid or duplicate task id: ${task.id || "<empty>"}`);
    ids.add(task.id);
    if (!VALID_STATUS.has(task.status)) throw new Error(`Invalid status for ${task.id}: ${task.status}`);
    if (!VALID_ASSIGNEE.has(task.assigneeType)) throw new Error(`Invalid assigneeType for ${task.id}: ${task.assigneeType}`);
    if (!VALID_PRIORITY.has(task.priority)) throw new Error(`Invalid priority for ${task.id}: ${task.priority}`);
    if (!projectIds.has(task.projectId)) throw new Error(`Unknown project for ${task.id}: ${task.projectId}`);
  }
  for (const task of ledger.tasks) {
    for (const dependency of task.dependencies || []) {
      if (!ids.has(dependency)) throw new Error(`Unknown dependency for ${task.id}: ${dependency}`);
      if (dependency === task.id) throw new Error(`Task cannot depend on itself: ${task.id}`);
    }
  }
  const byId = new Map(ledger.tasks.map((task) => [task.id, task]));
  const visiting = new Set();
  const visited = new Set();
  function visit(id) {
    if (visiting.has(id)) throw new Error(`Dependency cycle detected at: ${id}`);
    if (visited.has(id)) return;
    visiting.add(id);
    for (const dependency of byId.get(id)?.dependencies || []) visit(dependency);
    visiting.delete(id);
    visited.add(id);
  }
  for (const id of ids) visit(id);
}

function required(name) {
  const value = option(name);
  if (!value) throw new Error(`Missing --${name}.`);
  return value;
}

function emit(value) {
  process.stdout.write(`${typeof value === "string" ? value : JSON.stringify(value, null, 2)}\n`);
}

function selectTasks(ledger) {
  return ledger.tasks.filter((task) =>
    (!option("status") || task.status === option("status")) &&
    (!option("assignee-type") || task.assigneeType === option("assignee-type")) &&
    (!option("project") || task.projectId === option("project"))
  );
}

function humanQueue(tasks) {
  const rank = { critical: 0, high: 1, normal: 2, low: 3 };
  return tasks
    .filter((task) => task.assigneeType === "human" && !["done", "cancelled"].includes(task.status))
    .sort((a, b) => (rank[a.priority] - rank[b.priority]) || String(a.due || "9999").localeCompare(String(b.due || "9999")));
}

const statePath = resolve(option("state", DEFAULT_PATH));
const ledger = await load(statePath);

switch (command) {
  case "init":
    await save(statePath, ledger);
    emit({ ok: true, statePath });
    break;
  case "add-project": {
    const id = required("id");
    const existing = ledger.projects.find((project) => project.id === id);
    const project = {
      id,
      name: required("name"),
      path: resolve(required("path")),
      objective: option("objective"),
      updatedAt: now(),
    };
    if (existing) Object.assign(existing, project);
    else ledger.projects.push(project);
    ledger.events.push({ at: now(), kind: "project-upserted", projectId: id });
    await save(statePath, ledger);
    emit(project);
    break;
  }
  case "upsert-task": {
    const id = required("id");
    const existing = ledger.tasks.find((task) => task.id === id);
    const task = {
      id,
      projectId: required("project"),
      title: required("title"),
      objective: option("objective"),
      assigneeType: required("assignee-type"),
      assignee: option("assignee"),
      requiredCapability: option("capability"),
      status: option("status", "backlog"),
      priority: option("priority", "normal"),
      due: option("due"),
      estimatedMinutes: Number(option("estimated-minutes", "0")) || 0,
      dependencies: values("dependencies"),
      expectedOutput: option("expected-output"),
      verification: option("verification"),
      reason: option("reason"),
      evidence: existing?.evidence || [],
      lastReminderAt: existing?.lastReminderAt || "",
      createdAt: existing?.createdAt || now(),
      updatedAt: now(),
    };
    if (!ledger.projects.some((project) => project.id === task.projectId)) throw new Error(`Unknown project: ${task.projectId}`);
    if (existing) Object.assign(existing, task);
    else ledger.tasks.push(task);
    ledger.events.push({ at: now(), kind: "task-upserted", projectId: task.projectId, taskId: id });
    await save(statePath, ledger);
    emit(task);
    break;
  }
  case "transition": {
    const task = ledger.tasks.find((item) => item.id === required("id"));
    if (!task) throw new Error("Task not found.");
    const status = required("status");
    if (!VALID_STATUS.has(status)) throw new Error(`Invalid status: ${status}`);
    if (status === "done" && !option("evidence")) throw new Error("Done requires --evidence.");
    task.status = status;
    task.updatedAt = now();
    if (option("evidence")) task.evidence.push({ at: now(), value: option("evidence") });
    ledger.events.push({ at: now(), kind: "task-transitioned", projectId: task.projectId, taskId: task.id, status });
    await save(statePath, ledger);
    emit(task);
    break;
  }
  case "reminded": {
    const task = ledger.tasks.find((item) => item.id === required("id"));
    if (!task) throw new Error("Task not found.");
    task.lastReminderAt = now();
    task.updatedAt = now();
    ledger.events.push({ at: now(), kind: "human-reminded", projectId: task.projectId, taskId: task.id });
    await save(statePath, ledger);
    emit(task);
    break;
  }
  case "remove-task": {
    const id = required("id");
    const reason = required("reason");
    const index = ledger.tasks.findIndex((item) => item.id === id);
    if (index < 0) throw new Error("Task not found.");
    const [removed] = ledger.tasks.splice(index, 1);
    ledger.events.push({ at: now(), kind: "task-removed", projectId: removed.projectId, taskId: id, summary: reason });
    await save(statePath, ledger);
    emit({ removed: id, reason });
    break;
  }
  case "remove-project": {
    const id = required("id");
    const reason = required("reason");
    const projectIndex = ledger.projects.findIndex((item) => item.id === id);
    if (projectIndex < 0) throw new Error("Project not found.");
    const projectTasks = ledger.tasks.filter((item) => item.projectId === id);
    if (projectTasks.length && option("cascade") !== "true") throw new Error("Project has tasks; pass --cascade true to remove them.");
    ledger.tasks = ledger.tasks.filter((item) => item.projectId !== id);
    ledger.projects.splice(projectIndex, 1);
    ledger.events.push({ at: now(), kind: "project-removed", projectId: id, summary: `${reason}; removedTasks=${projectTasks.length}` });
    await save(statePath, ledger);
    emit({ removed: id, removedTasks: projectTasks.map((task) => task.id), reason });
    break;
  }
  case "event":
    ledger.events.push({ at: now(), kind: required("kind"), projectId: option("project"), taskId: option("task"), summary: required("summary") });
    await save(statePath, ledger);
    emit(ledger.events.at(-1));
    break;
  case "list":
    emit(selectTasks(ledger));
    break;
  case "human-queue":
    emit(humanQueue(selectTasks(ledger)));
    break;
  case "brief": {
    const queue = humanQueue(selectTasks(ledger));
    const active = selectTasks(ledger).filter((task) => ["ready", "in-progress", "verification", "blocked"].includes(task.status));
    const lines = ["# AI Project Manager", "", `Updated: ${ledger.updatedAt}`, "", "## Human queue", ""];
    if (!queue.length) lines.push("- Nothing needs human attention.");
    for (const task of queue) lines.push(`- [${task.priority}] ${task.title} (${task.status})${task.due ? ` — due ${task.due}` : ""}${task.estimatedMinutes ? ` — ${task.estimatedMinutes} min` : ""}\n  - Reason: ${task.reason || "Human capability or authority is required."}\n  - Complete with: ${task.expectedOutput || "Expected output not defined."}`);
    lines.push("", "## Active AI/service work", "");
    if (!active.length) lines.push("- No active work.");
    for (const task of active) lines.push(`- [${task.status}] ${task.title} — ${task.assigneeType}:${task.assignee || "unassigned"}`);
    emit(lines.join("\n"));
    break;
  }
  case "validate":
    validateLedger(ledger);
    emit({ ok: true, projects: ledger.projects.length, tasks: ledger.tasks.length, events: ledger.events.length, statePath });
    break;
  default:
    emit(`Usage:
  pm-ledger.mjs init [--state PATH]
  pm-ledger.mjs add-project --id ID --name NAME --path PATH [--objective TEXT]
  pm-ledger.mjs upsert-task --id ID --project ID --title TEXT --assignee-type agent|human|service [options]
  pm-ledger.mjs transition --id ID --status STATUS [--evidence TEXT]
  pm-ledger.mjs reminded --id ID
  pm-ledger.mjs remove-task --id ID --reason TEXT
  pm-ledger.mjs remove-project --id ID --reason TEXT [--cascade true]
  pm-ledger.mjs event --kind KIND --summary TEXT [--project ID] [--task ID]
  pm-ledger.mjs list|human-queue|brief|validate [filters] [--state PATH]`);
}
