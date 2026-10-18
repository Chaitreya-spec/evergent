import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { Console, type ConsoleTab } from "@/components/workspace/Console";
import type { LogLine } from "@/lib/workflow/events";
import { task, workflow } from "../fixtures/data";
import { renderWithClient } from "../fixtures/render";

const log: LogLine[] = [
  { key: "s1", seq: 1, event: "task_started", level: "info", task: "t1", text: "Started on Claude Sonnet", at: "2026-10-14T10:00:00Z" },
  { key: "s2", seq: 2, event: "task_failed", level: "error", task: "t2", text: "Failed: timeout", at: "2026-10-14T10:00:05Z" },
];

function Harness() {
  const [tab, setTab] = useState<ConsoleTab>("logs");
  return (
    <Console
      tab={tab}
      onTab={setTab}
      workflow={workflow()}
      tasks={[task("t1"), task("t2", { status: "failed", error: "timeout" })]}
      log={log}
      stream="live"
      toolCalls={[
        { id: "c", task_id: "id-t1", task_key: "t1", tool_name: "run_tests", arguments: { command: "pytest -q" }, result: { exit_code: 1, output: "1 failed" }, succeeded: false, error: "test exit 1", duration_ms: 1200, created_at: "2026-10-14T10:00:03Z" },
      ]}
      executions={[]}
      artifacts={[]}
      selectedTask={null}
      activeFile={null}
      onActiveFile={vi.fn()}
    />
  );
}

describe("Console", () => {
  it("shows live logs and filters them", async () => {
    renderWithClient(<Harness />);
    const user = userEvent.setup();
    const lines = screen.getByRole("log");
    expect(within(lines).getByText("Started on Claude Sonnet")).toBeInTheDocument();
    expect(screen.getByText("Live")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Search logs"), "timeout");
    expect(within(lines).queryByText("Started on Claude Sonnet")).not.toBeInTheDocument();
    expect(within(lines).getByText("Failed: timeout")).toBeInTheDocument();
  });

  it("is honest that there is no terminal", async () => {
    renderWithClient(<Harness />);
    await userEvent.setup().click(screen.getByRole("tab", { name: /terminal/i }));
    expect(screen.getByText("No interactive terminal")).toBeInTheDocument();
  });

  it("lists test runs with exit codes and counts errors", async () => {
    renderWithClient(<Harness />);
    const user = userEvent.setup();
    expect(screen.getByRole("tab", { name: /errors 2/i })).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: /test results/i }));
    expect(screen.getByText("pytest -q")).toBeInTheDocument();
    expect(screen.getByText("exit 1")).toBeInTheDocument();
  });
});
