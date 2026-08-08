#!/usr/bin/env node
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { resolve, join } from "node:path";

const argv = process.argv.slice(2);
const option = name => {
  const index = argv.indexOf(`--${name}`);
  return index >= 0 ? argv[index + 1] || "" : "";
};

const project = resolve(option("project") || ".");
const requestedBase = option("base");
const baseSource = option("base-source");
const requestedProduction = option("production");
const proposedMigration = option("migration");
const remoteMigrationsFile = option("remote-migrations-file");
const allowedSources = new Set(["user", "ledger", "production", "release-evidence"]);
const errors = [];

function git(cwd, args, optional = false) {
  try {
    return execFileSync("git", ["-C", cwd, ...args], {
      encoding: "utf8",
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    }).trim();
  } catch (error) {
    if (optional) return "";
    const detail = String(error?.stderr || error?.message || "git command failed").trim();
    throw new Error(`${args.join(" ")}: ${detail}`);
  }
}

function parseWorktrees(raw) {
  const rows = [];
  let current = null;
  for (const line of raw.split(/\r?\n/)) {
    if (line.startsWith("worktree ")) {
      current = { path: resolve(line.slice(9)), head: "", branch: "" };
      rows.push(current);
    } else if (current && line.startsWith("HEAD ")) {
      current.head = line.slice(5);
    } else if (current && line.startsWith("branch ")) {
      current.branch = line.slice(7).replace(/^refs\/heads\//, "");
    }
  }
  return rows;
}

function resolveRef(ref) {
  if (!ref) return "";
  return git(project, ["rev-parse", "--verify", `${ref}^{commit}`], true);
}

function isAncestor(ancestor, descendant) {
  if (!ancestor || !descendant) return false;
  try {
    execFileSync("git", ["-C", project, "merge-base", "--is-ancestor", ancestor, descendant], {
      windowsHide: true,
      stdio: "ignore",
    });
    return true;
  } catch {
    return false;
  }
}

if (!existsSync(join(project, ".git"))) {
  errors.push(`project is not a Git worktree: ${project}`);
}
if (!requestedBase) {
  errors.push("--base is required; do not infer an implementation base");
}
if (!requestedProduction) {
  errors.push("--production is required; do not assume the base contains deployed code");
}
if (!allowedSources.has(baseSource)) {
  errors.push("--base-source must be user, ledger, production, or release-evidence");
}

const worktrees = parseWorktrees(git(project, ["worktree", "list", "--porcelain"], true));
const baseSha = resolveRef(requestedBase);
const productionSha = resolveRef(requestedProduction);
if (requestedBase && !baseSha) {
  errors.push(`base cannot be resolved: ${requestedBase}`);
}
if (requestedProduction && !productionSha) {
  errors.push(`production cannot be resolved: ${requestedProduction}`);
}
const productionIsAncestor = isAncestor(productionSha, baseSha);
if (productionSha && baseSha && !productionIsAncestor) {
  errors.push("base does not contain the verified production lineage");
}

const refs = {};
for (const ref of ["HEAD", "origin/develop", "origin/main", "develop", "main"]) {
  const sha = resolveRef(ref);
  if (sha) refs[ref] = sha;
}

const migrationByNumber = new Map();
const gitMigrationNames = new Set();
function addMigration(filename, location) {
  const match = /^(\d{4})_(.+)\.sql$/i.exec(filename);
  if (!match) return;
  gitMigrationNames.add(filename);
  const number = Number(match[1]);
  if (!migrationByNumber.has(number)) migrationByNumber.set(number, new Map());
  const names = migrationByNumber.get(number);
  if (!names.has(filename)) names.set(filename, []);
  if (!names.get(filename).includes(location)) names.get(filename).push(location);
}

for (const worktree of worktrees) {
  const directory = join(worktree.path, "v2", "db", "migrations");
  if (!existsSync(directory)) continue;
  for (const filename of readdirSync(directory)) {
    addMigration(filename, worktree.path);
  }
}

const treeRefs = new Map();
for (const [label, sha] of [
  ["base", baseSha],
  ["production", productionSha],
  ...Object.entries(refs),
]) {
  if (sha && !treeRefs.has(sha)) treeRefs.set(sha, label);
}
for (const [sha, label] of treeRefs) {
  const files = git(
    project,
    ["ls-tree", "-r", "--name-only", sha, "--", "v2/db/migrations"],
    true,
  );
  for (const file of files.split(/\r?\n/).filter(Boolean)) {
    addMigration(file.split("/").at(-1), `${label}@${sha.slice(0, 7)}`);
  }
}

let remoteMigrationNames = [];
if (proposedMigration && !remoteMigrationsFile) {
  errors.push("--remote-migrations-file is required when proposing a migration");
}
if (remoteMigrationsFile) {
  const remotePath = resolve(remoteMigrationsFile);
  if (!existsSync(remotePath)) {
    errors.push(`remote migration evidence does not exist: ${remotePath}`);
  } else {
    try {
      const parsed = JSON.parse(readFileSync(remotePath, "utf8"));
      const strings = [];
      const visit = value => {
        if (typeof value === "string") strings.push(value);
        else if (Array.isArray(value)) value.forEach(visit);
        else if (value && typeof value === "object") Object.values(value).forEach(visit);
      };
      visit(parsed);
      remoteMigrationNames = [...new Set(strings.filter(value => /^\d{4}_.+\.sql$/i.test(value)))];
      if (!remoteMigrationNames.length) {
        errors.push("remote migration evidence contains no NNNN_description.sql names");
      }
      for (const filename of remoteMigrationNames) {
        if (!gitMigrationNames.has(filename)) {
          errors.push(`remote migration is absent from registered Git worktrees: ${filename}`);
        }
        const number = Number(filename.slice(0, 4));
        if (!migrationByNumber.has(number)) migrationByNumber.set(number, new Map());
        const names = migrationByNumber.get(number);
        if (!names.has(filename)) names.set(filename, []);
        if (!names.get(filename).includes("remote-d1")) names.get(filename).push("remote-d1");
      }
    } catch (error) {
      errors.push(`remote migration evidence is invalid JSON: ${error.message}`);
    }
  }
}

const migrationCollisions = [];
for (const [number, names] of migrationByNumber) {
  if (names.size > 1) {
    migrationCollisions.push({
      number: String(number).padStart(4, "0"),
      files: [...names].map(([filename, paths]) => ({ filename, worktrees: paths })),
    });
  }
}
if (migrationCollisions.length) {
  errors.push("migration number collision exists across registered worktrees");
}

const existingNumbers = [...migrationByNumber.keys()];
const maxMigration = existingNumbers.length ? Math.max(...existingNumbers) : 0;
const nextMigration = String(maxMigration + 1).padStart(4, "0");

if (proposedMigration) {
  const match = /^(\d{4})_.+\.sql$/i.exec(proposedMigration);
  if (!match) {
    errors.push("--migration must match NNNN_description.sql");
  } else {
    const proposedNumber = Number(match[1]);
    if (migrationByNumber.has(proposedNumber)) {
      errors.push(`proposed migration reuses existing number ${match[1]}`);
    }
    if (proposedNumber !== maxMigration + 1) {
      errors.push(`proposed migration must use next verified number ${nextMigration}`);
    }
  }
}

const relations = {};
for (const pair of [
  ["origin/main", "origin/develop"],
  ["main", "develop"],
]) {
  if (!refs[pair[0]] || !refs[pair[1]]) continue;
  relations[`${pair[0]}...${pair[1]}`] = git(
    project,
    ["rev-list", "--left-right", "--count", `${pair[0]}...${pair[1]}`],
    true,
  );
}

const report = {
  ok: errors.length === 0,
  project,
  requestedBase,
  baseSource,
  baseSha,
  requestedProduction,
  productionSha,
  productionIsAncestor,
  refs,
  relations,
  worktrees,
  migrations: {
    maxVerifiedLocal: String(maxMigration).padStart(4, "0"),
    nextVerifiedLocal: nextMigration,
    collisions: migrationCollisions,
    proposed: proposedMigration || null,
    remoteEvidenceFile: remoteMigrationsFile ? resolve(remoteMigrationsFile) : null,
    remoteNames: remoteMigrationNames,
    remoteVerified: Boolean(remoteMigrationsFile && remoteMigrationNames.length),
  },
  errors,
};

process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
process.exitCode = errors.length ? 2 : 0;
