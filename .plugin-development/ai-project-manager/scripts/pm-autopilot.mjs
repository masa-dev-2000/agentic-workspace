#!/usr/bin/env node
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { homedir } from "node:os";

const args = process.argv.slice(2);
const option = (name, fallback = "") => {
  const index = args.indexOf(`--${name}`);
  return index >= 0 ? args[index + 1] ?? fallback : fallback;
};
const flag = (name) => args.includes(`--${name}`);
const statePath = resolve(option("state", join(homedir(), ".codex", "ai-project-manager", "ledger.json")));
const storageRoot = dirname(statePath);
const runner = new URL("./pm-run.mjs", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
const selected = option("project");
const codexEntry = resolve(option("codex-js", join(process.env.APPDATA || "", "npm", "node_modules", "@openai", "codex", "bin", "codex.js")));

function run(executable, commandArgs, options = {}) {
  const result = spawnSync(executable, commandArgs, { encoding: "utf8", windowsHide: true, shell: false, timeout: options.timeout || 30 * 60 * 1000 });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${executable} failed (${result.status}): ${result.stderr || result.stdout}`);
  return result;
}

async function restoreLedger(raw) {
  const temporary = `${statePath}.${process.pid}.rollback`;
  await writeFile(temporary, raw, "utf8");
  await rename(temporary, statePath);
}

function validateScope(before, after, projectId) {
  if (JSON.stringify(before.projects) !== JSON.stringify(after.projects)) {
    throw new Error("Agent changed the registered project set.");
  }
  const projectIds = new Set(after.projects.map((project) => project.id));
  for (const task of after.tasks) {
    if (!projectIds.has(task.projectId)) throw new Error(`Agent created a task for an unknown project: ${task.id} -> ${task.projectId}`);
  }
  const beforeById = new Map(before.tasks.map((task) => [task.id, JSON.stringify(task)]));
  for (const task of after.tasks) {
    if (task.projectId !== projectId && beforeById.get(task.id) !== JSON.stringify(task)) {
      throw new Error(`Agent changed out-of-scope task: ${task.id}`);
    }
  }
  for (const task of before.tasks) {
    if (task.projectId !== projectId && !after.tasks.some((candidate) => candidate.id === task.id)) {
      throw new Error(`Agent removed out-of-scope task: ${task.id}`);
    }
  }
}

const deterministicArgs = [runner, "--state", statePath, "--execute"];
if (selected) deterministicArgs.push("--project", selected);
const deterministic = run(process.execPath, deterministicArgs);
const ledger = JSON.parse(await readFile(statePath, "utf8"));
const projects = ledger.projects.filter((project) => !selected || project.id === selected);
if (!projects.length) throw new Error(selected ? `Unknown project: ${selected}` : "No registered projects.");
await mkdir(join(storageRoot, "reports"), { recursive: true });

const agentRuns = [];
if (!flag("no-agent")) {
  await readFile(codexEntry, "utf8").catch(() => { throw new Error(`Codex CLI entrypoint not found: ${codexEntry}`); });
  for (const project of projects) {
    const beforeRaw = await readFile(statePath, "utf8");
    const beforeLedger = JSON.parse(beforeRaw);
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    const output = join(storageRoot, "reports", `${project.id}-${stamp}.md`);
    const prompt = [
      "Use $project-orchestrator as the single AI project manager.",
      `Manage only project '${project.id}' at '${project.path}'.`,
      `Use the persistent ledger at '${statePath}' as the task source of truth.`,
      "Observe current evidence, compare it with the ledger, infer the next useful outcome, and update milestones or tasks when evidence supports the change.",
      "When a consequential choice is unresolved, create one waiting-human task with a recommendation instead of guessing.",
      "Advance only safe, reversible, workspace-scoped work with objective verification.",
      "Do not publish, message people, spend money, change production, delete material data, or perform irreversible actions.",
      "Use ticket and service outboxes for external work. Verify outcomes before marking tasks done.",
      "Return only verified progress and the prioritized human queue.",
    ].join(" ");
    const codexArgs = [
      "exec",
      "-C", project.path,
      "--add-dir", storageRoot,
      "--sandbox", "workspace-write",
      "--ephemeral",
      "-c", 'approval_policy="never"',
      "-o", output,
      prompt,
    ];
    let result;
    try {
      result = run(process.execPath, [codexEntry, ...codexArgs]);
      const afterLedger = JSON.parse(await readFile(statePath, "utf8"));
      validateScope(beforeLedger, afterLedger, project.id);
    } catch (error) {
      await restoreLedger(beforeRaw);
      throw new Error(`Agent run rolled back for ${project.id}: ${error.message}`);
    }
    agentRuns.push({ projectId: project.id, output, stdout: result.stdout.trim() });
  }
}

process.stdout.write(`${JSON.stringify({ deterministic: JSON.parse(deterministic.stdout), agentRuns }, null, 2)}\n`);
