/**
 * The engineering canvas as data: which nodes exist, where they sit, what
 * state each is in, and how they connect. Pure — everything shown is derived
 * from the backend's records (workflow, tasks, executions, reviews, tool
 * calls) plus live activity from the event stream. Nothing here invents a
 * status, a model or a number.
 */

import type {
  Agent,
  Execution,
  LiveEvent,
  Model,
  Task,
  Workflow,
  WorkflowReview,
  WorkflowToolCall,
} from "@/lib/api/types";

export type StageId = "request" | "planner" | "decomposition" | "router" | "review" | "integration";

export type Metric = { label: string; value: string };

export type ModelRef = { name: string; displayName: string; provider: string };

export type StageNodeData = {
  kind: "stage";
  stage: StageId;
  title: string;
  subtitle: string;
  status: string;
  /** Overrides the status's usual wording, e.g. "Not reached". */
  statusLabel?: string;
  model?: ModelRef;
  metrics: Metric[];
};

export type TaskNodeData = {
  kind: "task";
  task: Task;
  agentLabel: string;
  model?: ModelRef;
  status: string;
  activity?: string;
};

export type NodeData = StageNodeData | TaskNodeData;

export type GraphNode = {
  id: string;
  type: "stage" | "task";
  position: { x: number; y: number };
  data: NodeData;
};

export type GraphEdge = {
  id: string;
  source: string;
  target: string;
  /** "bottom"/"top" for edges within a stacked column of stages. */
  sourceHandle?: "bottom";
  targetHandle?: "top";
  kind: "flow" | "dependency";
  active: boolean;
  failed: boolean;
};

export type Graph = { nodes: GraphNode[]; edges: GraphEdge[] };

/** Per-task activity the records do not hold yet: what is happening right now. */
export type Activity = Record<string, string>;

export type GraphInput = {
  workflow: Workflow;
  tasks: Task[];
  agents?: Agent[];
  models?: Model[];
  executions?: Execution[];
  reviews?: WorkflowReview[];
  toolCalls?: WorkflowToolCall[];
  activity?: Activity;
};

export const NODE_WIDTH = 248;
export const TASK_HEIGHT = 118;
export const STAGE_HEIGHT = 112;
const COLUMN = NODE_WIDTH + 64;
const ROW = TASK_HEIGHT + 26;
const STACK_GAP = 36;

// The stages before the tasks are stacked in one column and the stages after
// them in another, so the canvas is only as wide as the task graph plus two
// columns, and fits on screen at a readable zoom.
const LEAD: StageId[] = ["request", "planner", "decomposition", "router"];
const TAIL: StageId[] = ["review", "integration"];

export const taskNodeId = (key: string) => `task:${key}`;
export const stageNodeId = (stage: StageId) => `stage:${stage}`;

const FINISHED = new Set(["completed", "failed", "cancelled", "skipped"]);
const RUNNING_TASK = new Set(["running", "retrying"]);

/** Depth of each task in its dependency graph: 0 for tasks needing nothing. */
export function taskLevels(tasks: Task[]): Map<string, number> {
  const byKey = new Map(tasks.map((t) => [t.key, t]));
  const levels = new Map<string, number>();
  const visiting = new Set<string>();

  const level = (key: string): number => {
    const known = levels.get(key);
    if (known !== undefined) return known;
    if (visiting.has(key)) return 0; // a cycle: the backend refuses these, but never loop
    visiting.add(key);
    const deps = (byKey.get(key)?.depends_on ?? []).filter((d) => byKey.has(d));
    const value = deps.length ? 1 + Math.max(...deps.map(level)) : 0;
    visiting.delete(key);
    levels.set(key, value);
    return value;
  };
  for (const task of tasks) level(task.key);
  return levels;
}

function modelRef(name: string | null | undefined, models: Model[], provider?: string): ModelRef | undefined {
  if (!name) return undefined;
  const model = models.find((m) => m.name === name);
  return {
    name,
    displayName: model?.display_name ?? name,
    provider: model?.provider_name ?? provider ?? "unknown",
  };
}

function lastExecution(executions: Execution[], roles: string[]): Execution | undefined {
  for (let i = executions.length - 1; i >= 0; i--) {
    const e = executions[i]!;
    if (roles.includes(e.role)) return e;
  }
  return undefined;
}

function spent(executions: Execution[], roles: string[]): { calls: number; cost: number } {
  const own = executions.filter((e) => roles.includes(e.role));
  return { calls: own.length, cost: own.reduce((sum, e) => sum + e.estimated_cost, 0) };
}

