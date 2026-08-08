#!/usr/bin/env node
import { readFile, access } from "node:fs/promises";
import { resolve, join } from "node:path";
import { spawnSync } from "node:child_process";

const args = process.argv.slice(2);
const value = name => {
  const index = args.indexOf(`--${name}`);
  return index >= 0 ? args[index + 1] : undefined;
};
const project = resolve(value("project") || ".");
const shouldRun = args.includes("--run");
const checks = [];
const add = (name, ok, detail) => checks.push({ name, ok, detail });
const exists = async path => access(path).then(() => true).catch(() => false);
function npmRun(script, extra = []) {
  const env = { ...process.env, CI: "1" };
  if (process.platform === "win32") {
    return spawnSync(process.env.ComSpec || "cmd.exe", ["/d", "/s", "/c", "npm", "run", script, ...extra], {
      cwd: project, encoding: "utf8", windowsHide: true, env
    });
  }
  return spawnSync("npm", ["run", script, ...extra], { cwd: project, encoding: "utf8", env });
}
const resultDetail = result => (result.stderr || result.stdout || result.error?.message || "").slice(-2000);

try {
  const packagePath = join(project, "package.json");
  const packageJson = JSON.parse(await readFile(packagePath, "utf8"));
  add("package", true, packagePath);
  add("start-script", Boolean(packageJson.scripts?.dev || packageJson.scripts?.start), "dev or start script");
  add("build-script", Boolean(packageJson.scripts?.build), "build script");
  add("source", await exists(join(project, "src")), "src directory");
  add("app-contract", await exists(join(project, "app-contract.json")), "app-contract.json");

  if (shouldRun && packageJson.scripts?.build) {
    const build = npmRun("build");
    add("build", build.status === 0, resultDetail(build));
  }
  if (shouldRun && packageJson.scripts?.test) {
    const test = npmRun("test");
    add("test", test.status === 0, resultDetail(test));
  } else {
    add("test", false, "No test script; browser inspection does not replace automated core-behavior tests");
  }
} catch (error) {
  add("package", false, error.message);
}

const failed = checks.filter(check => !check.ok);
console.log(JSON.stringify({
  ok: failed.length === 0,
  project,
  checks,
  note: "Real-browser desktop/mobile image verification is required separately and must be recorded in the project ledger."
}, null, 2));
process.exit(failed.length ? 1 : 0);
