#!/usr/bin/env node
import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { watch } from "node:fs";
import { dirname, extname, resolve } from "node:path";
import { homedir } from "node:os";
import { fileURLToPath } from "node:url";
import { normalizeLedger } from "./roadmap-data.mjs";

const args = process.argv.slice(2);
function option(name, fallback = "") {
  const index = args.indexOf(`--${name}`);
  return index >= 0 ? args[index + 1] ?? fallback : fallback;
}

const skillRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const ledgerPath = resolve(option("ledger",
  resolve(homedir(), ".codex", "ai-project-manager", "ledger.json")));
const htmlPath = resolve(skillRoot, "assets", "live-roadmap.html");
const host = option("host", "127.0.0.1");
const port = Number(option("port", "4317"));
const projectId = option("project");
const clients = new Set();
let revision = 0;
let lastMtime = 0;
let lastLedgerUpdatedAt = "";
let debounceTimer;

try {
  lastMtime = (await stat(ledgerPath)).mtimeMs;
  lastLedgerUpdatedAt = JSON.parse(await readFile(ledgerPath, "utf8")).updatedAt || "";
} catch {
  // The state endpoint will report a missing or invalid ledger without stopping the server.
}

async function state() {
  const ledger = JSON.parse(await readFile(ledgerPath, "utf8"));
  return { ...normalizeLedger(ledger, { projectId }), revision };
}

function notify() {
  revision += 1;
  for (const response of clients) {
    response.write(`event: roadmap-update\ndata: ${JSON.stringify({ revision })}\n\n`);
  }
}

function scheduleNotify() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(notify, 120);
}

try {
  watch(dirname(ledgerPath), () => scheduleNotify());
} catch (error) {
  console.error(`Directory watch unavailable; polling remains active: ${error.message}`);
}

setInterval(async () => {
  try {
    const current = (await stat(ledgerPath)).mtimeMs;
    const currentUpdatedAt = JSON.parse(await readFile(ledgerPath, "utf8")).updatedAt || "";
    if ((lastMtime && current !== lastMtime) ||
        (lastLedgerUpdatedAt && currentUpdatedAt !== lastLedgerUpdatedAt)) scheduleNotify();
    lastMtime = current;
    lastLedgerUpdatedAt = currentUpdatedAt;
  } catch {}
}, 1000).unref();

const server = createServer(async (request, response) => {
  try {
    const url = new URL(request.url || "/", `http://${request.headers.host || host}`);
    if (url.pathname === "/events") {
      response.writeHead(200, {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Access-Control-Allow-Origin": "*",
      });
      response.write(`event: connected\ndata: ${JSON.stringify({ revision })}\n\n`);
      clients.add(response);
      request.on("close", () => clients.delete(response));
      return;
    }
    if (url.pathname === "/api/state") {
      const body = JSON.stringify(await state());
      response.writeHead(200, {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
      });
      response.end(body);
      return;
    }
    if (url.pathname === "/" || url.pathname === "/index.html") {
      const body = await readFile(htmlPath);
      response.writeHead(200, {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store",
      });
      response.end(body);
      return;
    }
    response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
    response.end("Not found");
  } catch (error) {
    response.writeHead(500, { "Content-Type": "application/json; charset=utf-8" });
    response.end(JSON.stringify({ error: error.message }));
  }
});

server.listen(port, host, () => {
  console.log(`Live roadmap: http://${host}:${port}`);
  console.log(`Ledger: ${ledgerPath}`);
  if (projectId) console.log(`Project: ${projectId}`);
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => server.close(() => process.exit(0)));
}
