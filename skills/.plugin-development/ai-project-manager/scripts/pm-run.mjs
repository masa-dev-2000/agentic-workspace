#!/usr/bin/env node
import { createHash } from "node:crypto";
import { access, mkdir, readFile, readdir, rename, stat, writeFile } from "node:fs/promises";
import { constants } from "node:fs";
import { spawnSync } from "node:child_process";
import { basename, dirname, join, relative, resolve } from "node:path";
import { homedir } from "node:os";

const argv = process.argv.slice(2);
const option = (name, fallback = "") => {
  const index = argv.indexOf(`--${name}`);
  return index >= 0 ? argv[index + 1] ?? fallback : fallback;
};
const flag = (name) => argv.includes(`--${name}`);
const statePath = resolve(option("state", join(homedir(), ".codex", "ai-project-manager", "ledger.json")));
const at = new Date(option("now") || Date.now());
const iso = () => at.toISOString();
const hash = (text) => createHash("sha256").update(text).digest("hex");
const slug = (text) => String(text).toLowerCase().normalize("NFKC").replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "").slice(0, 64) || "task";
const normalize = (text) => String(text ?? "").replace(/\r\n/g, "\n");

async function exists(path) {
  try { await access(path, constants.F_OK); return true; } catch { return false; }
}

async function read(path) {
  try { return await readFile(path, "utf8"); } catch (error) { if (error.code === "ENOENT") return ""; throw error; }
}

async function atomicJson(path, value) {
  await mkdir(dirname(path), { recursive: true });
  const temporary = `${path}.${process.pid}.tmp`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, "utf8");
  await rename(temporary, path);
}

async function loadLedger() {
  const raw = await read(statePath);
  if (!raw) return { schemaVersion: 1, updatedAt: iso(), projects: [], tasks: [], events: [] };
  const ledger = JSON.parse(raw);
  ledger.projects ||= [];
  ledger.tasks ||= [];
  ledger.events ||= [];
  const projectIds = new Set(ledger.projects.map((project) => project.id));
  for (const task of ledger.tasks) {
    if (!projectIds.has(task.projectId)) throw new Error(`Ledger task references an unknown project: ${task.id} -> ${task.projectId}`);
  }
  return ledger;
}

