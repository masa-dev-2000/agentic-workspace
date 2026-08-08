import { basename, isAbsolute, relative, resolve, sep } from "node:path";

const STATUS = {
  done: "done",
  "in-progress": "active",
  verification: "active",
  blocked: "blocked",
  "waiting-human": "waiting",
  "waiting-service": "waiting",
  ready: "parallel",
  backlog: "locked",
  cancelled: "locked",
};

function minutes(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : fallback;
}

function taskEstimate(task) {
  if (task.status === "done" || task.status === "cancelled") {
    return { optimistic: 0, likely: 0, pessimistic: 0, source: "completed" };
  }
  const likely = minutes(task.estimate?.likelyMinutes, minutes(task.estimatedMinutes, 60));
  return {
    optimistic: minutes(task.estimate?.optimisticMinutes, Math.max(15, likely * 0.7)),
    likely,
    pessimistic: minutes(task.estimate?.pessimisticMinutes, likely * 1.6),
    source: task.estimate?.likelyMinutes || task.estimatedMinutes ? "ledger" : "default",
  };
}

function addMinutes(base, value) {
  return new Date(base.getTime() + value * 60_000).toISOString();
}

function unique(values) {
  return [...new Set(values.filter(Boolean))];
}

const EXECUTION_STATES = new Set(["assigned", "working", "waiting", "stale", "finished"]);
const STALE_AFTER_MS = 5 * 60 * 1000;

function workspaceLabel(project, value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  if (!isAbsolute(raw)) {
    return [project?.name, ...raw.split(/[\\/]+/).filter(Boolean)].filter(Boolean).join(" › ");
  }
  if (project?.path) {
    const local = relative(resolve(project.path), resolve(raw));
    if (local === "") return project.name || basename(resolve(raw));
    if (local && local !== ".." && !local.startsWith(`..${sep}`) && !isAbsolute(local)) {
      return [project.name, ...local.split(/[\\/]+/).filter(Boolean)].filter(Boolean).join(" › ");
    }
  }
  return basename(resolve(raw));
}

function taskExecutions(task, project, now) {
  const raw = Array.isArray(task.executions)
    ? task.executions
    : task.execution && typeof task.execution === "object" ? [task.execution] : [];
  return raw.flatMap((execution, index) => {
    if (!execution || typeof execution !== "object") return [];
    const heartbeatAt = execution.heartbeatAt || execution.updatedAt || "";
    let state = EXECUTION_STATES.has(execution.state) ? execution.state : "assigned";
    if (state === "working" && heartbeatAt) {
      const age = now.getTime() - Date.parse(heartbeatAt);
      if (Number.isFinite(age) && age > STALE_AFTER_MS) state = "stale";
    }
    const actorType = execution.actorType || task.assigneeType || "agent";
    const actorId = String(execution.actorId || execution.sessionId || `${task.id}:${index}`);
    return [{
      actorType,
      actorId,
      displayName: String(execution.displayName || execution.actorName || execution.actorId || task.assignee || actorType),
      role: String(execution.role || ""),
      model: String(execution.model || ""),
      workspace: workspaceLabel(project, execution.workspacePath || execution.worktreePath || ""),
      startedAt: execution.startedAt || "",
      heartbeatAt,
      state,
    }];
  });
}

function aggregateExecutions(nodes) {
  const rank = { working: 5, waiting: 4, assigned: 3, stale: 2, finished: 1 };
  const actors = new Map();
  for (const execution of nodes.flatMap(node => node.executions || [])) {
    const key = `${execution.actorType}:${execution.actorId}`;
    const previous = actors.get(key);
    if (!previous || (rank[execution.state] || 0) > (rank[previous.state] || 0)) {
      actors.set(key, execution);
    }
  }
  return [...actors.values()];
}

const STATUS_LABEL = {
  done: "完了",
  active: "進行中",
  waiting: "確認待ち",
  blocked: "要対応",
  parallel: "着手可能",
  locked: "待機",
};

