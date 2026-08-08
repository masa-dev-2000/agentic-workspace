---
name: build-dependency-roadmap
description: Create or improve a one-screen, hierarchical DAG dependency roadmap in HTML/SVG, including scale-aware project decomposition, collapsible detail panes, nested sub-roadmaps, ETA ranges, and live AI Project Manager ledger updates. Use when users ask for dependency graphs, dependency networks, DAG roadmaps, 工程進捗マップ, large-project roadmaps, critical paths, bottlenecks, skill-tree roadmaps, node details, estimated completion times, or real-time project status dashboards.
---

# Build Dependency Roadmap

Create a hierarchical directed acyclic graph (DAG) from verified project evidence. Call the
result a **依存関係ロードマップ** in user-facing Japanese. Prefer the bundled live viewer when
the AI Project Manager ledger is available.

## Workflow

1. Read [dependency-investigation.md](references/dependency-investigation.md), then investigate
   code, document, operational, service, approval, and human dependencies before drawing.
2. Convert work into nodes with stable IDs, directed dependencies, hierarchy, and one status:
   done, active, waiting, blocked, parallel, or locked. Use blocked only when an unfinished
   mandatory dependency actually prevents a required downstream outcome. Render ordinary human
   or service waits as waiting, with `確認待ち` or `外部待ち`.
3. Keep the interactive roadmap to at most three semantic layers. Prefer overview →
   phase/workstream → milestone/task. Collapse finer work into the third-layer node, its facts, or
   a separately filtered view instead of adding a fourth drill-down layer.
4. Select the three most useful levels from portfolio, project, phase/workstream, milestone,
   deliverable, and task/subtask. Skip empty levels and never invent meaningless containers.
5. Keep each graph view to roughly 6–10 sibling nodes. If a level exceeds 10, partition it by
   phase, workstream, milestone, deliverable, or explicit parent before using a neutral batch.
   Never use status as the primary hierarchy when semantic grouping exists.
6. Render only the current level in the main graph. A single click opens a compact, collapsible
   right detail pane about that node itself. A double click on a node with children enters exactly one
   child level. Preserve a persistent breadcrumb and `一つ上へ戻る` action. Do not put child
   navigation or descendant summaries in the detail pane.
7. Identify the current bottleneck and critical path at both the current level and the leaf-task
   level.
8. Lay out prerequisites left-to-right. Stack parallel branches vertically and merge them before
   shared quality or release gates.
9. Render the graph as responsive inline SVG with a fixed viewBox and
   preserveAspectRatio="xMidYMid meet".
10. Keep the detail pane focused on the selected node: what it is, why it exists, its completion
    criteria, current state, blocker/wait impact, owner, and forecast. Do not put child counts,
    descendant status summaries, child navigation, or another graph in it. Let users collapse
    and reopen the pane without changing the current hierarchy. Make the displayed node ID
    directly clickable to copy it, without adding a separate copy-control row. Keep the sidebar
    open and preserve its selected node while drilling down or navigating back.
11. Show optimistic, likely, and pessimistic completion times with confidence and estimate source.
12. Show active workers as quiet corner badges; keep role, model, workspace, start, heartbeat, and
   execution state in the node detail pane.
13. Keep the entire map and detail pane within 100vw by 100dvh; avoid body scrolling. Render the
    desktop detail surface as an integrated, flush-right sidebar without floating-card margins,
    rounding, backdrop, or modal shadow. On narrow screens, overlay it from the right instead of
    crushing the graph.
14. Verify HTTP 200, state JSON, live updates, keyboard operation, deep navigation, cycle
   handling, and common viewport sizes.

## Scale policy

- Small: up to 10 leaf tasks — show the task DAG directly.
- Medium: 11–40 leaf tasks — add one semantic grouping level.
- Large: 41–150 leaf tasks — use overview → phase/workstream → milestone/task.
- Very large: over 150 leaf tasks or more than 4 projects — use portfolio/project →
  phase/workstream → milestone/task, then expose finer work through filters or a separate view.
