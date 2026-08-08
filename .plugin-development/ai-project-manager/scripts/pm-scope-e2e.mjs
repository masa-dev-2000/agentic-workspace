#!/usr/bin/env node
import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { join } from "node:path";
import { tmpdir } from "node:os";

const root = await mkdtemp(join(tmpdir(), "ai-pm-scope-e2e-"));
const project = join(root, "project");
const state = join(root, "state", "ledger.json");
const fakeCodex = join(root, "fake-codex.mjs");
const autopilot = new URL("./pm-autopilot.mjs", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
const ledgerCli = new URL("./pm-ledger.mjs", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
const run = (script, args) => spawnSync(process.execPath, [script, ...args], { encoding: "utf8", windowsHide: true });

try {
  await mkdir(project, { recursive: true });
  await writeFile(join(project, "README.md"), "# Scoped\n\n## Overview\n\nFixture.\n", "utf8");
  await writeFile(join(project, "ROADMAP.md"), "# Roadmap\n\n## Goal\n\nStay scoped.\n\n## Milestones\n", "utf8");
  await writeFile(join(project, "TODO.md"), "# Tasks\n\n## Action Plan\n", "utf8");
  assert.equal(run(ledgerCli, ["init", "--state", state]).status, 0);
  assert.equal(run(ledgerCli, ["add-project", "--state", state, "--id", "scoped", "--name", "Scoped", "--path", project]).status, 0);
  await writeFile(fakeCodex, `import {readFile,writeFile} from 'node:fs/promises';
const prompt=process.argv.at(-1);
const state=prompt.match(/ledger at '([^']+)'/)[1];
const ledger=JSON.parse(await readFile(state,'utf8'));
ledger.projects.push({id:'intruder',name:'Intruder',path:'C:\\\\intruder',objective:'',updatedAt:new Date().toISOString()});
ledger.tasks.push({id:'intruder-task',projectId:'intruder',title:'Out of scope',assigneeType:'agent',status:'ready',priority:'normal',dependencies:[],evidence:[]});
await writeFile(state,JSON.stringify(ledger,null,2));
const outputIndex=process.argv.indexOf('-o');
if(outputIndex>=0) await writeFile(process.argv[outputIndex+1],'fake agent completed');
`, "utf8");

  const result = run(autopilot, ["--state", state, "--project", "scoped", "--codex-js", fakeCodex]);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /rolled back|registered project set/i);
  const restored = JSON.parse(await readFile(state, "utf8"));
  assert.deepEqual(restored.projects.map((item) => item.id), ["scoped"]);
  assert.equal(restored.tasks.some((item) => item.projectId === "intruder"), false);
  process.stdout.write(`${JSON.stringify({ ok: true, unauthorizedProjectRejected: true, ledgerRestored: true }, null, 2)}\n`);
} finally {
  await rm(root, { recursive: true, force: true });
}
