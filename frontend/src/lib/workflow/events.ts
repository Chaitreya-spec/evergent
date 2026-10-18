/**
 * Live workflow events, turned into console lines. Pure: the same event
 * always reads the same, whether it arrived live or in a replay.
 */

import type { LiveEvent } from "@/lib/api/types";
import { duration, money } from "@/lib/format";

/** Every named event the stream can carry. EventSource only delivers names it listens for. */
export const STREAM_EVENTS = [
  "subscribed",
  "stream_end",
  "workflow_created",
  "workflow_started",
  "planning_started",
  "plan_ready",
  "plan_approved",
  "task_created",
  "task_queued",
  "task_started",
  "task_progress",
  "task_completed",
  "task_failed",
  "model_selected",
  "retry_started",
  "tool_started",
  "tool_completed",
  "tests_started",
  "tests_completed",
  "tests_skipped",
  "validation_started",
  "review_completed",
  "artifact_created",
  "synthesis_started",
  "workflow_completed",
  "workflow_failed",
  "workflow_paused",
  "workflow_cancelled",
  "budget_exceeded",
] as const;

export type LogLevel = "debug" | "info" | "success" | "warn" | "error";

export type LogLine = {
  /** Stable across replays: the event's sequence number, else its id. */
  key: string;
  seq?: number;
  at?: string;
  event: string;
  level: LogLevel;
  task?: string;
  text: string;
};

const str = (value: unknown): string | undefined =>
  typeof value === "string" && value.length > 0 ? value : undefined;
const num = (value: unknown): number | undefined =>
  typeof value === "number" && Number.isFinite(value) ? value : undefined;

