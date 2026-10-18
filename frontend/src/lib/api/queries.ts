"use client";

import { useQuery, type UseQueryOptions } from "@tanstack/react-query";
import { api, withQuery } from "./client";
import type {
  Agent,
  ApiKey,
  Artifact,
  ArtifactSummary,
  Attachment,
  AuditLog,
  Execution,
  Health,
  Model,
  Page,
  Project,
  Provider,
  Readiness,
  Task,
  Tool,
  Usage,
  User,
  Workflow,
  WorkflowReview,
  WorkflowStatus,
  WorkflowToolCall,
} from "./types";
import { ACTIVE_WORKFLOW } from "./types";

/** Every cache key in one place, so invalidation cannot drift from reads. */
export const keys = {
  me: ["me"] as const,
  projects: ["projects"] as const,
  workflows: (params: WorkflowListParams = {}) => ["workflows", params] as const,
  allWorkflows: ["workflows"] as const,
  workflow: (id: string) => ["workflow", id] as const,
  tasks: (id: string) => ["workflow", id, "tasks"] as const,
  artifacts: (id: string) => ["workflow", id, "artifacts"] as const,
  executions: (id: string) => ["workflow", id, "executions"] as const,
  toolCalls: (id: string) => ["workflow", id, "tool-calls"] as const,
  reviews: (id: string) => ["workflow", id, "reviews"] as const,
  agents: ["agents"] as const,
  models: ["models"] as const,
  providers: ["providers"] as const,
  tools: ["tools"] as const,
  usage: (days: number, projectId?: string) => ["usage", days, projectId ?? null] as const,
  audit: (action?: string) => ["audit", action ?? null] as const,
  apiKeys: ["api-keys"] as const,
  files: (projectId?: string) => ["files", projectId ?? null] as const,
  attachments: (projectId: string) => ["attachments", projectId] as const,
  readiness: ["readiness"] as const,
  health: ["health"] as const,
};

type Extra<T> = Omit<UseQueryOptions<T>, "queryKey" | "queryFn">;

export const useMe = () => useQuery({ queryKey: keys.me, queryFn: () => api<User>("/auth/me") });

export const useProjects = () =>
  useQuery({
    queryKey: keys.projects,
    queryFn: () => api<Page<Project>>("/projects?limit=200"),
    select: (page) => page.items,
  });

export type WorkflowListParams = {
  project_id?: string;
  status?: WorkflowStatus[];
  q?: string;
  limit?: number;
  offset?: number;
};

export const useWorkflows = (params: WorkflowListParams = {}, extra: Extra<Page<Workflow>> = {}) =>
  useQuery({
    queryKey: keys.workflows(params),
    queryFn: () => api<Page<Workflow>>(withQuery("/workflows", params)),
    ...extra,
  });

/** Workflows still in flight, for the sidebar. Polled: they change on their own. */
export const useActiveWorkflows = () =>
  useWorkflows({ status: [...ACTIVE_WORKFLOW], limit: 20 }, { refetchInterval: 5000 });

export const useWorkflow = (id: string, extra: Extra<Workflow> = {}) =>
  useQuery({ queryKey: keys.workflow(id), queryFn: () => api<Workflow>(`/workflows/${id}`), ...extra });

export const useTasks = (id: string, extra: Extra<Task[]> = {}) =>
  useQuery({ queryKey: keys.tasks(id), queryFn: () => api<Task[]>(`/workflows/${id}/tasks`), ...extra });

export const useWorkflowArtifacts = (id: string) =>
  useQuery({
    queryKey: keys.artifacts(id),
    queryFn: () => api<Artifact[]>(`/workflows/${id}/artifacts`),
  });

export const useExecutions = (workflowId: string) =>
  useQuery({
    queryKey: keys.executions(workflowId),
    queryFn: () => api<Execution[]>(withQuery("/executions", { workflow_id: workflowId, limit: 500 })),
    // The API lists newest first; a workflow's history reads oldest first.
    select: (rows) => [...rows].sort((a, b) => a.created_at.localeCompare(b.created_at)),
  });

export const useWorkflowToolCalls = (id: string) =>
  useQuery({
    queryKey: keys.toolCalls(id),
    queryFn: () => api<WorkflowToolCall[]>(`/workflows/${id}/tool-calls`),
  });

export const useWorkflowReviews = (id: string) =>
  useQuery({
    queryKey: keys.reviews(id),
    queryFn: () => api<WorkflowReview[]>(`/workflows/${id}/reviews`),
  });

export const useAgents = () =>
  useQuery({ queryKey: keys.agents, queryFn: () => api<Agent[]>("/agents"), staleTime: 60_000 });

export const useModels = () =>
  useQuery({ queryKey: keys.models, queryFn: () => api<Model[]>("/models"), staleTime: 60_000 });

export const useProviders = () =>
  useQuery({ queryKey: keys.providers, queryFn: () => api<Provider[]>("/providers") });

export const useTools = () =>
  useQuery({ queryKey: keys.tools, queryFn: () => api<Tool[]>("/tools"), staleTime: 60_000 });

export const useUsage = (days: number, projectId?: string) =>
  useQuery({
    queryKey: keys.usage(days, projectId),
    queryFn: () => api<Usage>(withQuery("/usage", { days, project_id: projectId })),
  });

export const useAuditLogs = (action?: string) =>
  useQuery({
    queryKey: keys.audit(action),
    queryFn: () => api<AuditLog[]>(withQuery("/audit-logs", { action, limit: 200 })),
  });

export const useApiKeys = () =>
  useQuery({ queryKey: keys.apiKeys, queryFn: () => api<ApiKey[]>("/api-keys") });

export const useFiles = (projectId?: string) =>
  useQuery({
    queryKey: keys.files(projectId),
    queryFn: () => api<ArtifactSummary[]>(withQuery("/artifacts", { project_id: projectId, limit: 500 })),
  });

export const useAttachments = (projectId: string | undefined) =>
  useQuery({
    queryKey: keys.attachments(projectId ?? ""),
    queryFn: () => api<Attachment[]>(`/projects/${projectId}/attachments`),
    enabled: Boolean(projectId),
  });

export const useReadiness = () =>
  useQuery({
    queryKey: keys.readiness,
    queryFn: () => api<Readiness>("/ready"),
    refetchInterval: 30_000,
    retry: false,
  });

export const useHealth = () =>
  useQuery({ queryKey: keys.health, queryFn: () => api<Health>("/health"), staleTime: 300_000 });
