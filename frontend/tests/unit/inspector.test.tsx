import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Inspector, type InspectorData } from "@/components/workspace/Inspector";
import { buildGraph, taskNodeId } from "@/lib/workflow/graph";
import { agents, execution, models, task, workflow } from "../fixtures/data";
import { mockApi, renderWithClient } from "../fixtures/render";

afterEach(() => vi.unstubAllGlobals());

function setup(overrides: Partial<InspectorData> = {}, selected = taskNodeId("t2")) {
  const data: InspectorData = {
    workflow: workflow(),
    tasks: [
      task("t1", { status: "completed" }),
      task("t2", {
        status: "failed",
        depends_on: ["t1"],
        agent_name: "testing",
        model_name: "claude-sonnet",
        routing_reason: "Claude Sonnet scores 9.5 on testing.",
        error: "Tests failed twice",
        retry_count: 1,
      }),
    ],
    agents,
    models,
    executions: [execution("testing", { task_id: "id-t2", succeeded: false, error: "429 rate limited" })],
    reviews: [],
    toolCalls: [],
    artifacts: [],
    ...overrides,
  };
  const graph = buildGraph(data);
  const onSelect = vi.fn();
  mockApi({});
  renderWithClient(<Inspector node={graph.nodes.find((n) => n.id === selected) ?? null} onSelect={onSelect} onOpenFile={vi.fn()} {...data} />);
  return { onSelect };
}

describe("Inspector", () => {
  it("shows the task's real assignment, error and retry history", () => {
    setup();
    expect(screen.getAllByText("Test Engineer").length).toBeGreaterThan(0);
    expect(screen.getByText("Claude Sonnet")).toBeInTheDocument();
    expect(screen.getByText("Claude Sonnet scores 9.5 on testing.")).toBeInTheDocument();
    expect(screen.getByText("Tests failed twice")).toBeInTheDocument();
    expect(screen.getByText("429 rate limited")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("jumps to a dependency", async () => {
    const { onSelect } = setup();
    await userEvent.setup().click(screen.getByRole("button", { name: /Task t1/ }));
    expect(onSelect).toHaveBeenCalledWith(taskNodeId("t1"));
  });

  it("allows editing only before the task runs", () => {
    setup({ workflow: workflow({ status: "awaiting_approval" }), tasks: [task("t2", { status: "pending", agent_name: "testing" })] });
    expect(screen.getByRole("button", { name: /edit/i })).toBeInTheDocument();
  });

  it("explains how to use it when nothing is selected", () => {
    setup({}, "none");
    expect(screen.getByText("Nothing selected")).toBeInTheDocument();
  });
});
