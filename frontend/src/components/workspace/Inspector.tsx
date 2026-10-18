"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Ban, FileCode2, MousePointerClick, Pencil, RotateCcw, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { EmptyState, Notice } from "@/components/ui/feedback";
import { StatusBadge, StatusDot } from "@/components/ui/status";
import { api, errorText } from "@/lib/api/client";
import { keys, useAttachments } from "@/lib/api/queries";
import type {
  Agent,
  Artifact,
  Execution,
  Model,
  Task,
  Workflow,
  WorkflowReview,
  WorkflowToolCall,
} from "@/lib/api/types";
import { TERMINAL_WORKFLOW } from "@/lib/api/types";
import { bytes, clock, duration, humanize, money, tokens } from "@/lib/format";
import type { GraphNode, StageNodeData, TaskNodeData } from "@/lib/workflow/graph";
import { taskNodeId } from "@/lib/workflow/graph";
import { Chips, CodeBlock, Disclosure, Facts, json, Section } from "./bits";
import { TaskEditDialog } from "./TaskEditDialog";

export type InspectorData = {
  workflow: Workflow;
  tasks: Task[];
  agents: Agent[];
  models: Model[];
  executions: Execution[];
  reviews: WorkflowReview[];
  toolCalls: WorkflowToolCall[];
  artifacts: Artifact[];
};

type Props = InspectorData & {
  node: GraphNode | null;
  onSelect: (id: string | null) => void;
  onOpenFile: (artifactId: string) => void;
};

function ExecutionList({ executions, empty }: { executions: Execution[]; empty: string }) {
  if (executions.length === 0) return <p className="text-xs text-subtle">{empty}</p>;
  return (
    <ul className="space-y-1.5">
      {executions.map((e, i) => (
        <li key={e.id} className="rounded-md border border-line bg-panel-2/40 px-2.5 py-2 text-xs">
          <div className="flex items-center justify-between gap-2">
            <span className="truncate font-mono text-fg">
              {i + 1}. {e.model_name}
            </span>
            <StatusBadge status={e.succeeded ? "completed" : "failed"} label={e.succeeded ? "OK" : "Failed"} />
          </div>
          <div className="mt-1 text-subtle tabular-nums">
            {e.provider_name} · {tokens(e.input_tokens)} in / {tokens(e.output_tokens)} out · {money(e.estimated_cost)} · {duration(e.latency_ms)}
          </div>
          {e.error && <div className="mt-1 break-words text-bad">{e.error}</div>}
        </li>
      ))}
    </ul>
  );
}

function ReviewList({ reviews }: { reviews: WorkflowReview[] }) {
  if (reviews.length === 0) return <p className="text-xs text-subtle">No reviews yet.</p>;
  return (
    <ul className="space-y-1.5">
      {reviews.map((r) => (
        <li key={r.id}>
          <Disclosure
            summary={
              <div className="flex items-center justify-between gap-2">
                <span className="truncate">
                  <span className="font-mono text-subtle">{r.task_key}</span> {r.summary ?? "Review"}
                </span>
                <StatusBadge status={r.status === "valid" ? "completed" : r.status === "invalid" ? "failed" : "retrying"} label={`${humanize(r.status)} · ${r.score.toFixed(1)}`} />
              </div>
            }
          >
            {r.feedback.length === 0 ? (
              <p className="text-xs text-subtle">No findings.</p>
            ) : (
              <ul className="space-y-1 text-xs">
                {r.feedback.map((f, i) => {
                  const item = f as { severity?: string; file?: string; message?: string };
                  return (
                    <li key={i} className="flex gap-2">
                      <span className={item.severity === "high" || item.severity === "critical" ? "text-bad" : item.severity === "medium" ? "text-warn" : "text-subtle"}>
                        {item.severity ?? "note"}
                      </span>
                      <span className="min-w-0 break-words text-muted">
                        {item.file && <span className="font-mono text-fg">{item.file}: </span>}
                        {item.message}
                      </span>
                    </li>
                  );
                })}
              </ul>
            )}
          </Disclosure>
        </li>
      ))}
    </ul>
  );
}

