#!/usr/bin/env node
import { access, copyFile, readFile } from "node:fs/promises";
import { constants } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const command = process.argv[2];
const target = resolve(process.argv[3] || ".");
const template = resolve(dirname(fileURLToPath(import.meta.url)), "..", "assets", "PHASES.template.md");
const allowedDecisions = new Set(["pending", "go", "hold", "rework", "kill"]);

function parse(markdown) {
  const current = /## Current Phase[\s\S]*?-\s*Phase:\s*([^\r\n]+)/i.exec(markdown)?.[1].trim().toLowerCase() || "";
  const chunks = markdown.split(/^## Phase:\s*/gim).slice(1);
  const phases = chunks.map(chunk => {
    const [title, ...rest] = chunk.split(/\r?\n/);
    const body = rest.join("\n");
    const section = name => new RegExp(`### ${name}\\s*\\n([\\s\\S]*?)(?=\\n### |$)`, "i").exec(body)?.[1].trim() || "";
    const requirements = section("Requirements").match(/^\s*-\s*\[[ xX]\]\s+([A-Z][A-Z0-9]*-\d{3,})\b.*$/gm) || [];
    const exitBody = section("Exit Criteria");
    const exits = exitBody.match(/^\s*-\s*\[[ xX]\]\s+.+$/gm) || [];
    const completedExits = exits.filter(line => /\[[xX]\]/.test(line)).length;
    const decision = /-\s*Result:\s*([^\r\n]+)/i.exec(section("Gate Decision"))?.[1].trim().toLowerCase() || "";
    const evidence = section("Evidence");
    return {
      id: title.trim().toLowerCase(),
      requirements: requirements.map(line => /\b([A-Z][A-Z0-9]*-\d{3,})\b/.exec(line)[1]),
      exits: { completed: completedExits, total: exits.length },
      evidenceRecorded: Boolean(evidence && !/^(?:-\s*)?pending\.?$/i.test(evidence)),
      decision,
      missingSections: ["Purpose", "Scope", "Requirements", "Exit Criteria", "Evidence", "Risks", "Gate Decision"]
        .filter(name => !section(name)),
    };
  });
  return { current, phases };
}

function validate(model) {
  const errors = [];
  if (!model.current) errors.push("Current phase is not defined.");
  if (!model.phases.some(phase => phase.id === model.current)) {
    errors.push(`Current phase '${model.current}' has no matching phase section.`);
  }
  const requirementIds = new Set();
  for (const phase of model.phases) {
    if (phase.missingSections.length) errors.push(`${phase.id}: missing ${phase.missingSections.join(", ")}.`);
    if (!allowedDecisions.has(phase.decision)) errors.push(`${phase.id}: invalid gate decision '${phase.decision || "(empty)"}'.`);
    for (const id of phase.requirements) {
      if (requirementIds.has(id)) errors.push(`Duplicate requirement ID: ${id}.`);
      requirementIds.add(id);
    }
    if (phase.decision === "go" && phase.exits.completed !== phase.exits.total) {
      errors.push(`${phase.id}: GO requires every exit criterion to be complete.`);
    }
    if (phase.decision === "go" && !phase.evidenceRecorded) {
      errors.push(`${phase.id}: GO requires recorded evidence.`);
    }
  }
  return errors;
}

if (command === "init") {
  const destination = resolve(target, "PHASES.md");
  try {
    await access(destination, constants.F_OK);
    throw new Error(`Refusing to overwrite existing ${destination}`);
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  await copyFile(template, destination, constants.COPYFILE_EXCL);
  console.log(JSON.stringify({ ok: true, created: destination }));
} else if (command === "validate") {
  const markdown = await readFile(target, "utf8");
  const model = parse(markdown);
  const errors = validate(model);
  console.log(JSON.stringify({ ok: errors.length === 0, errors, ...model }, null, 2));
  if (errors.length) process.exitCode = 1;
} else {
  console.error("Usage: node phase-gate.mjs init PROJECT_PATH | validate PHASES.md");
  process.exitCode = 2;
}