function shorten(text: string | undefined, max = 240): string {
  if (!text) return "";
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

/** A console line for one event, or null for transport-only messages. */
export function describeEvent(event: LiveEvent): LogLine | null {
  const d = event.data ?? {};
  const task = str(d.task_key);
  const key = event.seq !== undefined ? `s${event.seq}` : (event.id ?? `${event.event}-${event.timestamp}`);
  const line = (level: LogLevel, text: string): LogLine => ({
    key,
    seq: event.seq,
    at: event.timestamp,
    event: event.event,
    level,
    task,
    text,
  });

  switch (event.event) {
    case "subscribed":
    case "ping":
    case "stream_end":
      return null;
    case "workflow_created":
      return line("info", "Workflow created");
    case "workflow_started":
      return line("info", d.resumed ? "Workflow resumed" : "Workflow started");
    case "planning_started":
      return line("info", "Planner started breaking the request into tasks");
    case "plan_ready": {
      const count = num(d.task_count) ?? 0;
      return line(
        "info",
        `Plan ready: ${count} task${count === 1 ? "" : "s"}${d.awaiting_approval ? " — waiting for approval" : ""}`,
      );
    }
    case "plan_approved":
      return line("success", "Plan approved");
    case "task_created":
      return line("debug", `Created "${str(d.title) ?? task}" for ${str(d.agent) ?? "an agent"}`);
    case "model_selected":
      return line("info", `Routed to ${str(d.model) ?? "a model"}${d.reason ? ` — ${shorten(str(d.reason), 160)}` : ""}`);
    case "task_queued":
      return line("debug", "Queued");
    case "task_started": {
      const attempt = num(d.attempt) ?? 1;
      return line("info", `Started on ${str(d.model) ?? "a model"}${attempt > 1 ? ` (attempt ${attempt})` : ""}`);
    }
    case "task_progress":
      return line("debug", `Tool round ${num(d.tool_round) ?? "?"} of ${num(d.max_tool_rounds) ?? "?"}`);
    case "tool_started":
      return line("debug", `Calling ${str(d.tool) ?? "a tool"}`);
    case "tool_completed":
      return d.ok
        ? line("debug", `${str(d.tool) ?? "Tool"} finished in ${duration(num(d.duration_ms))}`)
        : line("warn", `${str(d.tool) ?? "Tool"} failed: ${shorten(str(d.error)) || "no detail"}`);
    case "tests_started":
      return line("info", `Running tests${d.command ? `: ${shorten(str(d.command), 120)}` : ""}`);
    case "tests_completed":
      return d.passed
        ? line("success", "Tests passed")
        : line("error", `Tests failed (${str(d.stage) ?? "run"} exit ${num(d.exit_code) ?? "?"})`);
    case "tests_skipped":
      return line("warn", `Tests not run: ${shorten(str(d.reason)) || "no sandbox"}`);
    case "validation_started":
      return line("info", "Review started");
    case "review_completed": {
      const ok = d.status === "valid";
      const score = num(d.score);
      return line(
        ok ? "success" : "warn",
        `Review: ${String(d.status ?? "done").replace(/_/g, " ")}${score !== undefined ? ` (${score.toFixed(1)}/10)` : ""}`,
      );
    }
    case "artifact_created":
      return line("info", `Wrote ${str(d.path) ?? "a file"}`);
    case "retry_started":
      return line("warn", `Retrying (attempt ${num(d.attempt) !== undefined ? (num(d.attempt) as number) + 1 : "?"})`);
    case "task_completed": {
      const files = Array.isArray(d.artifacts) ? d.artifacts.length : 0;
      return line("success", `Completed${files ? ` — ${files} file${files === 1 ? "" : "s"}` : ""}`);
    }
    case "task_failed":
      return line("error", `Failed: ${shorten(str(d.error)) || "no detail"}`);
    case "synthesis_started":
      return line("info", "Final integration started");
    case "workflow_completed":
      return line(
        "success",
        `Workflow completed${num(d.cost_usd) !== undefined ? ` — ${money(num(d.cost_usd))} estimated` : ""}`,
      );
    case "workflow_failed":
      return line("error", `Workflow failed: ${shorten(str(d.error), 400) || "no detail"}`);
    case "budget_exceeded":
      return line("error", `Stopped by the spending limit: ${shorten(str(d.error), 400)}`);
    case "workflow_paused":
      return line("warn", d.reason === "interrupted" ? "Paused: the process running it stopped" : "Workflow paused");
    case "workflow_cancelled":
      return line("warn", "Workflow cancelled");
    default:
      return line("debug", event.event.replace(/_/g, " "));
  }
}

/**
 * Merge new lines into a log, in sequence order, dropping any already
 * present — a reconnect may deliver an event twice.
 */
export function appendLines(log: LogLine[], incoming: LogLine[], max = 2000): LogLine[] {
  if (incoming.length === 0) return log;
  const seen = new Set(log.map((l) => l.key));
  const fresh = incoming.filter((l) => !seen.has(l.key) && seen.add(l.key));
  if (fresh.length === 0) return log;
  const merged = [...log, ...fresh];
  const ordered = merged.every((l, i) => i === 0 || (l.seq ?? Infinity) >= (merged[i - 1]!.seq ?? -Infinity))
    ? merged
    : [...merged].sort((a, b) => (a.seq ?? Number.MAX_SAFE_INTEGER) - (b.seq ?? Number.MAX_SAFE_INTEGER));
  return ordered.length > max ? ordered.slice(-max) : ordered;
}

/** Events after which the workflow's stored records changed. */
export const REFRESHES = {
  workflow: new Set([
    "workflow_started",
    "planning_started",
    "plan_ready",
    "plan_approved",
    "synthesis_started",
    "workflow_completed",
    "workflow_failed",
    "workflow_paused",
    "workflow_cancelled",
    "budget_exceeded",
    "task_completed",
    "task_failed",
  ]),
  tasks: new Set([
    "plan_ready",
    "task_created",
    "model_selected",
    "task_queued",
    "task_started",
    "task_completed",
    "task_failed",
    "retry_started",
    "workflow_paused",
    "workflow_cancelled",
    "workflow_failed",
  ]),
  artifacts: new Set(["artifact_created", "task_completed"]),
  toolCalls: new Set(["tool_completed", "tests_completed", "tests_skipped"]),
  reviews: new Set(["review_completed"]),
  executions: new Set(["plan_ready", "task_completed", "task_failed", "review_completed", "workflow_completed", "workflow_failed"]),
} as const;