function ToolCallList({ calls }: { calls: WorkflowToolCall[] }) {
  if (calls.length === 0) return <p className="text-xs text-subtle">No tools used.</p>;
  return (
    <ul className="space-y-1.5">
      {calls.map((c) => (
        <li key={c.id}>
          <Disclosure
            summary={
              <div className="flex items-center justify-between gap-2">
                <span className="flex min-w-0 items-center gap-2">
                  <StatusDot status={c.succeeded ? "completed" : "failed"} />
                  <span className="truncate font-mono text-fg">{c.tool_name}</span>
                </span>
                <span className="shrink-0 text-subtle tabular-nums">
                  {clock(c.created_at)} · {duration(c.duration_ms)}
                </span>
              </div>
            }
          >
            {c.error && <p className="mb-2 break-words text-xs text-bad">{c.error}</p>}
            <div className="space-y-2">
              <div>
                <div className="mb-1 text-[10px] uppercase tracking-wide text-subtle">Arguments</div>
                <CodeBlock text={json(c.arguments)} maxHeight="max-h-48" />
              </div>
              {c.result && (
                <div>
                  <div className="mb-1 text-[10px] uppercase tracking-wide text-subtle">Result</div>
                  <CodeBlock text={typeof (c.result as { output?: unknown }).output === "string" ? String((c.result as { output: string }).output) : json(c.result)} maxHeight="max-h-64" />
                </div>
              )}
            </div>
          </Disclosure>
        </li>
      ))}
    </ul>
  );
}

