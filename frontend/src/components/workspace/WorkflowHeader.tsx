"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Ban, ChevronLeft, Download, Pause, Play, RefreshCw } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Notice, Spinner } from "@/components/ui/feedback";
import { StatusBadge } from "@/components/ui/status";
import { Tooltip } from "@/components/ui/tooltip";
import { api, errorText } from "@/lib/api/client";
import { keys } from "@/lib/api/queries";
import type { Task, Workflow } from "@/lib/api/types";
import { TERMINAL_WORKFLOW } from "@/lib/api/types";
import { elapsed, money, tokens } from "@/lib/format";

type Action = "run" | "pause" | "resume" | "cancel" | "approve" | "plan";

const RUNNING = new Set(["planning", "running", "validating", "synthesizing"]);

export function useWorkflowAction(workflow: Workflow, onStarted?: () => void) {
  const queries = useQueryClient();
  return useMutation({
    mutationFn: (action: Action) => api<Workflow>(`/workflows/${workflow.id}/${action}`, { method: "POST" }),
    onSuccess: (updated, action) => {
      queries.setQueryData(keys.workflow(workflow.id), updated);
      void queries.invalidateQueries({ queryKey: keys.tasks(workflow.id) });
      void queries.invalidateQueries({ queryKey: keys.allWorkflows });
      if (action !== "pause" && action !== "cancel") onStarted?.();
      const said: Record<Action, string> = {
        run: "Started",
        resume: "Resumed",
        pause: "Pausing — running tasks stop at their next checkpoint",
        cancel: "Cancelled",
        approve: "Plan approved — work is starting",
        plan: "Re-planning",
      };
      toast.success(said[action]);
    },
    onError: (e) => toast.error(errorText(e)),
  });
}

export function WorkflowHeader({ workflow, tasks, projectName, onStarted }: { workflow: Workflow; tasks: Task[]; projectName?: string; onStarted: () => void }) {
  const action = useWorkflowAction(workflow, onStarted);
  const busy = action.isPending;
  const status = workflow.status;
  const done = tasks.filter((t) => t.status === "completed").length;
  const finished = TERMINAL_WORKFLOW.has(status);

  return (
    <section aria-label="Workflow summary" className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line bg-panel px-3 py-2.5 sm:px-4">
      <Button asChild variant="ghost" size="icon-sm" aria-label="All workflows">
        <Link href="/workflows">
          <ChevronLeft />
        </Link>
      </Button>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <h1 className="truncate text-sm font-semibold text-fg" title={workflow.goal}>
            {workflow.goal}
          </h1>
          <StatusBadge status={status} />
        </div>
        <div className="mt-0.5 flex flex-wrap gap-x-3 text-[11px] text-subtle tabular-nums">
          {projectName && <span>{projectName}</span>}
          <span>
            {done}/{tasks.length} tasks
          </span>
          <span title="Estimated from token counts and catalogue prices">
            {money(workflow.total_cost_usd)} of {money(workflow.max_cost_usd)} est.
          </span>
          <span>{tokens(workflow.total_tokens)} tokens</span>
          {workflow.started_at && <span>{elapsed(workflow.started_at, workflow.completed_at)}</span>}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        {busy && <Spinner />}
        {status === "pending" && (
          <Button size="sm" variant="primary" onClick={() => action.mutate("run")} disabled={busy}>
            <Play /> Run
          </Button>
        )}
        {RUNNING.has(status) && (
          <Button size="sm" onClick={() => action.mutate("pause")} disabled={busy}>
            <Pause /> Pause
          </Button>
        )}
        {status === "paused" && (
          <Button size="sm" variant="primary" onClick={() => action.mutate("resume")} disabled={busy}>
            <Play /> Resume
          </Button>
        )}
        {(status === "failed" || status === "cancelled") && (
          <Tooltip content="Runs every task that has not finished. Retry failed tasks from the inspector first.">
            <Button size="sm" onClick={() => action.mutate("run")} disabled={busy}>
              <RefreshCw /> Run remaining
            </Button>
          </Tooltip>
        )}
        {!finished && status !== "awaiting_approval" && (
          <Button size="sm" variant="danger" onClick={() => action.mutate("cancel")} disabled={busy}>
            <Ban /> Cancel
          </Button>
        )}
        <Button asChild size="sm" variant="ghost">
          <a href={`/api/v1/workflows/${workflow.id}/archive`} download aria-label="Download all files as a zip">
            <Download />
            <span className="hidden sm:inline">Files</span>
          </a>
        </Button>
      </div>
    </section>
  );
}

export function ApprovalBanner({ workflow, tasks, onStarted }: { workflow: Workflow; tasks: Task[]; onStarted: () => void }) {
  const action = useWorkflowAction(workflow, onStarted);
  if (workflow.status !== "awaiting_approval") return null;
  const agents = new Set(tasks.map((t) => t.agent_name).filter(Boolean)).size;
  return (
    <div className="border-b border-line bg-panel px-3 py-2.5 sm:px-4">
      <Notice
        tone="warn"
        title={`The plan is ready: ${tasks.length} task${tasks.length === 1 ? "" : "s"} for ${agents} engineer${agents === 1 ? "" : "s"}`}
        action={
          <div className="flex flex-wrap gap-1.5">
            <Button size="sm" variant="approve" onClick={() => action.mutate("approve")} disabled={action.isPending}>
              <Play /> Approve and run
            </Button>
            <Button size="sm" onClick={() => action.mutate("plan")} disabled={action.isPending}>
              <RefreshCw /> Re-plan
            </Button>
            <Button size="sm" variant="danger" onClick={() => action.mutate("cancel")} disabled={action.isPending}>
              Cancel
            </Button>
          </div>
        }
      >
        Nothing has run yet. Select tasks to review them, edit instructions, reassign engineers or pin models, then approve.
      </Notice>
    </div>
  );
}