- Cap visible siblings and interactive depth. Never exceed three layers to solve density; split
  the scope, filter it, or aggregate lower-level work instead.
- Aggregate status, progress, ETA, blockers, criticality, and active workers upward from leaf
  nodes; retain leaf evidence as the source of truth.

## Live ledger viewer

Read [ledger-roadmap-contract.md](references/ledger-roadmap-contract.md) and
[release-quest-ui.md](references/release-quest-ui.md), then run:

```powershell
node scripts/roadmap-server.mjs
```

Optional flags:

```text
--ledger PATH   Alternative ledger.json
--project ID    Limit to one project
--host HOST     Default 127.0.0.1
--port PORT     Default 4317
```

Open `http://127.0.0.1:4317`. The server watches the ledger directory, emits SSE updates, and
polls mtime as fallback. Do not copy ledger data into the HTML or treat the derived view as a
second source of truth.

## Visual rules

- Use `assets/live-roadmap.html` and the Release Quest reference UI as the default visual system.
  Do not fall back to a generic dashboard-card theme unless the user requests another style.
- Show only the map unless the user explicitly requests supporting panels.
- Label the visualization `依存関係ロードマップ`; use `階層DAG` in technical documentation.
- Use equal node sizes where possible. Never squeeze text by scaling a node.
- Fit text by measured rendered width, not character count. Apply a one-line ellipsis inside the
  node and preserve the complete value in its accessible name and detail pane.
- Put short names, one-line outcomes, and explicit status text inside every node. Show technical
  IDs on leaf/formal-task nodes; omit generated phase/workstream container IDs that do not help
  the user decide or navigate.
- Do not rely on color alone. Pair color with CLEAR, ACTIVE, 確認待ち, 外部待ち, BLOCKED,
  PARALLEL, or LOCKED. Reserve BLOCKED for a real mandatory-path stop.
- Use solid green edges for completed dependencies.
- Use animated gold dashed edges for the critical path.
- Use purple dashed edges for independently parallel work.
- Highlight exactly one current bottleneck unless evidence shows several.
- Include a compact completion counter and legend inside the SVG.
- Keep worker badges smaller and lower contrast than status and bottleneck signals.
- Show at most three worker identities on a node, then use a `+N` count.
- Distinguish assigned, working, waiting, stale, and finished; never infer working from assignee.
- Do not expose work locations or model identity in the primary roadmap.
- Use SVG icons or geometry, not emoji.
- Respect prefers-reduced-motion.
- Make every node keyboard reachable and keep focus visible.
- Preserve the selected node and detail-pane state across live updates.
- Show a compact breadcrumb for depth two and three.
- Let Enter/Space open details. Double click drills into a parent. Back returns one level without
  losing context.

## Information-density rules

- Target 6-10 visible nodes per screen.
- Keep node titles under 12 Japanese characters where practical.
- Keep descriptions to one line.
- Never allow node ID, title, outcome, ETA, status, or worker badge to cross the node boundary.
- Remove headers, detailed lists, footers, and prose outside the graph.
- Prefer whitespace and consistent alignment over decorative effects.
- On narrow screens, scale the full SVG instead of introducing horizontal scrolling.
- Allow only a clearly bounded facts region inside the detail pane to scroll.
- Do not render every descendant in one detail pane. Render immediate children through layer three,
  then summarize deeper descendants as facts or open them in a separately filtered view.

## Update rules

- Derive status from repository evidence; do not mark work complete from plans alone.
- Do not map every `waiting-human` or `waiting-service` task to blocked. Determine whether it has
  an unfinished required dependent and whether that dependency is on the current critical or
  release path. Show non-blocking waits as waiting and state their impact explicitly.
- Update the completion counter whenever a node changes.
- Recalculate downstream locks and the critical path after dependency changes.
- Recalculate ETA after every ledger update; label low-confidence forecasts instead of hiding them.
- Keep blocker text actionable by naming the exact release condition.
- Preserve the existing visual language when editing an established map.
- Rebuild only affected ancestor aggregates after a leaf update; preserve the user's current
  hierarchy depth and breadcrumb.