function TaskInspector({ data, all, onSelect, onOpenFile }: { data: TaskNodeData; all: InspectorData; onSelect: Props["onSelect"]; onOpenFile: Props["onOpenFile"] }) {
  const queries = useQueryClient();
  const { task } = data;
  const [editing, setEditing] = useState(false);
  const agent = all.agents.find((a) => a.name === task.agent_name);
  const byKey = new Map(all.tasks.map((t) => [t.key, t]));
  const executions = all.executions.filter((e) => e.task_id === task.id);
  const reviews = all.reviews.filter((r) => r.task_id === task.id);
  const calls = all.toolCalls.filter((c) => c.task_id === task.id);
  const outputs = all.artifacts.filter((a) => a.task_id === task.id);
  const editable = ["pending", "queued"].includes(task.status) && ["awaiting_approval", "pending", "paused", "failed", "cancelled"].includes(all.workflow.status);
  const retryable = ["failed", "cancelled", "completed", "skipped"].includes(task.status);
  const cancellable = !["completed", "failed", "cancelled", "skipped"].includes(task.status) && !TERMINAL_WORKFLOW.has(all.workflow.status);

  const refresh = () => {
    void queries.invalidateQueries({ queryKey: keys.tasks(all.workflow.id) });
    void queries.invalidateQueries({ queryKey: keys.workflow(all.workflow.id) });
  };
  const act = useMutation({
    mutationFn: (action: "retry" | "cancel") => api<Task>(`/tasks/${task.id}/${action}`, { method: "POST" }),
    onSuccess: (_, action) => {
      refresh();
      if (action === "retry") {
        const idle = TERMINAL_WORKFLOW.has(all.workflow.status) || all.workflow.status === "paused";
        toast.success(`${task.key} is back in the queue`, {
          description: idle ? "Run the workflow to execute it." : "It runs when the workflow reaches it.",
        });
      } else toast.success(`${task.key} cancelled`);
    },
    onError: (e) => toast.error(errorText(e)),
  });

  return (
    <>
      <Section title="Task" className="pt-0">
        <h2 className="text-sm font-semibold leading-snug text-fg">{task.title}</h2>
        <p className="mt-2 whitespace-pre-wrap text-xs leading-relaxed text-muted">{task.description}</p>
        <div className="mt-3 flex flex-wrap gap-2">
          {editable && (
            <Button size="sm" onClick={() => setEditing(true)}>
              <Pencil /> Edit
            </Button>
          )}
          {retryable && (
            <Button size="sm" onClick={() => act.mutate("retry")} disabled={act.isPending}>
              <RotateCcw /> Retry
            </Button>
          )}
          {cancellable && (
            <Button size="sm" variant="danger" onClick={() => act.mutate("cancel")} disabled={act.isPending}>
              <Ban /> Cancel task
            </Button>
          )}
        </div>
      </Section>

      {task.error && (
        <Section title="Error">
          <Notice tone={task.status === "failed" ? "bad" : "warn"}>{task.error}</Notice>
        </Section>
      )}

      <Section title="Assignment">
        <Facts
          rows={[
            ["Engineer", agent ? agent.display_name : (task.agent_name ?? "Unassigned")],
            ["Type", humanize(task.task_type)],
            ["Model", data.model ? <span className="font-mono">{data.model.displayName}</span> : "Not routed yet"],
            ["Provider", data.model?.provider ?? "—"],
            ["Priority", task.priority],
          ]}
        />
        {task.routing_reason && <p className="mt-2.5 rounded-md bg-panel-2 px-2.5 py-2 text-xs leading-relaxed text-muted">{task.routing_reason}</p>}
      </Section>

      {agent && (
        <Section title="Capabilities and tools">
          <Chips items={agent.capabilities} />
          <div className="mt-2">
            <Chips items={agent.allowed_tools} empty="No tools" />
          </div>
          <div className="mt-2.5">
            <Disclosure summary={<span className="text-muted">Agent instructions</span>}>
              <p className="whitespace-pre-wrap text-xs leading-relaxed text-muted">{agent.instructions}</p>
            </Disclosure>
          </div>
        </Section>
      )}

      <Section title="Inputs">
        {(task.depends_on ?? []).length === 0 ? (
          <p className="text-xs text-subtle">No dependencies — starts in the first wave.</p>
        ) : (
          <ul className="space-y-1">
            {(task.depends_on ?? []).map((key) => {
              const dep = byKey.get(key);
              return (
                <li key={key}>
                  <button type="button" onClick={() => onSelect(taskNodeId(key))} className="flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-xs hover:bg-panel-3">
                    <StatusDot status={dep?.status ?? "pending"} />
                    <span className="font-mono text-subtle">{key}</span>
                    <span className="truncate text-muted">{dep?.title ?? "Unknown task"}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
        {(task.owns ?? []).length > 0 && (
          <div className="mt-2.5">
            <div className="mb-1 text-[11px] text-subtle">Files it owns</div>
            <Chips items={task.owns ?? []} />
          </div>
        )}
      </Section>

      <Section title="Outputs">
        {outputs.length > 0 && (
          <ul className="mb-2 space-y-0.5">
            {outputs.map((a) => (
              <li key={a.id}>
                <button type="button" onClick={() => onOpenFile(a.id)} className="flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-xs hover:bg-panel-3">
                  <FileCode2 className="size-3.5 text-subtle" />
                  <span className="truncate font-mono text-fg">{a.path}</span>
                  <span className="ml-auto text-subtle">v{a.version} · {bytes(a.content.length)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {task.output ? (
          <CodeBlock text={task.output} maxHeight="max-h-60" />
        ) : outputs.length === 0 ? (
          <p className="text-xs text-subtle">Nothing produced yet.</p>
        ) : null}
      </Section>

      <Section title="Usage">
        <Facts
          rows={[
            ["Tokens", `${tokens(task.input_tokens)} in · ${tokens(task.output_tokens)} out`],
            ["Cost", <span key="c">{money(task.cost_usd)} <span className="text-subtle">estimated</span></span>],
            ["Duration", duration(task.execution_time_ms)],
            ["Retries", task.retry_count],
            ["Started", task.started_at ? clock(task.started_at) : "—"],
            ["Finished", task.completed_at ? clock(task.completed_at) : "—"],
          ]}
        />
      </Section>

      <Section title={`Reviews (${reviews.length})`}>
        <ReviewList reviews={reviews} />
      </Section>
      <Section title={`Tool calls (${calls.length})`}>
        <ToolCallList calls={calls} />
      </Section>
      <Section title={`Model calls and retries (${executions.length})`} className="border-b-0">
        <ExecutionList executions={executions} empty="No model calls yet." />
      </Section>

      {editing && (
        <TaskEditDialog task={task} agents={all.agents} models={all.models} open={editing} onOpenChange={setEditing} onSaved={refresh} />
      )}
    </>
  );
}

function BriefList({ label, items }: { label: string; items: unknown }) {
  const list = Array.isArray(items) ? items.map(String) : [];
  if (list.length === 0) return null;
  return (
    <div className="mt-2.5">
      <div className="mb-1 text-[11px] text-subtle">{label}</div>
      <ul className="list-disc space-y-0.5 pl-4 text-xs text-muted">
        {list.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function StageInspector({ data, all, onSelect }: { data: StageNodeData; all: InspectorData; onSelect: Props["onSelect"] }) {
  const wf = all.workflow;
  const attachments = useAttachments(data.stage === "request" ? wf.project_id : undefined);
  const brief = (wf.brief ?? {}) as Record<string, unknown>;
  const roles: Record<string, string[]> = { planner: ["commander"], decomposition: ["planner"], integration: ["synthesizer"], review: ["reviewer"] };
  const executions = all.executions.filter((e) => roles[data.stage]?.includes(e.role));
  const tests = all.toolCalls.filter((c) => c.tool_name === "run_tests");

  return (
    <>
      <Section title={data.title} className="pt-0">
        <p className="whitespace-pre-wrap text-xs leading-relaxed text-muted">{data.subtitle}</p>
        <div className="mt-3">
          <Facts rows={data.metrics.map((m) => [m.label, <span key={m.label} className="capitalize">{m.value}</span>])} />
        </div>
      </Section>

      {data.stage === "request" && (
        <Section title="Request settings">
          <Facts
            rows={[
              ["Strategy", humanize(wf.routing_strategy)],
              ["Pinned model", wf.pinned_model ?? "None"],
              ["Fallback", wf.pinned_model ? (wf.allow_fallback ? "Allowed" : "Off") : "—"],
              ["Plan approval", wf.requires_approval ? (wf.approved_at ? `Approved ${clock(wf.approved_at)}` : "Required") : "Not required"],
              ["Team", wf.team?.length ? wf.team.join(", ") : "Planner chooses"],
              ["Budget", money(wf.max_cost_usd)],
            ]}
          />
          {(attachments.data ?? []).filter((a) => a.workflow_id === wf.id).length > 0 && (
            <div className="mt-2.5">
              <div className="mb-1 text-[11px] text-subtle">Attached files</div>
              <Chips items={(attachments.data ?? []).filter((a) => a.workflow_id === wf.id).map((a) => a.filename)} />
            </div>
          )}
        </Section>
      )}

      {data.stage === "planner" && (
        <Section title="Brief">
          {wf.brief ? (
            <>
              <Facts
                rows={[
                  ["Objective", String(brief.objective ?? "—")],
                  ["Deliverable", humanize(String(brief.deliverable_type ?? "—"))],
                  ["Complexity", humanize(String(brief.complexity ?? "—"))],
                ]}
              />
              <BriefList label="Requirements" items={brief.requirements} />
              <BriefList label="Constraints" items={brief.constraints} />
              <BriefList label="Stack" items={brief.stack} />
            </>
          ) : (
            <p className="text-xs text-subtle">The brief appears once the planner has read the request.</p>
          )}
        </Section>
      )}

      {(data.stage === "decomposition" || data.stage === "router") && (
        <Section title={data.stage === "router" ? "Routing decisions" : "Tasks"}>
          {all.tasks.length === 0 ? (
            <p className="text-xs text-subtle">No tasks yet.</p>
          ) : (
            <ul className="space-y-1.5">
              {[...all.tasks].sort((a, b) => a.key.localeCompare(b.key, undefined, { numeric: true })).map((t) => (
                <li key={t.id}>
                  <button type="button" onClick={() => onSelect(taskNodeId(t.key))} className="w-full rounded-md border border-line bg-panel-2/40 px-2.5 py-2 text-left text-xs hover:border-line-strong">
                    <div className="flex items-center gap-2">
                      <StatusDot status={t.status} />
                      <span className="font-mono text-subtle">{t.key}</span>
                      <span className="truncate text-fg">{t.title}</span>
                    </div>
                    {data.stage === "router" ? (
                      <div className="mt-1 text-muted">
                        <span className="font-mono text-fg">{t.model_name ?? "not routed"}</span>
                        {t.routing_reason && <span> — {t.routing_reason}</span>}
                      </div>
                    ) : (
                      <div className="mt-1 text-subtle">
                        {all.agents.find((a) => a.name === t.agent_name)?.display_name ?? t.agent_name}
                        {(t.depends_on ?? []).length > 0 && ` · after ${(t.depends_on ?? []).join(", ")}`}
                      </div>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Section>
      )}

      {data.stage === "review" && (
        <>
          <Section title={`Reviews (${all.reviews.length})`}>
            <ReviewList reviews={all.reviews} />
          </Section>
          <Section title={`Test runs (${tests.length})`}>
            <ToolCallList calls={tests} />
          </Section>
        </>
      )}

      {data.stage === "integration" && (
        <Section title="Final result">
          {wf.final_result ? (
            <CodeBlock text={wf.final_result} maxHeight="max-h-[28rem]" />
          ) : wf.error ? (
            <Notice tone="bad">{wf.error}</Notice>
          ) : (
            <p className="text-xs text-subtle">Written once every task has finished and passed review.</p>
          )}
        </Section>
      )}

      {data.stage !== "request" && data.stage !== "router" && (
        <Section title={`Model calls (${executions.length})`} className="border-b-0">
          <ExecutionList executions={executions} empty="No model calls for this stage yet." />
        </Section>
      )}
    </>
  );
}

export function Inspector({ node, onSelect, onOpenFile, ...all }: Props) {
  if (!node) {
    return (
      <EmptyState icon={MousePointerClick} title="Nothing selected" className="h-full">
        Select a node on the canvas to see its instructions, model, inputs, outputs, cost and history. Use <kbd className="font-mono">j</kbd>/<kbd className="font-mono">k</kbd> to step through.
      </EmptyState>
    );
  }
  const data = node.data;
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-2 px-4 pb-2 pt-3">
        <div className="flex min-w-0 items-center gap-2">
          {data.kind === "task" && <span className="font-mono text-xs text-subtle">{data.task.key}</span>}
          <span className="truncate text-xs font-medium text-muted">{data.kind === "task" ? data.agentLabel : "Pipeline stage"}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <StatusBadge status={data.status} label={data.kind === "stage" ? data.statusLabel : undefined} />
          <Button size="icon-sm" variant="ghost" aria-label="Close inspector" onClick={() => onSelect(null)}>
            <X />
          </Button>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {data.kind === "task" ? (
          <TaskInspector key={data.task.id} data={data} all={all} onSelect={onSelect} onOpenFile={onOpenFile} />
        ) : (
          <StageInspector data={data} all={all} onSelect={onSelect} />
        )}
      </div>
    </div>
  );
}
