#!/usr/bin/env node
import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { join } from "node:path";
import { tmpdir } from "node:os";

const root = await mkdtemp(join(tmpdir(), "build-complete-app-"));
const contractCli = new URL("./app-contract.mjs", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
const mvpCli = new URL("./mvp-check.mjs", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
const run = (script, args, expected = 0) => {
  const result = spawnSync(process.execPath, [script, ...args], { encoding: "utf8", windowsHide: true });
  assert.equal(result.status, expected, result.stderr || result.stdout);
  return JSON.parse(result.stdout);
};

try {
  await mkdir(join(root, "src"), { recursive: true });
  const contract = {
    schemaVersion: 1,
    name: "Fixture",
    outcome: "A user completes one useful action.",
    personas: [{ name: "User", need: "Complete action" }],
    primaryJourney: ["Open", "Act", "See result"],
    acceptanceCriteria: ["Result is visible"],
    nonGoals: ["Production deployment"],
    stack: { runtime: "web" }
  };
  await writeFile(join(root, "app-contract.json"), JSON.stringify(contract), "utf8");
  await writeFile(join(root, "package.json"), JSON.stringify({
    name: "fixture",
    scripts: {
      dev: "node -e \"process.exit(0)\"",
      build: "node -e \"process.exit(0)\"",
      test: "node -e \"process.exit(0)\""
    }
  }), "utf8");
  assert.equal(run(contractCli, ["validate", "--file", join(root, "app-contract.json")]).ok, true);
  assert.equal(run(mvpCli, ["--project", root, "--run"]).ok, true);
  contract.primaryJourney = [];
  await writeFile(join(root, "bad-contract.json"), JSON.stringify(contract), "utf8");
  assert.equal(run(contractCli, ["validate", "--file", join(root, "bad-contract.json")], 1).ok, false);
  process.stdout.write(JSON.stringify({ ok: true, contractGate: true, buildAndTestGate: true }, null, 2));
} finally {
  await rm(root, { recursive: true, force: true });
}
