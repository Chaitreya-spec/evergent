import { ReactFlowProvider } from "@xyflow/react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TaskNode } from "@/components/workspace/nodes";
import { task } from "../fixtures/data";

function renderNode(data: Parameters<typeof TaskNode>[0]["data"]) {
  const props = { id: "task:t1", data, selected: false, type: "task", dragging: false, zIndex: 0, isConnectable: false, positionAbsoluteX: 0, positionAbsoluteY: 0, draggable: false, selectable: true, deletable: false } as Parameters<typeof TaskNode>[0];
  return render(
    <ReactFlowProvider>
      <TaskNode {...props} />
    </ReactFlowProvider>,
  );
}

describe("TaskNode", () => {
  it("shows the agent, task, model, status and retries", () => {
    renderNode({
      kind: "task",
      task: task("t1", { title: "Write the API", status: "retrying", retry_count: 2 }),
      agentLabel: "Backend Engineer",
      model: { name: "claude-sonnet", displayName: "Claude Sonnet", provider: "anthropic" },
      status: "retrying",
      activity: "Using web_search",
    });
    expect(screen.getByText("Backend Engineer")).toBeInTheDocument();
    expect(screen.getByText("Write the API")).toBeInTheDocument();
    expect(screen.getByText(/Claude Sonnet/)).toBeInTheDocument();
    expect(screen.getByText("Retrying")).toBeInTheDocument();
    expect(screen.getByText("Using web_search")).toBeInTheDocument();
    expect(screen.getByTitle("2 retries")).toBeInTheDocument();
  });

  it("says when no model has been chosen instead of guessing one", () => {
    renderNode({ kind: "task", task: task("t1"), agentLabel: "Backend Engineer", status: "pending" });
    expect(screen.getByText("No model yet")).toBeInTheDocument();
  });

  it("surfaces a failure's reason on the node", () => {
    renderNode({ kind: "task", task: task("t1", { status: "failed", error: "Provider returned 500" }), agentLabel: "Backend Engineer", status: "failed" });
    expect(screen.getByText("Provider returned 500")).toBeInTheDocument();
  });
});
