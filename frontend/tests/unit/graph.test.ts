import { describe, expect, it } from "vitest";
import {
  applyActivity,
  buildGraph,
  lineage,
  matchesFilter,
  stageNodeId,
  taskLevels,
  taskNodeId,
  type StageNodeData,
} from "@/lib/workflow/graph";
import { agents, execution, models, task, workflow } from "../fixtures/data";

const stage = (graph: ReturnType<typeof buildGraph>, id: string) =>
  graph.nodes.find((n) => n.id === stageNodeId(id as never))!.data as StageNodeData;

describe("taskLevels", () => {
  it("puts each task one wave after its latest dependency", () => {
    const levels = taskLevels([
      task("t1"),
      task("t2", { depends_on: ["t1"] }),
      task("t3", { depends_on: ["t1"] }),
      task("t4", { depends_on: ["t2", "t3"] }),
    ]);
    expect(Object.fromEntries(levels)).toEqual({ t1: 0, t2: 1, t3: 1, t4: 2 });
  });

  it("ignores unknown dependencies and survives a cycle", () => {
    const levels = taskLevels([task("a", { depends_on: ["b", "ghost"] }), task("b", { depends_on: ["a"] })]);
    expect(levels.size).toBe(2);
  });
});

describe("buildGraph", () => {
  const tasks = [
    task("t1", { status: "completed", model_name: "claude-sonnet" }),
    task("t2", { status: "running", depends_on: ["t1"], model_name: "claude-sonnet", agent_name: "testing" }),
  ];

  it("connects the pipeline around the task graph", () => {
    const graph = buildGraph({ workflow: workflow(), tasks, agents, models });
    const ids = graph.edges.map((e) => `${e.source}->${e.target}`);
    expect(ids).toContain(`${stageNodeId("router")}->${taskNodeId("t1")}`);
    expect(ids).toContain(`${taskNodeId("t1")}->${taskNodeId("t2")}`);
    // Only the leaf task feeds the review stage.
    expect(ids).toContain(`${taskNodeId("t2")}->${stageNodeId("review")}`);
    expect(ids).not.toContain(`${taskNodeId("t1")}->${stageNodeId("review")}`);
    expect(ids).toContain(`${stageNodeId("review")}->${stageNodeId("integration")}`);
  });

  it("labels agents and models from backend records, never invented", () => {
    const graph = buildGraph({ workflow: workflow(), tasks, agents, models });
    const t2 = graph.nodes.find((n) => n.id === taskNodeId("t2"))!.data;
    expect(t2.kind === "task" && t2.agentLabel).toBe("Test Engineer");
    expect(t2.kind === "task" && t2.model).toEqual({ name: "claude-sonnet", displayName: "Claude Sonnet", provider: "anthropic" });

    const unknown = buildGraph({ workflow: workflow(), tasks: [task("t9", { agent_name: "mystery" })], agents, models });
    const t9 = unknown.nodes.find((n) => n.id === taskNodeId("t9"))!.data;
    expect(t9.kind === "task" && t9.agentLabel).toBe("mystery");
    expect(t9.kind === "task" && t9.model).toBeUndefined();
  });

  it("animates edges into running work only", () => {
    const graph = buildGraph({ workflow: workflow(), tasks, agents, models });
    const active = graph.edges.filter((e) => e.active).map((e) => e.target);
    expect(active).toEqual([taskNodeId("t2")]);
  });

  it("derives stage status from what is recorded", () => {
    const planning = buildGraph({ workflow: workflow({ status: "planning", brief: null }), tasks: [] });
    expect(stage(planning, "planner").status).toBe("running");
    expect(stage(planning, "decomposition").status).toBe("pending");
    expect(stage(planning, "integration").status).toBe("pending");

    const failedEarly = buildGraph({ workflow: workflow({ status: "failed", brief: null, error: "No model" }), tasks: [] });
    expect(stage(failedEarly, "planner").status).toBe("failed");
    expect(stage(failedEarly, "integration").statusLabel).toBe("Not reached");

    const done = buildGraph({
      workflow: workflow({ status: "completed" }),
      tasks: [task("t1", { status: "completed", model_name: "claude-sonnet" })],
      executions: [execution("planner"), execution("synthesizer", { model_name: "claude-sonnet" })],
    });
    expect(stage(done, "decomposition").status).toBe("completed");
    expect(stage(done, "integration").status).toBe("completed");
    expect(stage(done, "integration").model?.name).toBe("claude-sonnet");
    // No reviews and no test runs were recorded, and the page says so.
    expect(stage(done, "review").statusLabel).toBe("Nothing to check");
  });

  it("counts reviews and test runs, including tests that could not run", () => {
    const graph = buildGraph({
      workflow: workflow({ status: "completed" }),
      tasks: [task("t1", { status: "completed" })],
      reviews: [{ id: "r", task_id: "id-t1", task_key: "t1", reviewer_agent: "reviewer", status: "valid", score: 8, feedback: [], summary: null, created_at: "" }],
      toolCalls: [
        { id: "c1", task_id: "id-t1", task_key: "t1", tool_name: "run_tests", arguments: {}, result: { exit_code: 0 }, succeeded: true, error: null, duration_ms: 10, created_at: "" },
        { id: "c2", task_id: "id-t1", task_key: "t1", tool_name: "run_tests", arguments: {}, result: { skipped: true }, succeeded: false, error: "Tests not run", duration_ms: 0, created_at: "" },
      ],
    });
    const review = stage(graph, "review");
    expect(review.status).toBe("completed");
    expect(review.metrics.find((m) => m.label === "Tests")!.value).toBe("1 passed, 1 not run");
  });
});

describe("lineage and filters", () => {
  const graph = buildGraph({
    workflow: workflow(),
    tasks: [task("a"), task("b", { depends_on: ["a"] }), task("c")],
  });

  it("highlights everything upstream and downstream of a node", () => {
    const related = lineage(taskNodeId("b"), graph.edges);
    expect(related.has(taskNodeId("a"))).toBe(true);
    expect(related.has(stageNodeId("integration"))).toBe(true);
    expect(related.has(taskNodeId("c"))).toBe(false);
  });

  it("filters by status and agent", () => {
    const node = graph.nodes.find((n) => n.id === taskNodeId("a"))!.data;
    expect(matchesFilter(node, "waiting", null)).toBe(true);
    expect(matchesFilter(node, "attention", null)).toBe(false);
    expect(matchesFilter(node, "all", "testing")).toBe(false);
    expect(matchesFilter(node, "all", "backend")).toBe(true);
  });
});

describe("applyActivity", () => {
  const event = (name: string, data: Record<string, unknown> = {}) => ({ event: name, workflow_id: "wf-1", data: { task_key: "t1", ...data } });

  it("tracks what a task is doing and forgets it when it ends", () => {
    let activity = applyActivity({}, event("task_started"));
    expect(activity.t1).toBe("Thinking");
    activity = applyActivity(activity, event("tool_started", { tool: "web_search" }));
    expect(activity.t1).toBe("Using web_search");
    activity = applyActivity(activity, event("tests_started"));
    expect(activity.t1).toBe("Running tests");
    activity = applyActivity(activity, event("task_completed"));
    expect(activity).toEqual({});
  });

  it("returns the same object when nothing changes", () => {
    const start = { t1: "Thinking" };
    expect(applyActivity(start, event("task_started"))).toBe(start);
    expect(applyActivity(start, { event: "plan_ready", workflow_id: "wf-1", data: {} })).toBe(start);
  });
});