export function normalizeLedger(ledger, options = {}) {
  if (!ledger || ledger.schemaVersion !== 1 || !Array.isArray(ledger.projects) ||
      !Array.isArray(ledger.tasks) || !Array.isArray(ledger.events)) {
    throw new Error("Unsupported AI Project Manager ledger.");
  }

  const now = options.now ? new Date(options.now) : new Date();
  const projectId = options.projectId ||
    (ledger.projects.length === 1 ? ledger.projects[0].id : "");
  const projects = projectId
    ? ledger.projects.filter(project => project.id === projectId)
    : ledger.projects;
  const allowedProjects = new Set(projects.map(project => project.id));
  const tasks = ledger.tasks.filter(task => allowedProjects.has(task.projectId));
  const taskById = new Map(tasks.map(task => [task.id, task]));
  const dependents = new Map(tasks.map(task => [task.id, []]));

  for (const task of tasks) {
    for (const dependency of task.dependencies || []) {
      if (dependents.has(dependency)) dependents.get(dependency).push(task.id);
    }
  }

  function dependencyEvidence(task, dependency) {
    const records = Array.isArray(task.dependencyEvidence) ? task.dependencyEvidence : [];
    const record = records.find(item => item?.dependencyId === dependency);
    return record ? {
      verified: record.verified === true,
      source: String(record.source || ""),
      kind: String(record.kind || "declared"),
      confidence: String(record.confidence || (record.verified ? "high" : "low")),
    } : {
      verified: false,
      source: "",
      kind: "declared-only",
      confidence: "unknown",
    };
  }

  const schedule = new Map();
  const visiting = new Set();
  function finish(id) {
    if (schedule.has(id)) return schedule.get(id);
    if (visiting.has(id)) throw new Error(`Dependency cycle at ${id}`);
    visiting.add(id);
    const task = taskById.get(id);
    const dependencies = (task?.dependencies || []).filter(item => taskById.has(item));
    const dependencySchedules = dependencies.map(finish);
    const estimate = taskEstimate(task || {});
    const result = {
      optimistic: estimate.optimistic + Math.max(0, ...dependencySchedules.map(item => item.optimistic)),
      likely: estimate.likely + Math.max(0, ...dependencySchedules.map(item => item.likely)),
      pessimistic: estimate.pessimistic + Math.max(0, ...dependencySchedules.map(item => item.pessimistic)),
      source: estimate.source,
    };
    visiting.delete(id);
    schedule.set(id, result);
    return result;
  }
  for (const task of tasks) finish(task.id);

  const terminalTasks = tasks.filter(task => (dependents.get(task.id) || []).length === 0);
  const projectFinish = Math.max(0, ...terminalTasks.map(task => schedule.get(task.id)?.likely || 0));
  const criticalIds = new Set();
  let cursor = terminalTasks
    .slice()
    .sort((a, b) => (schedule.get(b.id)?.likely || 0) - (schedule.get(a.id)?.likely || 0))[0];
  while (cursor) {
    criticalIds.add(cursor.id);
    cursor = (cursor.dependencies || [])
      .filter(id => taskById.has(id))
      .map(id => taskById.get(id))
      .sort((a, b) => (schedule.get(b.id)?.likely || 0) - (schedule.get(a.id)?.likely || 0))[0];
  }

  const estimatesWithEvidence = tasks.filter(task => taskEstimate(task).source !== "default").length;
  function waitBlocksRequiredPath(task) {
    if (!["waiting-human", "waiting-service"].includes(task.status)) return false;
    const unfinishedDependents = (dependents.get(task.id) || [])
      .map(id => taskById.get(id))
      .filter(item => item && !["done", "cancelled"].includes(item.status));
    const terminalRequiredGate = unfinishedDependents.length === 0 &&
      ["critical", "high"].includes(task.priority);
    return criticalIds.has(task.id) && (unfinishedDependents.length > 0 || terminalRequiredGate);
  }
  const blockedCount = tasks.filter(task =>
    task.status === "blocked" || waitBlocksRequiredPath(task)
  ).length;
  const estimateCoverage = tasks.length ? estimatesWithEvidence / tasks.length : 0;
  const confidence = blockedCount > 0 || estimateCoverage < 0.4
    ? "low"
    : estimateCoverage < 0.8 ? "medium" : "high";

  const milestoneGroups = new Map();
  for (const task of tasks) {
    if (task.milestoneId) {
      if (!milestoneGroups.has(task.milestoneId)) milestoneGroups.set(task.milestoneId, []);
      milestoneGroups.get(task.milestoneId).push(task);
    }
  }

  function taskDepth(task) {
    let depth = 1;
    let cursor = task;
    const seen = new Set();
    while (cursor?.parentTaskId && taskById.has(cursor.parentTaskId) && !seen.has(cursor.id)) {
      seen.add(cursor.id);
      cursor = taskById.get(cursor.parentTaskId);
      depth += 1;
    }
    return depth;
  }

  function descendantIds(taskId) {
    const result = [];
    const queue = tasks.filter(item => item.parentTaskId === taskId);
    const seen = new Set();
    while (queue.length) {
      const item = queue.shift();
      if (!item || seen.has(item.id)) continue;
      seen.add(item.id);
      result.push(item.id);
      queue.push(...tasks.filter(candidate => candidate.parentTaskId === item.id));
    }
    return result;
  }

  function taskNode(task) {
    const eta = schedule.get(task.id);
    const project = projects.find(item => item.id === task.projectId);
    const children = tasks.filter(item => item.parentTaskId === task.id).map(item => item.id);
    const depth = taskDepth(task);
    const collapsedDescendants = depth >= 3 ? descendantIds(task.id) : [];
    const localIds = children.length && depth < 3
      ? children
      : unique([...(task.dependencies || []), task.id, ...(dependents.get(task.id) || [])]);
    const isBlocking = task.status === "blocked" || waitBlocksRequiredPath(task);
    const normalizedStatus = isBlocking ? "blocked" : STATUS[task.status] || "locked";
    const waitingLabel = task.status === "waiting-human"
      ? "確認待ち"
      : task.status === "waiting-service" ? "外部待ち" : "";
    return {
      id: task.id,
      projectId: task.projectId,
      title: task.title,
      objective: task.objective || task.expectedOutput || "",
      reason: task.reason || "",
      verification: task.verification || "",
      expectedOutput: task.expectedOutput || "",
      status: normalizedStatus,
      sourceStatus: task.status,
      waitingLabel,
      isBlocking,
      priority: task.priority || "normal",
      assigneeType: task.assigneeType || "agent",
      assignee: task.assignee || "",
      executions: taskExecutions(task, project, now),
      dependencies: (task.dependencies || []).filter(id => taskById.has(id)),
      dependents: dependents.get(task.id) || [],
      children,
      detailNodeIds: localIds,
      displayDepth: Math.min(depth, 3),
      collapsedDescendantIds: collapsedDescendants,
      progress: task.status === "done" ? 1 : Number(task.progress || 0),
      due: task.due || "",
      blocker: isBlocking
        ? task.reason || task.requiredCapability || "解除条件が未記録"
        : "",
      waitReason: waitingLabel && !isBlocking
        ? task.reason || task.requiredCapability || ""
        : "",
      estimate: taskEstimate(task),
      eta: {
        optimistic: addMinutes(now, eta.optimistic),
        likely: addMinutes(now, eta.likely),
        pessimistic: addMinutes(now, eta.pessimistic),
      },
      critical: criticalIds.has(task.id),
      updatedAt: task.updatedAt || ledger.updatedAt,
    };
  }

  const allNodes = tasks.map(taskNode);
  const nodeById = new Map(allNodes.map(node => [node.id, node]));
  const syntheticNodes = [];
  for (const [milestoneId, members] of milestoneGroups) {
    const memberNodes = members.map(task => nodeById.get(task.id));
    const terminal = memberNodes.slice().sort((a, b) =>
      Date.parse(b.eta.likely) - Date.parse(a.eta.likely)
    )[0];
    syntheticNodes.push({
      id: `milestone:${milestoneId}`,
      projectId: members[0].projectId,
      title: members[0].milestoneTitle || milestoneId,
      objective: members[0].objective || "",
      status: memberNodes.every(node => node.sourceStatus === "done") ? "done"
        : memberNodes.some(node => node.status === "blocked") ? "blocked"
        : memberNodes.some(node => node.status === "active") ? "active"
        : memberNodes.some(node => node.status === "waiting") ? "waiting" : "parallel",
      sourceStatus: "milestone",
      priority: members.some(task => task.priority === "critical") ? "critical" : "normal",
      dependencies: unique(members.flatMap(task => task.dependencies || [])
        .filter(id => !members.some(member => member.id === id))),
      dependents: [],
      children: members.map(task => task.id),
      detailNodeIds: members.map(task => task.id),
      eta: terminal?.eta,
      critical: memberNodes.some(node => node.critical),
      updatedAt: ledger.updatedAt,
      executions: aggregateExecutions(memberNodes),
    });
  }

  const syntheticByMilestone = new Map(
    syntheticNodes.map(node => [node.id.replace("milestone:", ""), node.id])
  );
  const visibleNodes = [
    ...syntheticNodes,
    ...allNodes.filter(node => {
      const task = taskById.get(node.id);
      return !task.parentTaskId && !syntheticByMilestone.has(task.milestoneId);
    }),
  ];

  function visibleAncestor(taskId) {
    let task = taskById.get(taskId);
    const seen = new Set();
    while (task && task.parentTaskId && taskById.has(task.parentTaskId) && !seen.has(task.id)) {
      seen.add(task.id);
      task = taskById.get(task.parentTaskId);
    }
    return syntheticByMilestone.get(task?.milestoneId) || task?.id || "";
  }

  const visibleEdges = [];
  const edgeKeys = new Set();
  for (const task of tasks) {
    for (const dependency of task.dependencies || []) {
      if (!taskById.has(dependency)) continue;
      const from = visibleAncestor(dependency);
      const to = visibleAncestor(task.id);
      const key = `${from}->${to}`;
      const evidence = dependencyEvidence(task, dependency);
      if (from && to && from !== to && edgeKeys.has(key)) {
        const existing = visibleEdges.find(edge => edge.from === from && edge.to === to);
        existing.evidenceItems ||= [existing.evidence];
        existing.evidenceItems.push(evidence);
        existing.evidence = {
          verified: existing.evidenceItems.every(item => item.verified),
          source: existing.evidenceItems.map(item => item.source).filter(Boolean).join("; "),
          kind: "aggregate",
          confidence: existing.evidenceItems.every(item => item.confidence === "high")
            ? "high" : "mixed",
        };
      } else if (from && to && from !== to) {
        edgeKeys.add(key);
        visibleEdges.push({
          from,
          to,
          critical: criticalIds.has(dependency) && criticalIds.has(task.id),
          complete: taskById.get(dependency)?.status === "done",
          evidence,
          evidenceItems: [evidence],
        });
      }
    }
  }

  function latestEta(nodes) {
    return nodes.slice().sort((a, b) =>
      Date.parse(b.eta?.likely || 0) - Date.parse(a.eta?.likely || 0)
    )[0]?.eta;
  }

  function aggregateProject(project) {
    const members = visibleNodes.filter(node => node.projectId === project.id);
    if (members.length <= 10) return { nodes: members, edges: visibleEdges, drillNodes: [] };

    const buckets = new Map();
    for (const member of members) {
      if (!buckets.has(member.status)) buckets.set(member.status, []);
      buckets.get(member.status).push(member);
    }
    const drillNodes = [];
    const groups = [...buckets].map(([status, grouped]) => {
      let detailNodeIds = grouped.map(node => node.id);
      if (grouped.length > 10) {
        const batches = [];
        for (let index = 0; index < grouped.length; index += 8) {
          const batchMembers = grouped.slice(index, index + 8);
          const batch = {
            id: `batch:${project.id}:${status}:${index / 8 + 1}`,
            projectId: project.id,
            title: `${STATUS_LABEL[status] || status} ${index + 1}–${index + batchMembers.length}`,
            objective: `${batchMembers.length}件のタスク`,
            status,
            sourceStatus: "batch",
            priority: batchMembers.some(node => node.priority === "critical") ? "critical" : "normal",
            dependencies: [],
            dependents: [],
            children: batchMembers.map(node => node.id),
            detailNodeIds: batchMembers.map(node => node.id),
            eta: latestEta(batchMembers),
            critical: batchMembers.some(node => node.critical),
            updatedAt: ledger.updatedAt,
            executions: aggregateExecutions(batchMembers),
          };
          batches.push(batch);
          drillNodes.push(batch);
        }
        detailNodeIds = batches.map(node => node.id);
      }
      return {
        id: `group:${project.id}:${status}`,
        projectId: project.id,
        title: `${STATUS_LABEL[status] || status} · ${grouped.length}`,
        objective: `${project.name}の${STATUS_LABEL[status] || status}タスク`,
        status,
        sourceStatus: "group",
        priority: grouped.some(node => node.priority === "critical") ? "critical" : "normal",
        dependencies: [],
        dependents: [],
        children: grouped.map(node => node.id),
        detailNodeIds,
        eta: latestEta(grouped),
        critical: grouped.some(node => node.critical),
        updatedAt: ledger.updatedAt,
        executions: aggregateExecutions(grouped),
      };
    });
    const groupByMember = new Map(groups.flatMap(group =>
      group.children.map(id => [id, group.id])
    ));
    const keys = new Set();
    const edges = visibleEdges.flatMap(edge => {
      const from = groupByMember.get(edge.from);
      const to = groupByMember.get(edge.to);
      const key = `${from}->${to}`;
      if (!from || !to || from === to || keys.has(key)) return [];
      keys.add(key);
      return [{ ...edge, from, to }];
    });
    for (const edge of edges) {
      groups.find(group => group.id === edge.to)?.dependencies.push(edge.from);
      groups.find(group => group.id === edge.from)?.dependents.push(edge.to);
    }
    return { nodes: groups, edges, drillNodes };
  }

  const projectViews = new Map(projects.map(project =>
    [project.id, aggregateProject(project)]
  ));
  const projectNodes = projects.map(project => {
    const view = projectViews.get(project.id);
    const members = allNodes.filter(node => node.projectId === project.id);
    return {
      id: `project:${project.id}`,
      projectId: project.id,
      title: project.name,
      objective: project.objective || project.description || "プロジェクト全体",
      status: members.every(node => node.sourceStatus === "done") ? "done"
        : members.some(node => node.status === "blocked") ? "blocked"
        : members.some(node => node.status === "active") ? "active"
        : members.some(node => node.status === "waiting") ? "waiting" : "parallel",
      sourceStatus: "project",
      priority: members.some(node => node.priority === "critical") ? "critical" : "normal",
      dependencies: [],
      dependents: [],
      children: view.nodes.map(node => node.id),
      detailNodeIds: view.nodes.map(node => node.id),
      eta: latestEta(members),
      critical: members.some(node => node.critical),
      updatedAt: ledger.updatedAt,
      executions: aggregateExecutions(members),
    };
  });
  const portfolioMode = !projectId && projects.length > 1;
  const selectedView = projects.length === 1
    ? projectViews.get(projects[0].id)
    : { nodes: projectNodes, edges: [] };
  const aggregateNodes = [...projectViews.values()].flatMap(view => [
    ...view.nodes.filter(node => node.sourceStatus === "group"),
    ...view.drillNodes,
  ]);

  const done = tasks.filter(task => task.status === "done").length;
  const bottleneck = allNodes
    .filter(node => node.status === "blocked" || node.status === "active")
    .sort((a, b) => {
      if (a.status !== b.status) return a.status === "blocked" ? -1 : 1;
      return Date.parse(b.eta.likely) - Date.parse(a.eta.likely);
    })[0]?.id || "";
  const dependencyCount = tasks.reduce(
    (count, task) => count + (task.dependencies || []).filter(id => taskById.has(id)).length, 0
  );
  const verifiedDependencyCount = tasks.reduce(
    (count, task) => count + (task.dependencies || [])
      .filter(id => taskById.has(id) && dependencyEvidence(task, id).verified).length, 0
  );

  return {
    schemaVersion: 1,
    generatedAt: now.toISOString(),
    ledgerUpdatedAt: ledger.updatedAt,
    projects,
    selectedProjectId: projectId,
    summary: {
      done,
      total: tasks.length,
      blocked: blockedCount,
      bottleneck,
      confidence,
      estimateCoverage: Math.round(estimateCoverage * 100),
      dependencyEvidence: {
        verified: verifiedDependencyCount,
        total: dependencyCount,
        coverage: dependencyCount ? Math.round(verifiedDependencyCount / dependencyCount * 100) : 100,
      },
      eta: {
        likely: addMinutes(now, projectFinish),
        optimistic: addMinutes(now, Math.max(0, ...terminalTasks.map(task => schedule.get(task.id)?.optimistic || 0))),
        pessimistic: addMinutes(now, Math.max(0, ...terminalTasks.map(task => schedule.get(task.id)?.pessimistic || 0))),
      },
    },
    graph: portfolioMode ? { nodes: projectNodes, edges: [] } : selectedView,
    nodes: [...allNodes, ...syntheticNodes, ...aggregateNodes, ...projectNodes],
    events: ledger.events.slice(-30),
  };
}