function section(text, heading) {
  const lines = normalize(text).split("\n");
  for (let i = 0; i < lines.length; i++) {
    const match = /^(#{1,6})\s+(.+?)\s*$/.exec(lines[i]);
    if (!match || match[2].trim().toLowerCase() !== heading.toLowerCase()) continue;
    const level = match[1].length;
    let end = lines.length;
    for (let j = i + 1; j < lines.length; j++) {
      const next = /^(#{1,6})\s+/.exec(lines[j]);
      if (next && next[1].length <= level) { end = j; break; }
    }
    return lines.slice(i + 1, end).join("\n").trim();
  }
  return "";
}

function parseActions(todo, projectId) {
  const body = section(todo, "Action Plan");
  const lines = normalize(body).split("\n");
  const actions = [];
  let current = null;
  for (const line of lines) {
    const action = /^\s*-\s+\[([ xX])\]\s+(.+?)\s*$/.exec(line);
    if (action) {
      if (current) actions.push(current);
      current = { checked: action[1].toLowerCase() === "x", title: action[2].trim(), fields: {} };
      continue;
    }
    if (!current) continue;
    const field = /^\s{2,}-\s+([^:]+):\s*(.*?)\s*$/.exec(line);
    if (field) current.fields[field[1].trim().toLowerCase()] = field[2].trim();
  }
  if (current) actions.push(current);
  const seen = new Map();
  return actions.map((action) => {
    const base = `${projectId}-${slug(action.title)}`;
    const count = (seen.get(base) || 0) + 1;
    seen.set(base, count);
    return { ...action, id: count === 1 ? base : `${base}-${count}` };
  });
}

function route(action) {
  const explicit = action.fields["assignee-type"]?.toLowerCase();
  if (["agent", "human", "service"].includes(explicit)) return explicit;
  const text = `${action.title} ${action.fields.notes || ""}`.toLowerCase();
  if (/(approve|confirm|call|contact|negotiate|payment|physical|承認|確認|電話|連絡|交渉|支払|現地)/u.test(text)) return "human";
  if (action.fields.service) return "service";
  return "agent";
}

function parseJsonArray(value, label) {
  if (!value) return null;
  const parsed = JSON.parse(value);
  if (!Array.isArray(parsed) || !parsed.length || parsed.some((item) => typeof item !== "string")) throw new Error(`${label} must be a non-empty JSON string array.`);
  return parsed;
}

function priority(value = "normal") {
  const normalized = value.toLowerCase();
  return ["critical", "high", "normal", "low"].includes(normalized) ? normalized : "normal";
}

function desiredStatus(action, assigneeType) {
  if (action.checked || action.fields.status?.toLowerCase() === "done") return "done";
  if (assigneeType === "human") return "waiting-human";
  if (assigneeType === "service") return "waiting-service";
  return "ready";
}

async function scanFiles(root, limit = 10000) {
  const ignored = new Set([".git", "node_modules", "dist", "build", ".next", "target", "vendor"]);
  const queue = [root];
  let files = 0, source = 0, tests = 0, docs = 0;
  while (queue.length && files < limit) {
    const current = queue.shift();
    for (const entry of await readdir(current, { withFileTypes: true }).catch(() => [])) {
      if (entry.name.startsWith(".") || ignored.has(entry.name)) continue;
      const path = join(current, entry.name);
      if (entry.isDirectory()) queue.push(path);
      else {
        files++;
        if (/\.(md|txt|json)$/i.test(entry.name)) docs++;
        else if (/(test|spec)\./i.test(entry.name)) tests++;
        else if (/\.(js|mjs|cjs|ts|tsx|jsx|py|ps1|rs|go|java)$/i.test(entry.name)) source++;
      }
      if (files >= limit) break;
    }
  }
  return { files, source, tests, docs, truncated: files >= limit };
}

function gitEvidence(root) {
  const inside = spawnSync("git", ["-C", root, "rev-parse", "--is-inside-work-tree"], { encoding: "utf8", windowsHide: true });
  if (inside.status !== 0) return { available: false, branch: "", changes: [], commits: [] };
  const branch = spawnSync("git", ["-C", root, "branch", "--show-current"], { encoding: "utf8", windowsHide: true });
  const statusResult = spawnSync("git", ["-C", root, "status", "--porcelain"], { encoding: "utf8", windowsHide: true });
  const log = spawnSync("git", ["-C", root, "log", "-20", "--pretty=format:%H%x09%cI%x09%s"], { encoding: "utf8", windowsHide: true });
  return {
    available: true,
    branch: branch.stdout.trim(),
    changes: statusResult.stdout.split(/\r?\n/).filter(Boolean),
    commits: log.stdout.split(/\r?\n/).filter(Boolean).map((line) => {
      const [commit, date, ...subject] = line.split("\t");
      return { commit, date, subject: subject.join("\t") };
    }),
  };
}

async function observe(project, storageRoot) {
  const root = resolve(project.path);
  const [readme, roadmap, todo, packageJson, fileEvidence] = await Promise.all([
    read(join(root, "README.md")), read(join(root, "ROADMAP.md")), read(join(root, "TODO.md")), read(join(root, "package.json")), scanFiles(root),
  ]);
  const docs = { readme: hash(readme), roadmap: hash(roadmap), todo: hash(todo), packageJson: hash(packageJson) };
  const snapshot = {
    projectId: project.id,
    observedAt: iso(),
    root,
    objective: section(roadmap, "Goal") || project.objective || "",
    planning: {
      hasReadme: Boolean(readme),
      hasRoadmap: Boolean(roadmap),
      hasTodo: Boolean(todo),
      actionCount: parseActions(todo, project.id).length,
    },
    docs,
    files: fileEvidence,
    git: gitEvidence(root),
  };
  const snapshotPath = join(storageRoot, "snapshots", `${project.id}.json`);
  const previousRaw = await read(snapshotPath);
  const previous = previousRaw ? JSON.parse(previousRaw) : null;
  snapshot.changed = !previous || JSON.stringify(previous.docs) !== JSON.stringify(snapshot.docs) || JSON.stringify(previous.git?.changes) !== JSON.stringify(snapshot.git.changes);
  await atomicJson(snapshotPath, snapshot);
  return { snapshot, readme, roadmap, todo, actions: parseActions(todo, project.id) };
}

function proposedDocuments(project, observation) {
  const root = project.path;
  const name = project.name || basename(root);
  const today = iso().slice(0, 10);
  const result = [];
  if (!observation.readme) result.push({ name: "README.md", originalHash: hash(""), proposed: `# ${name}\n\n## Overview\n\nOverview not defined.\n` });
  if (!observation.roadmap) result.push({ name: "ROADMAP.md", originalHash: hash(""), proposed: `# Roadmap\n\nReviewed: ${today}\n\n## Goal\n\n${project.objective || "Goal not defined."}\n\n## Milestones\n` });
  if (!observation.todo) result.push({ name: "TODO.md", originalHash: hash(""), proposed: `# Tasks\n\nReviewed: ${today}\n\n## Action Plan\n` });
  return result;
}

async function writeNormalizationProposal(project, observation, storageRoot) {
  const documents = proposedDocuments(project, observation);
  if (!documents.length) return null;
  const proposal = { id: `${project.id}-${Date.now()}`, projectId: project.id, projectPath: project.path, createdAt: iso(), documents };
  const path = join(storageRoot, "proposals", `${project.id}-normalization.json`);
  await atomicJson(path, proposal);
  return { path, proposal };
}

async function applyNormalization(proposalResult, storageRoot) {
  if (!proposalResult) return 0;
  const { proposal } = proposalResult;
  const backup = join(storageRoot, "backups", proposal.id);
  await mkdir(backup, { recursive: true });
  for (const document of proposal.documents) {
    const target = join(proposal.projectPath, document.name);
    const current = await read(target);
    if (hash(current) !== document.originalHash) throw new Error(`Normalization proposal is stale: ${document.name}`);
  }
  for (const document of proposal.documents) {
    const target = join(proposal.projectPath, document.name);
    const current = await read(target);
    await writeFile(join(backup, current ? document.name : `${document.name}.missing`), current, "utf8");
    await writeFile(target, document.proposed, { encoding: "utf8", flag: current ? "w" : "wx" });
  }
  await atomicJson(join(backup, "approval.json"), { proposalId: proposal.id, appliedAt: iso(), documents: proposal.documents.map((document) => document.name) });
  return proposal.documents.length;
}

function syncTasks(ledger, project, actions) {
  const taskById = new Map(ledger.tasks.map((task) => [task.id, task]));
  for (const action of actions) {
    const assigneeType = route(action);
    const existing = taskById.get(action.id);
    const dependencies = (action.fields.dependencies || "").split(",").map((item) => item.trim()).filter(Boolean).map((item) => item.includes("-") ? item : `${project.id}-${slug(item)}`);
    const task = {
      id: action.id,
      projectId: project.id,
      title: action.title,
      objective: action.fields.milestone || project.objective || "",
      assigneeType,
      assignee: action.fields.assignee || (assigneeType === "agent" ? "codex" : ""),
      requiredCapability: action.fields.capability || "",
      status: existing?.status === "done" ? "done" : desiredStatus(action, assigneeType),
      priority: priority(action.fields.priority),
      due: action.fields.due || "",
      estimatedMinutes: Number(action.fields["estimated-minutes"] || 0) || 0,
      dependencies,
      expectedOutput: action.fields["expected-output"] || "",
      verification: action.fields.verification || "",
      reason: action.fields.reason || action.fields.notes || "",
      evidence: existing?.evidence || [],
      lastReminderAt: existing?.lastReminderAt || "",
      command: parseJsonArray(action.fields.command, `Command for ${action.id}`),
      auto: action.fields.auto?.toLowerCase() === "true",
      ticket: action.fields.ticket?.toLowerCase() === "true",
      createdAt: existing?.createdAt || iso(),
      updatedAt: iso(),
    };
    if (existing) Object.assign(existing, task);
    else ledger.tasks.push(task);
  }
}

function dependenciesDone(task, ledger) {
  return (task.dependencies || []).every((id) => ledger.tasks.find((candidate) => candidate.id === id)?.status === "done");
}

function verify(task, root) {
  if (!task.verification) return { ok: false, evidence: "Verification not defined." };
  if (task.verification.startsWith("file:")) {
    const target = resolve(root, task.verification.slice(5).trim());
    const inside = relative(root, target);
    if (inside.startsWith("..") || resolve(root) === target) return { ok: false, evidence: "Verification path escaped project." };
    return { ok: false, pendingFile: target };
  }
  if (task.verification.startsWith("command:")) {
    const command = parseJsonArray(task.verification.slice(8).trim(), `Verification for ${task.id}`);
    const result = spawnSync(command[0], command.slice(1), { cwd: root, encoding: "utf8", windowsHide: true, shell: false });
    return { ok: result.status === 0, evidence: result.status === 0 ? `Verification command passed: ${JSON.stringify(command)}` : `Verification command failed: ${result.stderr || result.stdout}` };
  }
  return { ok: false, evidence: "Unsupported verification." };
}

async function executeReady(ledger, project) {
  const advanced = [];
  for (const task of ledger.tasks.filter((item) => item.projectId === project.id && item.assigneeType === "agent" && !["done", "cancelled"].includes(item.status))) {
    if (!dependenciesDone(task, ledger)) { task.status = "blocked"; continue; }
    if (!task.auto || !task.command) { task.status = "ready"; continue; }
    task.status = "in-progress";
    const result = spawnSync(task.command[0], task.command.slice(1), { cwd: project.path, encoding: "utf8", windowsHide: true, shell: false, timeout: 300000 });
    if (result.status !== 0) {
      task.status = "blocked";
      task.evidence.push({ at: iso(), value: `Command failed: ${result.stderr || result.stdout || result.error?.message}` });
      continue;
    }
    task.status = "verification";
    const check = verify(task, project.path);
    if (check.pendingFile) check.ok = await exists(check.pendingFile), check.evidence = check.ok ? `Verified file: ${relative(project.path, check.pendingFile)}` : `Missing file: ${relative(project.path, check.pendingFile)}`;
    if (check.ok) {
      task.status = "done";
      task.evidence.push({ at: iso(), value: check.evidence });
      advanced.push(task.id);
    } else {
      task.status = "blocked";
      task.evidence.push({ at: iso(), value: check.evidence });
    }
    task.updatedAt = iso();
  }
  return advanced;
}

function humanQueue(ledger) {
  const order = { critical: 0, high: 1, normal: 2, low: 3 };
  return ledger.tasks.filter((task) => task.assigneeType === "human" && !["done", "cancelled"].includes(task.status))
    .sort((a, b) => order[a.priority] - order[b.priority] || String(a.due || "9999").localeCompare(String(b.due || "9999")));
}

function dueForReminder(task) {
  if (!task.due) return false;
  const due = new Date(task.due.length === 10 ? `${task.due}T23:59:59` : task.due);
  if (Number.isNaN(due.valueOf()) || due > at) return false;
  if (!task.lastReminderAt) return true;
  return at - new Date(task.lastReminderAt) >= 24 * 60 * 60 * 1000;
}

async function writeOutputs(ledger, storageRoot) {
  const queue = humanQueue(ledger);
  const lines = ["# Human work queue", "", `Updated: ${iso()}`, ""];
  if (!queue.length) lines.push("- Nothing needs human attention.");
  for (const task of queue) {
    lines.push(`## ${task.title}`, "", `- Project: ${task.projectId}`, `- Priority: ${task.priority}`, `- Due: ${task.due || "Not set"}`, `- Estimated effort: ${task.estimatedMinutes || "Unknown"} minutes`, `- Reason: ${task.reason || "Human capability or authority is required."}`, `- Prepared: ${task.expectedOutput || "No prepared material recorded."}`, `- Completion signal: ${task.verification || task.expectedOutput || "Provide the requested result."}`, `- Impact of delay: ${(ledger.tasks.filter((candidate) => candidate.dependencies?.includes(task.id)).map((candidate) => candidate.title).join(", ")) || "No dependent task recorded."}`, "");
  }
  await writeFile(join(storageRoot, "human-queue.md"), `${lines.join("\n")}\n`, "utf8");
  const tickets = ledger.tasks.filter((task) => task.ticket && !["done", "cancelled"].includes(task.status)).map((task) => ({ id: task.id, projectId: task.projectId, title: task.title, body: task.reason, due: task.due, dependencies: task.dependencies, requiresExplicitPublishApproval: true }));
  await atomicJson(join(storageRoot, "ticket-outbox.json"), { generatedAt: iso(), tickets });
  const serviceTasks = ledger.tasks.filter((task) => task.assigneeType === "service" && !["done", "cancelled"].includes(task.status)).map((task) => ({
    id: task.id,
    projectId: task.projectId,
    service: task.assignee || task.requiredCapability || "unassigned",
    title: task.title,
    expectedOutput: task.expectedOutput,
    verification: task.verification,
    requiresExplicitDispatchApproval: true,
  }));
  await atomicJson(join(storageRoot, "service-outbox.json"), { generatedAt: iso(), tasks: serviceTasks });
}

const ledger = await loadLedger();
const storageRoot = dirname(statePath);
const selected = option("project");
const applyProposalPath = option("apply-proposal");
const approvedProposal = applyProposalPath ? JSON.parse(await read(resolve(applyProposalPath))) : null;
const projects = ledger.projects.filter((project) => !selected || project.id === selected);
if (!projects.length) throw new Error(selected ? `Unknown project: ${selected}` : "No registered projects.");
const report = { observed: [], normalized: [], advanced: [], humanQueue: [], reminders: [], ticketOutbox: join(storageRoot, "ticket-outbox.json"), serviceOutbox: join(storageRoot, "service-outbox.json") };

for (const project of projects) {
  const observation = await observe(project, storageRoot);
  report.observed.push({ projectId: project.id, changed: observation.snapshot.changed, actions: observation.actions.length });
  ledger.events.push({ at: iso(), kind: "project-observed", projectId: project.id, summary: `${observation.actions.length} actions; changed=${observation.snapshot.changed}` });
  if (approvedProposal?.projectId === project.id) {
    const applied = await applyNormalization({ path: resolve(applyProposalPath), proposal: approvedProposal }, storageRoot);
    report.normalized.push({ projectId: project.id, proposal: resolve(applyProposalPath), documents: approvedProposal.documents.map((document) => document.name), applied });
  } else {
    const proposal = await writeNormalizationProposal(project, observation, storageRoot);
    if (proposal) {
      report.normalized.push({ projectId: project.id, proposal: proposal.path, documents: proposal.proposal.documents.map((document) => document.name) });
    }
  }
  syncTasks(ledger, project, observation.actions);
  if (flag("execute")) report.advanced.push(...await executeReady(ledger, project));
}

for (const task of humanQueue(ledger)) {
  task.status = "waiting-human";
  if (dueForReminder(task)) {
    task.lastReminderAt = iso();
    report.reminders.push(task.id);
    ledger.events.push({ at: iso(), kind: "human-reminded", projectId: task.projectId, taskId: task.id, summary: `Reminder due for ${task.title}` });
  }
}

report.humanQueue = humanQueue(ledger).map((task) => task.id);
ledger.updatedAt = iso();
await atomicJson(statePath, ledger);
await writeOutputs(ledger, storageRoot);
process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
