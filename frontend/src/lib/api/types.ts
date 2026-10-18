/**
 * Shapes returned by the backend. Generated from its OpenAPI document
 * (npm run api:types), so a backend change that breaks the UI fails the
 * type check instead of failing in a browser.
 */

import type { components } from "./schema";

type S = components["schemas"];

export type User = S["UserOut"];
export type Project = S["ProjectOut"];
export type Workflow = S["WorkflowOut"];
export type WorkflowCreate = S["WorkflowCreate"];
export type Task = S["TaskDetail"];
export type TaskUpdate = S["TaskUpdate"];
export type Artifact = S["ArtifactOut"];
export type ArtifactSummary = S["ArtifactSummary"];
export type Attachment = S["AttachmentOut"];
export type Execution = S["ExecutionOut"];
export type Review = S["ReviewOut"];
export type WorkflowReview = S["WorkflowReviewOut"];
export type ToolCall = S["ToolCallOut"];
export type WorkflowToolCall = S["WorkflowToolCallOut"];
export type WorkflowEvent = S["WorkflowEventOut"];
export type Agent = S["AgentOut"];
export type AgentUpdate = S["AgentUpdate"];
export type Model = S["ModelOut"];
export type Provider = S["ProviderOut"];
export type ProviderUpdate = S["ProviderUpdate"];
export type Tool = S["ToolOut"];
export type Usage = S["UsageOut"];
export type UsageDay = S["UsageDay"];
export type AuditLog = S["AuditLogOut"];
export type ApiKey = S["ApiKeyOut"];
export type ApiKeyCreated = S["ApiKeyCreated"];
export type Readiness = S["Readiness"];
export type Health = S["Health"];
export type Accepted = S["Accepted"];
export type RoutingStrategy = S["RoutingStrategy"];
export type TaskType = S["TaskType"];

export type Page<T> = { items: T[]; total: number; limit: number; offset: number };

export type WorkflowStatus =
  | "pending"
  | "planning"
  | "awaiting_approval"
  | "running"
  | "validating"
  | "synthesizing"
  | "completed"
  | "failed"
  | "cancelled"
  | "paused";

export type TaskStatus =
  | "pending"
  | "planning"
  | "queued"
  | "running"
  | "waiting"
  | "completed"
  | "failed"
  | "retrying"
  | "cancelled"
  | "skipped";

export const TERMINAL_WORKFLOW: ReadonlySet<string> = new Set([
  "completed",
  "failed",
  "cancelled",
]);

/** Workflows that are doing (or about to do) work. */
export const ACTIVE_WORKFLOW: readonly WorkflowStatus[] = [
  "pending",
  "planning",
  "awaiting_approval",
  "running",
  "validating",
  "synthesizing",
  "paused",
];

/** One message on a workflow's live stream (and its replay). */
export type LiveEvent = {
  id?: string;
  seq?: number;
  event: string;
  workflow_id: string;
  timestamp?: string;
  data: Record<string, unknown>;
};
