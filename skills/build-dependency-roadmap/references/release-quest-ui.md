# Release Quest reference UI

Use `assets/live-roadmap.html` as the executable default. This contract preserves the successful
visual language first validated in `14_release_dependency_roadmap_2026-07-28.html` without copying
project-specific names, evidence, dates, or topology.

## Composition

- Fit the entire roadmap in `100vw × 100dvh`.
- Use a transparent header over the graph, not a separate dashboard card.
- Place a small uppercase kicker, project title, and latest evidence line at upper left.
- Place the large completed/total counter at upper right.
- Keep the legend compact and visually subordinate.
- Give the DAG most of the viewport. Use generous gaps and curved directed edges.

## Visual tokens

- Canvas: `#07111f` with a faint 48px grid and restrained central blue radial glow.
- Node surface: `#0a1828`; hover surface: `#0d2133`.
- Text: `#f5f8fc`; secondary text: `#91a5b8`; IDs: `#7890a7`.
- CLEAR: `#3bd887`; ACTIVE: `#62b6ff`; WAITING: `#f4bd4f`; BLOCKED: `#fb7185`;
  PARALLEL: `#9f7aea`; LOCKED: `#536b83`.
- Prefer `BIZ UDPGothic`, then Japanese system UI fonts.
- Use thin luminous strokes, restrained glow only for the current bottleneck, and rounded
  rectangular nodes. Avoid decorative cards around the whole graph.

## Node anatomy

Render in this order:

1. short stable ID for leaf/formal-task nodes; omit generated container IDs;
2. outcome-oriented title;
3. one-line evidence or outcome; for parent nodes use `{count}件 · 配下を表示`;
4. likely ETA when useful;
5. explicit status pill/text.

Keep worker identity quieter than status. Use icons or geometry rather than emoji.

## Edges

- CLEAR dependencies: solid green.
- Critical path: animated gold dash.
- Parallel lane: purple dash.
- Other dependencies: muted blue-gray.
- Preserve obvious split and merge points; never let an arrow end ambiguously between nodes.

## Interaction

- Make every node keyboard reachable. A single click or Enter/Space opens compact details.
- A double click on a parent enters exactly its immediate child DAG.
- Show details in a collapsible right pane and keep it about the selected node itself: detailed
  explanation, reason, completion
  criteria, current state, blocker/wait impact, owner, and forecast. Do not show descendant
  counts, descendant statuses, child navigation, or another graph.
- Make the node ID itself clickable to copy and confirm the result with a brief toast.
- Collapsing and reopening the pane must preserve the current hierarchy. On narrow screens, overlay
  it from the right so the graph keeps its usable width.
- On desktop, make the detail surface a flush-right sidebar in the main layout, not a floating
  card. Keep it open with the same selected detail while drilling down or navigating back.
- After drill-down, show a persistent breadcrumb and an obvious one-level Back action.
- Preserve hierarchy breadcrumb across live refresh.
- Retain the ledger-backed SSE update path and 15-second fallback refresh.

## Adaptation

Reuse the visual system, not the reference project's content or fixed coordinates. Compute layout
from the current DAG. Use at most three interactive hierarchy layers. For deeper projects,
aggregate lower-level work into the third layer or open a separately filtered view instead of
shrinking all descendants or adding more nested drill-down.