const usd = (v: number) => (v === 0 ? "$0.00" : v < 0.01 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`);

export function buildGraph(input: GraphInput): Graph {
  const { workflow: wf, tasks } = input;
  const agents = input.agents ?? [];
  const models = input.models ?? [];
  const executions = input.executions ?? [];
  const reviews = input.reviews ?? [];
  const testRuns = (input.toolCalls ?? []).filter((c) => c.tool_name === "run_tests");
  const activity = input.activity ?? {};
  const status = wf.status;
  const hasTasks = tasks.length > 0;
  const ended = status === "failed" || status === "cancelled";

  // --- columns -------------------------------------------------------------
  const levels = taskLevels(tasks);
  const depth = hasTasks ? Math.max(...levels.values()) + 1 : 1;
  const columns: Map<number, Task[]> = new Map();
  for (const task of tasks) {
    const l = levels.get(task.key) ?? 0;
    columns.set(l, [...(columns.get(l) ?? []), task]);
  }
  const tallest = Math.max(1, ...[...columns.values()].map((c) => c.length));
  const bandHeight = tallest * ROW - (ROW - TASK_HEIGHT);
  const middle = bandHeight / 2;
  const stackTop = (count: number) => middle - (count * STAGE_HEIGHT + (count - 1) * STACK_GAP) / 2;

  const nodes: GraphNode[] = [];
  const stage = (stageId: StageId, data: Omit<StageNodeData, "kind" | "stage">) => {
    const lead = LEAD.indexOf(stageId);
    const [stack, index, x] =
      lead >= 0 ? [LEAD, lead, 0] : [TAIL, TAIL.indexOf(stageId), (1 + depth) * COLUMN];
    nodes.push({
      id: stageNodeId(stageId),
      type: "stage",
      position: { x, y: stackTop(stack.length) + index * (STAGE_HEIGHT + STACK_GAP) },
      data: { kind: "stage", stage: stageId, ...data },
    });
  };

  // --- request -------------------------------------------------------------
  stage("request", {
    title: "User request",
    subtitle: wf.goal,
    status: "completed",
    statusLabel: "Received",
    metrics: [
      { label: "Strategy", value: wf.routing_strategy.replace(/_/g, " ") },
      { label: "Budget", value: usd(wf.max_cost_usd) },
    ],
  });

  // --- planner (understanding the request) ---------------------------------
  const commander = lastExecution(executions, ["commander"]);
  const understood = Boolean(wf.brief) || hasTasks;
  const plannerStatus = understood
    ? "completed"
    : status === "planning"
      ? "running"
      : ended
        ? status
        : "pending";
  const plannerCost = spent(executions, ["commander"]);
  stage("planner", {
    title: "Planner",
    subtitle: (wf.brief?.objective as string | undefined) ?? "Turns the request into a brief",
    status: plannerStatus,
    statusLabel: ended && !understood ? "Not reached" : undefined,
    model: modelRef(commander?.model_name, models, commander?.provider_name),
    metrics: [
      { label: "Complexity", value: String(wf.brief?.complexity ?? "—") },
      { label: "Cost", value: plannerCost.calls ? usd(plannerCost.cost) : "—" },
    ],
  });

  // --- decomposition (the plan) --------------------------------------------
  const planner = lastExecution(executions, ["planner"]);
  const decompositionStatus = hasTasks
    ? "completed"
    : status === "planning" && understood
      ? "running"
      : ended
        ? status
        : "pending";
  const planCost = spent(executions, ["planner"]);
  stage("decomposition", {
    title: "Task decomposition",
    subtitle: hasTasks
      ? `${tasks.length} task${tasks.length === 1 ? "" : "s"} in ${depth} wave${depth === 1 ? "" : "s"}`
      : "Splits the brief into dependent tasks",
    status: decompositionStatus,
    statusLabel: ended && !hasTasks ? "Not reached" : undefined,
    model: modelRef(planner?.model_name, models, planner?.provider_name),
    metrics: [
      { label: "Tasks", value: hasTasks ? String(tasks.length) : "—" },
      { label: "Cost", value: planCost.calls ? usd(planCost.cost) : "—" },
    ],
  });

  // --- router ----------------------------------------------------------------
  const routed = tasks.filter((t) => t.model_name);
  const distinct = new Set(routed.map((t) => t.model_name));
  stage("router", {
    title: "Model router",
    subtitle: wf.pinned_model
      ? `Pinned to ${modelRef(wf.pinned_model, models)?.displayName}${wf.allow_fallback ? ", fallback allowed" : ", no fallback"}`
      : `Scores models per task (${wf.routing_strategy.replace(/_/g, " ")})`,
    status: hasTasks && routed.length === tasks.length ? "completed" : hasTasks ? "running" : ended ? status : "pending",
    statusLabel: ended && !hasTasks ? "Not reached" : undefined,
    metrics: [
      { label: "Routed", value: hasTasks ? `${routed.length}/${tasks.length}` : "—" },
      { label: "Models", value: distinct.size ? String(distinct.size) : "—" },
    ],
  });

  // --- tasks -----------------------------------------------------------------
  const agentLabel = (name: string | null | undefined) =>
    (name && agents.find((a) => a.name === name)?.display_name) || name || "Unassigned";

  for (const [level, column] of columns) {
    column.sort((a, b) => a.priority - b.priority || a.key.localeCompare(b.key));
    const offset = (bandHeight - (column.length * ROW - (ROW - TASK_HEIGHT))) / 2;
    column.forEach((task, row) => {
      nodes.push({
        id: taskNodeId(task.key),
        type: "task",
        position: { x: (1 + level) * COLUMN, y: offset + row * ROW },
        data: {
          kind: "task",
          task,
          agentLabel: agentLabel(task.agent_name),
          model: modelRef(task.model_name, models),
          status: task.status,
          activity: RUNNING_TASK.has(task.status) ? activity[task.key] : undefined,
        },
      });
    });
  }

  // --- testing and review ------------------------------------------------------
  const reviewing = Object.entries(activity).some(
    ([key, what]) => /review|test/i.test(what) && RUNNING_TASK.has(tasks.find((t) => t.key === key)?.status ?? ""),
  );
  const checks = reviews.length + testRuns.length;
  const passedReviews = reviews.filter((r) => r.status === "valid").length;
  const passedTests = testRuns.filter((c) => c.succeeded).length;
  const skippedTests = testRuns.filter((c) => (c.result as { skipped?: boolean } | null)?.skipped).length;
  const allTasksFinished = hasTasks && tasks.every((t) => FINISHED.has(t.status));
  let reviewStatus: string;
  let reviewLabel: string | undefined;
  if (reviewing || status === "validating") reviewStatus = "running";
  else if (status === "synthesizing" || status === "completed" || allTasksFinished) {
    reviewStatus = checks ? "completed" : "skipped";
    reviewLabel = checks ? undefined : "Nothing to check";
  } else if (ended) {
    reviewStatus = checks ? status : "skipped";
    reviewLabel = checks ? undefined : "Not reached";
  } else reviewStatus = checks ? "running" : "pending";
  stage("review", {
    title: "Testing and review",
    subtitle: "Runs tests in the sandbox; a reviewer model checks each result",
    status: reviewStatus,
    statusLabel: reviewLabel,
    metrics: [
      { label: "Reviews", value: reviews.length ? `${passedReviews}/${reviews.length} passed` : "—" },
      {
        label: "Tests",
        value: testRuns.length
          ? `${passedTests} passed${skippedTests ? `, ${skippedTests} not run` : ""}${testRuns.length - passedTests - skippedTests ? `, ${testRuns.length - passedTests - skippedTests} failed` : ""}`
          : "—",
      },
    ],
  });

  // --- final integration ---------------------------------------------------------
  const synthesizer = lastExecution(executions, ["synthesizer"]);
  let integrationStatus: string;
  let integrationLabel: string | undefined;
  if (status === "synthesizing") integrationStatus = "running";
  else if (status === "completed") integrationStatus = "completed";
  else if (ended) {
    integrationStatus = synthesizer ? status : "skipped";
    integrationLabel = synthesizer ? undefined : "Not reached";
  } else integrationStatus = "pending";
  const synthCost = spent(executions, ["synthesizer"]);
  stage("integration", {
    title: "Final integration",
    subtitle: "Combines every result into one answer",
    status: integrationStatus,
    statusLabel: integrationLabel,
    model: modelRef(synthesizer?.model_name, models, synthesizer?.provider_name),
    metrics: [
      { label: "Total cost", value: usd(wf.total_cost_usd) },
      { label: "Integration", value: synthCost.calls ? usd(synthCost.cost) : "—" },
    ],
  });

  // --- edges -------------------------------------------------------------------------
  const statusOf = new Map(nodes.map((n) => [n.id, n.data.status]));
  const edges: GraphEdge[] = [];
  const connect = (source: string, target: string, kind: GraphEdge["kind"], stacked = false) => {
    const targetStatus = statusOf.get(target) ?? "";
    edges.push({
      id: `${source}->${target}`,
      source,
      target,
      ...(stacked ? { sourceHandle: "bottom" as const, targetHandle: "top" as const } : {}),
      kind,
      active: targetStatus === "running" || RUNNING_TASK.has(targetStatus) || targetStatus === "validating",
      failed: targetStatus === "failed",
    });
  };
  connect(stageNodeId("request"), stageNodeId("planner"), "flow", true);
  connect(stageNodeId("planner"), stageNodeId("decomposition"), "flow", true);
  connect(stageNodeId("decomposition"), stageNodeId("router"), "flow", true);

  const keys = new Set(tasks.map((t) => t.key));
  const dependents = new Set<string>();
  for (const task of tasks) {
    const deps = (task.depends_on ?? []).filter((d) => keys.has(d));
    if (deps.length === 0) connect(stageNodeId("router"), taskNodeId(task.key), "flow");
    for (const dep of deps) {
      dependents.add(dep);
      connect(taskNodeId(dep), taskNodeId(task.key), "dependency");
    }
  }
  if (hasTasks) {
    for (const task of tasks) {
      if (!dependents.has(task.key)) connect(taskNodeId(task.key), stageNodeId("review"), "flow");
    }
  } else {
    connect(stageNodeId("router"), stageNodeId("review"), "flow");
  }
  connect(stageNodeId("review"), stageNodeId("integration"), "flow", true);

  return { nodes, edges };
}

/** Every node upstream or downstream of `id`: what it waits on and what waits on it. */
export function lineage(id: string, edges: GraphEdge[]): Set<string> {
  const related = new Set([id]);
  const walk = (start: string, forward: boolean) => {
    const queue = [start];
    while (queue.length) {
      const current = queue.shift()!;
      for (const edge of edges) {
        const [from, to] = forward ? [edge.source, edge.target] : [edge.target, edge.source];
        if (from === current && !related.has(to)) {
          related.add(to);
          queue.push(to);
        }
      }
    }
  };
  walk(id, true);
  walk(id, false);
  return related;
}

export type StatusFilter = "all" | "active" | "attention" | "done" | "waiting";

const FILTER: Record<Exclude<StatusFilter, "all">, (status: string) => boolean> = {
  active: (s) => ["running", "retrying", "planning", "validating", "synthesizing", "queued"].includes(s),
  attention: (s) => ["failed", "retrying", "awaiting_approval", "paused"].includes(s),
  done: (s) => ["completed", "skipped", "cancelled"].includes(s),
  waiting: (s) => ["pending", "waiting", "queued"].includes(s),
};

/** Whether a node passes the canvas filters. Stage nodes ignore the agent filter. */
export function matchesFilter(data: NodeData, status: StatusFilter, agent: string | null): boolean {
  if (status !== "all" && !FILTER[status](data.status)) return false;
  if (agent && data.kind === "task" && data.task.agent_name !== agent) return false;
  if (agent && data.kind === "stage") return false;
  return true;
}

/**
 * What a task is doing right now, from live events. Cleared when the task
 * finishes; the records take over from there.
 */
export function applyActivity(activity: Activity, event: LiveEvent): Activity {
  const key = typeof event.data?.task_key === "string" ? event.data.task_key : null;
  if (!key) return activity;
  const d = event.data;
  let next: string | null | undefined;
  switch (event.event) {
    case "task_queued":
      next = "Queued";
      break;
    case "task_started":
      next = "Thinking";
      break;
    case "task_progress":
      next = `Tool round ${d.tool_round}/${d.max_tool_rounds}`;
      break;
    case "tool_started":
      next = `Using ${String(d.tool ?? "a tool")}`;
      break;
    case "tests_started":
      next = "Running tests";
      break;
    case "validation_started":
      next = "In review";
      break;
    case "retry_started":
      next = "Retrying";
      break;
    case "tool_completed":
    case "tests_completed":
    case "tests_skipped":
    case "review_completed":
      next = "Thinking";
      break;
    case "task_completed":
    case "task_failed":
      next = null;
      break;
    default:
      return activity;
  }
  if (next === null) {
    if (!(key in activity)) return activity;
    const rest = { ...activity };
    delete rest[key];
    return rest;
  }
  return activity[key] === next ? activity : { ...activity, [key]: next };
}
