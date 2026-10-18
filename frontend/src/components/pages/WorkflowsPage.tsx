"use client";

import { History, Plus, Search } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useDeferredValue, useState } from "react";
import { Button } from "@/components/ui/button";
import { EmptyState, Notice, PageHeader, Skeleton } from "@/components/ui/feedback";
import { Input } from "@/components/ui/form";
import { Select } from "@/components/ui/select";
import { WorkflowRow } from "@/components/workflows/WorkflowRow";
import { useProjects, useWorkflows } from "@/lib/api/queries";
import type { WorkflowStatus } from "@/lib/api/types";
import { ACTIVE_WORKFLOW } from "@/lib/api/types";

const PAGE = 25;

const STATUS_GROUPS: Record<string, { label: string; statuses?: WorkflowStatus[] }> = {
  all: { label: "Any status" },
  active: { label: "In progress", statuses: [...ACTIVE_WORKFLOW] },
  awaiting_approval: { label: "Awaiting approval", statuses: ["awaiting_approval"] },
  completed: { label: "Completed", statuses: ["completed"] },
  failed: { label: "Failed", statuses: ["failed"] },
  cancelled: { label: "Cancelled", statuses: ["cancelled"] },
};

export function WorkflowsPage() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const project = params.get("project") ?? undefined;
  const group = params.get("status") ?? "all";
  const page = Math.max(0, Number(params.get("page") ?? 0) || 0);
  const [query, setQuery] = useState(params.get("q") ?? "");
  const q = useDeferredValue(query.trim());

  const projects = useProjects();
  const list = useWorkflows({
    project_id: project,
    status: STATUS_GROUPS[group]?.statuses,
    q: q || undefined,
    limit: PAGE,
    offset: page * PAGE,
  });
  const names = new Map((projects.data ?? []).map((p) => [p.id, p.name]));

  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key !== "page") next.delete("page");
    router.replace(`${pathname}${next.size ? `?${next}` : ""}`);
  };

  const total = list.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE));

  return (
    <div className="h-full overflow-y-auto">
      <PageHeader
        title="Execution history"
        description="Every workflow, newest first, with its status and estimated cost."
        actions={
          <Button asChild variant="primary">
            <Link href="/">
              <Plus /> New task
            </Link>
          </Button>
        }
      />
      <div className="space-y-4 px-4 py-4 sm:px-6">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative w-full sm:w-72">
            <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-subtle" />
            <Input
              aria-label="Search workflows"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setParam("q", e.target.value.trim() || null);
              }}
              placeholder="Search requests"
              className="pl-8"
            />
          </div>
          <div className="w-44">
            <Select aria-label="Status" value={group} onValueChange={(v) => setParam("status", v === "all" ? null : v)} options={Object.entries(STATUS_GROUPS).map(([value, g]) => ({ value, label: g.label }))} />
          </div>
          <div className="w-52">
            <Select
              aria-label="Project"
              value={project ?? "__all"}
              onValueChange={(v) => setParam("project", v === "__all" ? null : v)}
              options={[{ value: "__all", label: "All projects" }, ...(projects.data ?? []).map((p) => ({ value: p.id, label: p.name }))]}
            />
          </div>
        </div>

        <div className="overflow-hidden rounded-lg border border-line bg-panel">
          <div className="hidden grid-cols-[1fr_8rem_5rem_5rem_6rem] gap-x-4 border-b border-line px-4 py-2 text-[11px] uppercase tracking-wide text-subtle sm:grid">
            <span>Request</span>
            <span>Status</span>
            <span className="text-right">Cost (est.)</span>
            <span className="text-right">Tokens</span>
            <span className="text-right">Created</span>
          </div>
          {list.isPending ? (
            <div className="space-y-3 p-4">
              {[0, 1, 2, 3].map((i) => (
                <Skeleton key={i} className="h-5" />
              ))}
            </div>
          ) : list.isError ? (
            <div className="p-4">
              <Notice tone="bad">{list.error.message}</Notice>
            </div>
          ) : list.data.items.length === 0 ? (
            <EmptyState icon={History} title={q || project || group !== "all" ? "Nothing matches" : "No workflows yet"}>
              {q || project || group !== "all" ? "Try another filter." : "Start one from New task."}
            </EmptyState>
          ) : (
            <ul className="divide-y divide-line">
              {list.data.items.map((w) => (
                <WorkflowRow key={w.id} workflow={w} projectName={names.get(w.project_id)} />
              ))}
            </ul>
          )}
        </div>

        {total > PAGE && (
          <nav aria-label="Pages" className="flex items-center justify-between text-xs text-muted">
            <span>
              {page * PAGE + 1}–{Math.min(total, (page + 1) * PAGE)} of {total}
            </span>
            <div className="flex gap-2">
              <Button size="sm" disabled={page === 0} onClick={() => setParam("page", String(page - 1))}>
                Previous
              </Button>
              <Button size="sm" disabled={page + 1 >= pages} onClick={() => setParam("page", String(page + 1))}>
                Next
              </Button>
            </div>
          </nav>
        )}
      </div>
    </div>
  );
}
