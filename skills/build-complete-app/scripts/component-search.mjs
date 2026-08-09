#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { homedir } from "node:os";
import { join } from "node:path";
import { existsSync } from "node:fs";

const cli = process.env.REUSE_LIBRARY_CLI ||
  join(homedir(), "dev", "99_toolbox", "reuse-library", "scripts", "reuse-library.mjs");
if (!existsSync(cli)) {
  console.error(`Reuse library CLI not found: ${cli}`);
  process.exit(2);
}
const query = process.argv.slice(2).join(" ").trim();
const result = spawnSync(process.execPath, [cli, "search", "--query", query], {
  stdio: "inherit",
  windowsHide: true
});
process.exit(result.status ?? 1);

