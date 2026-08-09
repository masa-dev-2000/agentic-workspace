import { readFile } from "node:fs/promises";
import assert from "node:assert/strict";

const html = await readFile(
  new URL("../assets/live-roadmap.html", import.meta.url),
  "utf8",
);

assert.match(html, /<aside class="detail-pane" id="detailPane"/, "collapsible detail pane must exist");
assert.doesNotMatch(html, /<dialog\b/, "modal dialog must not exist");
assert.match(html, /group\.addEventListener\("click", queueDetail\)/, "single click must queue details");
assert.match(html, /group\.addEventListener\("dblclick", drill\)/, "double click must drill");
assert.match(html, /clearTimeout\(state\.clickTimer\)/, "double click must cancel queued details");
assert.match(html, /if \(!canDrill\) \{\s*openDetail\(node\.id\);\s*return;/, "leaf double click must fall back to details");
assert.doesNotMatch(html, /id="drillButton"/, "detail pane must not contain child navigation");
assert.match(html, /openDetail\(state\.selectedId, true\)/, "live refresh must preserve pane state");
assert.match(html, /id="closePane"/, "pane must have a collapse control");
assert.match(html, /id="reopenPane"/, "collapsed pane must have a reopen control");
assert.match(html, /id="detailId"[^>]*aria-label="ノードIDをコピー"/, "node ID must be directly copyable");
assert.match(html, /navigator\.clipboard\.writeText/, "node ID copy must use the clipboard");
assert.match(html, /setPaneOpen\(false\)/, "pane must support collapsing");
assert.match(html, /setPaneOpen\(true/, "pane must support reopening");
assert.doesNotMatch(
  html,
  /function drillInto[\s\S]*?setPaneOpen\(false\)[\s\S]*?function render/,
  "drilling must preserve the open sidebar",
);
assert.match(
  html,
  /\$\("backLevel"\)\.addEventListener\("click", \(\) => \{\s*state\.viewStack\.pop\(\);\s*renderCurrentView\(\);\s*\}\);/,
  "back navigation must preserve the open sidebar",
);
assert.match(html, /aria-hidden="true"/, "pane must expose collapsed state");
assert.match(html, /状態/, "pane must show decision-relevant status");
assert.match(html, /阻害要因/, "pane must show blocker impact");
assert.match(html, /詳細説明/, "pane must explain the selected node");
assert.match(html, /この工程が必要な理由/, "pane must explain why the selected node exists");
assert.match(html, /完了条件/, "pane must explain how the selected node completes");
assert.doesNotMatch(html, /childStatusSummary|配下の内訳/, "pane must not summarize descendant nodes");
assert.doesNotMatch(html, /pointerdown|pointermove|showModal/, "pane must not retain modal dragging behavior");

console.log(JSON.stringify({ ok: true, contract: "click-detail-dblclick-drill" }));
