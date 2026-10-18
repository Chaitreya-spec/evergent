import type { Agent, Execution, Model, Provider, Task, Workflow } from "@/lib/api/types";

export function workflow(overrides: Partial<Workflow> = {}): Workflow {
  return {
    id: "wf-1",
    project_id: "p-1",
    conversation_id: null,
    team: null,
    goal: "Build a todo app with an API and tests",
    status: "running",
    routing_strategy: "balanced",
    brief: { objective: "A todo app", complexity: "medium" },
    final_result: null,
    error: null,
    total_cost_usd: 0.12,
    total_tokens: 4200,
    max_cost_usd: 5,
    requires_approval: false,
    approved_at: null,
    pinned_model: null,
    allow_fallback: true,
    created_at: "2026-10-14T10:00:00Z",
    started_at: "2026-10-14T10:00:01Z",
    completed_at: null,
    ...overrides,
  };
}

export function task(key: string, overrides: Partial<Task> = {}): Task {
  return {
    id: `id-${key}`,
    workflow_id: "wf-1",
    key,
    title: `Task ${key}`,
    description: `Do ${key}`,
    task_type: "coding",
    status: "pending",
    priority: 1,
    agent_name: "backend",
    routing_reason: null,
    output: null,
    error: null,
    retry_count: 0,
    input_tokens: 0,
    output_tokens: 0,
    cost_usd: 0,
    execution_time_ms: 0,
    started_at: null,
    completed_at: null,
    depends_on: [],
    owns: [],
    model_name: null,
    ...overrides,
  };
}

export const agents: Agent[] = [
  {
    name: "backend",
    display_name: "Backend Engineer",
    description: "APIs and services",
    instructions: "You build APIs.",
    capabilities: ["backend", "coding"],
    allowed_tools: ["list_files", "read_file", "write_file", "run_code"],
    handles: ["backend", "authentication"],
    requires_review: true,
    source: "builtin",
    is_enabled: true,
    assignable: true,
  },
  {
    name: "testing",
    display_name: "Test Engineer",
    description: "Writes and runs tests",
    instructions: "You test.",
    capabilities: ["coding", "debugging"],
    allowed_tools: ["list_files", "read_file", "write_file", "run_code"],
    handles: ["testing"],
    requires_review: true,
    source: "builtin",
    is_enabled: true,
    assignable: true,
  },
];

export const models: Model[] = [
  {
    id: "m-1",
    name: "claude-sonnet",
    display_name: "Claude Sonnet",
    provider_name: "anthropic",
    remote_id: "claude-sonnet-x",
    capabilities: { coding: 9.5, reasoning: 9.2 },
    context_window: 200000,
    max_output_tokens: 8192,
    cost_per_input_token: 3e-6,
    cost_per_output_token: 15e-6,
    avg_latency_ms: 4000,
    reliability: 0.99,
    is_enabled: true,
    listed_by_provider: null,
  },
];

export function provider(overrides: Partial<Provider> = {}): Provider {
  return {
    id: "pr-1",
    name: "anthropic",
    display_name: "Anthropic",
    base_url: null,
    is_enabled: true,
    configured: true,
    credential_source: "environment",
    api_key_preview: null,
    model_count: 1,
    verified_at: null,
    verification_ok: null,
    verification_error: null,
    verified_model_count: 0,
    ...overrides,
  };
}

export function execution(role: string, overrides: Partial<Execution> = {}): Execution {
  return {
    id: `ex-${role}-${Math.random()}`,
    workflow_id: "wf-1",
    task_id: null,
    provider_name: "anthropic",
    model_name: "claude-sonnet",
    role,
    input_tokens: 100,
    output_tokens: 50,
    estimated_cost: 0.001,
    latency_ms: 900,
    succeeded: true,
    error: null,
    created_at: "2026-10-14T10:00:02Z",
    ...overrides,
  };
}
