import Link from "next/link";
import { StatusBadge } from "@/components/ui/status";
import type { Workflow } from "@/lib/api/types";
import { elapsed, money, timeAgo, tokens } from "@/lib/format";

/** One workflow as a list row: what was asked, where it stands, what it cost. */
export function WorkflowRow({ workflow, projectName }: { workflow: Workflow; projectName?: string }) {
  return (
    <li>
      <Link
        href={`/workflows/${workflow.id}`}
        className="grid grid-cols-[1fr_auto] items-center gap-x-4 gap-y-1 px-4 py-3 transition-colors hover:bg-panel-2 sm:grid-cols-[1fr_8rem_5rem_5rem_6rem]"
      >
        <div className="min-w-0">
          <div className="truncate text-sm text-fg">{workflow.goal}</div>
          <div className="mt-0.5 truncate text-xs text-subtle">
            {projectName && <span>{projectName} · </span>}
            {workflow.routing_strategy.replace(/_/g, " ")}
            {workflow.pinned_model && ` · ${workflow.pinned_model}`}
          </div>
        </div>
        <div className="justify-self-end sm:justify-self-start">
          <StatusBadge status={workflow.status} />
        </div>
        <div className="hidden text-right text-xs tabular-nums text-muted sm:block" title="Estimated cost">
          {money(workflow.total_cost_usd)}
        </div>
        <div className="hidden text-right text-xs tabular-nums text-muted sm:block" title="Tokens">
          {tokens(workflow.total_tokens)}
        </div>
        <div className="hidden text-right text-xs text-subtle sm:block" title={workflow.started_at ? `Ran for ${elapsed(workflow.started_at, workflow.completed_at)}` : undefined}>
          {timeAgo(workflow.created_at)}
        </div>
      </Link>
    </li>
  );
}
