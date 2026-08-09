import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";

const root = resolve(import.meta.dirname, "..");
const validator = resolve(import.meta.dirname, "phase-gate.mjs");
const template = await readFile(resolve(root, "assets", "PHASES.template.md"), "utf8");
const directory = await mkdtemp(join(tmpdir(), "phase-gate-"));

try {
  const validPath = join(directory, "valid.md");
  await writeFile(validPath, template, "utf8");
  const valid = spawnSync(process.execPath, [validator, "validate", validPath], {
    encoding: "utf8",
    windowsHide: true,
  });
  assert.equal(valid.status, 0);
  assert.equal(JSON.parse(valid.stdout).ok, true);

  const invalidPath = join(directory, "invalid.md");
  await writeFile(invalidPath, template.replace("- Result: pending", "- Result: go"), "utf8");
  const invalid = spawnSync(process.execPath, [validator, "validate", invalidPath], {
    encoding: "utf8",
    windowsHide: true,
  });
  assert.equal(invalid.status, 1);
  const invalidResult = JSON.parse(invalid.stdout);
  assert.match(invalidResult.errors.join("\n"), /every exit criterion/);
  assert.match(invalidResult.errors.join("\n"), /recorded evidence/);

  console.log(JSON.stringify({ ok: true, validTemplate: true, unsafeGoRejected: true }));
} finally {
  await rm(directory, { recursive: true, force: true });
}
