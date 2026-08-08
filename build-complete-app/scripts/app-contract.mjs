#!/usr/bin/env node
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const args = process.argv.slice(2);
const command = args[0];
const value = name => {
  const index = args.indexOf(`--${name}`);
  return index >= 0 ? args[index + 1] : undefined;
};

const required = ["name", "outcome", "personas", "primaryJourney", "acceptanceCriteria", "nonGoals"];

function validate(contract) {
  const errors = [];
  for (const key of required) {
    if (!(key in contract)) errors.push(`Missing ${key}`);
  }
  for (const key of ["personas", "primaryJourney", "acceptanceCriteria", "nonGoals"]) {
    if (key in contract && (!Array.isArray(contract[key]) || contract[key].length === 0)) {
      errors.push(`${key} must be a non-empty array`);
    }
  }
  if (typeof contract.name !== "string" || !contract.name.trim()) errors.push("name must be a non-empty string");
  if (typeof contract.outcome !== "string" || !contract.outcome.trim()) errors.push("outcome must be a non-empty string");
  if (contract.stack && contract.stack.runtime !== "web") errors.push("This Skill supports web runtime only");
  return errors;
}

if (command !== "validate" || !value("file")) {
  console.error("Usage: app-contract.mjs validate --file <app-contract.json>");
  process.exit(2);
}

try {
  const file = resolve(value("file"));
  const contract = JSON.parse(await readFile(file, "utf8"));
  const errors = validate(contract);
  console.log(JSON.stringify({ ok: errors.length === 0, file, errors }, null, 2));
  process.exit(errors.length ? 1 : 0);
} catch (error) {
  console.error(JSON.stringify({ ok: false, error: error.message }, null, 2));
  process.exit(1);
}

