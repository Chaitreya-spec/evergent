/**
 * Every recorded failure in a workflow, newest concerns first: the workflow's
 * own error, failed tasks, failed tool calls and failed model calls. Pure.
 */

import type { Execution, Task, Workflow, WorkflowToolCall } from "@/lib/api/types";

export type Problem = {
  id: string;
  source: "workflow" | "task" | "tool" | "model";
  taskKey?: string;
  title: string;
  detail: string;
  at?: string | null;
};

export function collectProblems(
  workflow: Workflow,
  tasks: Task[],
  toolCalls: WorkflowToolCall[],
  executions: Execution[],
): Problem[] {
  const keyOf = new Map(tasks.map((t) => [t.id, t.key]));
  const problems: Problem[] = [];
  if (workflow.error && workflow.status !== "completed") {
    problems.push({ id: `wf-${workflow.id}`, source: "workflow", title: `Workflow ${workflow.status}`, detail: workflow.error, at: workflow.completed_at });
  }
  for (const task of tasks) {
    if (task.status === "failed" && task.error) {
      problems.push({ id: `task-${task.id}`, source: "task", taskKey: task.key, title: `${task.title} failed`, detail: task.error, at: task.completed_at });
    }
  }
  for (const call of toolCalls) {
    if (!call.succeeded) {
      problems.push({
        id: `tool-${call.id}`,
        source: "tool",
        taskKey: call.task_key,
        title: `${call.tool_name} failed`,
        detail: call.error ?? "No detail recorded",
        at: call.created_at,
      });
    }
  }
  for (const e of executions) {
    if (!e.succeeded) {
      problems.push({
        id: `model-${e.id}`,
        source: "model",
        taskKey: e.task_id ? keyOf.get(e.task_id) : undefined,
        title: `${e.model_name} call failed (${e.role})`,
        detail: e.error ?? "No detail recorded",
      });
    }
  }
  return problems;
}
