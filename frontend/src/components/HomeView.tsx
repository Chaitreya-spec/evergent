"use client";

import { Database, History, Radio, Server } from "lucide-react";
import Link from "next/link";
import { TaskComposer } from "@/components/composer/TaskComposer";
import { WorkflowRow } from "@/components/workflows/WorkflowRow";
import { EmptyState, Notice, Panel, PanelHeader, Skeleton } from "@/components/ui/feedback";
import { useMe, useProjects, useReadiness, useWorkflows } from "@/lib/api/queries";
import { cn } from "@/lib/cn";

function Check({ ok, label, detail, icon: Icon }: { ok: boolean | undefined; label: string; detail: string; icon: typeof Server }) {
  return (
    <li className="flex items-center gap-3 px-4 py-2.5">
      <Icon className="size-4 text-subtle" />
      <div className="min-w-0 flex-1">
        <div className="text-sm text-fg">{label}</div>
        <div className="truncate text-xs text-subtle">{detail}</div>
      </div>
      <span className={cn("text-xs", ok === undefined ? "text-subtle" : ok ? "text-ok" : "text-warn")}>
        {ok === undefined ? "…" : ok ? "Ready" : "Attention"}
      </span>
    </li>
  );
}

export function HomeView({ autoFocus }: { autoFocus: boolean }) {
  const me = useMe();
  const recent = useWorkflows({ limit: 8 });
  const projects = useProjects();
  const ready = useReadiness();
  const names = new Map((projects.data ?? []).map((p) => [p.id, p.name]));
  const first = me.data?.full_name?.split(" ")[0];

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6 lg:py-12">
        <h1 className="text-xl font-semibold tracking-tight">{first ? `What are we building, ${first}?` : "What are we building?"}</h1>
        <p className="mt-1 text-sm text-muted">
          A planner breaks the request into tasks, a router picks the best model for each, specialised agents do the work,
          and every result is tested and reviewed before it is integrated.
        </p>

        <div className="mt-6">
          <TaskComposer autoFocus={autoFocus} />
        </div>

        <div className="mt-8 grid gap-6 lg:grid-cols-[1fr_18rem]">
          <Panel>
            <PanelHeader
              title="Recent workflows"
              action={
                <Link href="/workflows" className="text-xs text-subtle hover:text-fg">
                  View all
                </Link>
              }
            />
            {recent.isPending ? (
              <div className="space-y-3 p-4">
                <Skeleton className="h-5" />
                <Skeleton className="h-5" />
                <Skeleton className="h-5 w-2/3" />
              </div>
            ) : recent.isError ? (
              <div className="p-4">
                <Notice tone="bad">Could not load workflows: {recent.error.message}</Notice>
              </div>
            ) : recent.data.items.length === 0 ? (
              <EmptyState icon={History} title="No workflows yet">
                Describe a task above and it will appear here, with its plan, progress and cost.
              </EmptyState>
            ) : (
              <ul className="divide-y divide-line">
                {recent.data.items.map((w) => (
                  <WorkflowRow key={w.id} workflow={w} projectName={names.get(w.project_id)} />
                ))}
              </ul>
            )}
          </Panel>

          <Panel className="self-start">
            <PanelHeader title="System" description="Checked by the backend, live." />
            {ready.isError ? (
              <div className="p-4">
                <Notice tone="bad">The backend did not answer its readiness check.</Notice>
              </div>
            ) : (
              <ul className="divide-y divide-line">
                <Check icon={Database} label="Database" ok={ready.data?.database} detail="PostgreSQL" />
                <Check icon={Radio} label="Event bus" ok={ready.data?.redis} detail={ready.data && !ready.data.redis ? "Redis down — single-process fallback" : "Redis"} />
                <Check
                  icon={Server}
                  label="Model providers"
                  ok={ready.data ? ready.data.providers.length > 0 : undefined}
                  detail={ready.data ? (ready.data.providers.length ? ready.data.providers.join(", ") : "None configured") : "Checking"}
                />
              </ul>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}
