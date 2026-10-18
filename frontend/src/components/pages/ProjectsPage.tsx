"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FolderKanban, MoreHorizontal, Pencil, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { EmptyState, Notice, PageHeader, Skeleton } from "@/components/ui/feedback";
import { Field, Input, Textarea } from "@/components/ui/form";
import { Menu, MenuContent, MenuItem, MenuTrigger } from "@/components/ui/menu";
import { api, errorText } from "@/lib/api/client";
import { keys, useProjects } from "@/lib/api/queries";
import type { Project } from "@/lib/api/types";
import { money, timeAgo, tokens } from "@/lib/format";

function ProjectForm({ project, onDone }: { project?: Project; onDone: () => void }) {
  const queries = useQueryClient();
  const save = useMutation({
    mutationFn: (form: FormData) => {
      const cost = String(form.get("max_cost_usd") ?? "").trim();
      const tok = String(form.get("max_tokens") ?? "").trim();
      const body = {
        name: String(form.get("name") ?? "").trim(),
        description: String(form.get("description") ?? "").trim() || null,
        max_cost_usd: cost ? Number(cost) : null,
        max_tokens: tok ? Number(tok) : null,
      };
      return project
        ? api<Project>(`/projects/${project.id}`, { method: "PATCH", json: body })
        : api<Project>("/projects", { method: "POST", json: body });
    },
    onSuccess: () => {
      void queries.invalidateQueries({ queryKey: keys.projects });
      toast.success(project ? "Project saved" : "Project created");
      onDone();
    },
  });
  return (
    <form action={(form) => save.mutate(form)} className="space-y-4">
      <Field label="Name">{(p) => <Input {...p} name="name" required maxLength={200} defaultValue={project?.name} />}</Field>
      <Field label="Description" hint="Optional. The planner does not see this.">
        {(p) => <Textarea {...p} name="description" rows={3} defaultValue={project?.description ?? ""} />}
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Default budget per run (USD)" hint="Empty: the server default.">
          {(p) => <Input {...p} name="max_cost_usd" type="number" step="0.01" min="0.01" max="1000" defaultValue={project?.max_cost_usd ?? ""} />}
        </Field>
        <Field label="Token ceiling per run" hint="Empty: no ceiling.">
          {(p) => <Input {...p} name="max_tokens" type="number" min="1" step="1" defaultValue={project?.max_tokens ?? ""} />}
        </Field>
      </div>
      {save.isError && <Notice tone="bad">{errorText(save.error)}</Notice>}
      <div className="flex justify-end gap-2">
        <Button onClick={onDone}>Cancel</Button>
        <Button type="submit" variant="primary" disabled={save.isPending}>
          {project ? "Save" : "Create project"}
        </Button>
      </div>
    </form>
  );
}

export function ProjectsPage() {
  const projects = useProjects();
  const queries = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Project | null>(null);
  const [deleting, setDeleting] = useState<Project | null>(null);

  const remove = useMutation({
    mutationFn: (project: Project) => api(`/projects/${project.id}`, { method: "DELETE" }),
    onSuccess: () => {
      void queries.invalidateQueries({ queryKey: keys.projects });
      void queries.invalidateQueries({ queryKey: keys.allWorkflows });
      toast.success("Project deleted");
      setDeleting(null);
    },
    onError: (e) => toast.error(errorText(e)),
  });

  return (
    <div className="h-full overflow-y-auto">
      <PageHeader
        title="Projects"
        description="Workflows, files and memory are grouped by project."
        actions={
          <Button variant="primary" onClick={() => setCreating(true)}>
            <Plus /> New project
          </Button>
        }
      />
      <div className="px-4 py-4 sm:px-6">
        {projects.isPending ? (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-28" />
            ))}
          </div>
        ) : projects.isError ? (
          <Notice tone="bad">{projects.error.message}</Notice>
        ) : projects.data.length === 0 ? (
          <EmptyState icon={FolderKanban} title="No projects yet" action={<Button variant="primary" onClick={() => setCreating(true)}><Plus /> New project</Button>}>
            A project holds related workflows, their files, and what the agents learn along the way.
          </EmptyState>
        ) : (
          <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {projects.data.map((p) => (
              <li key={p.id} className="flex flex-col rounded-lg border border-line bg-panel p-4">
                <div className="flex items-start justify-between gap-2">
                  <Link href={`/workflows?project=${p.id}`} className="min-w-0 text-sm font-semibold text-fg hover:underline">
                    <span className="block truncate">{p.name}</span>
                  </Link>
                  <Menu>
                    <MenuTrigger asChild>
                      <Button size="icon-sm" variant="ghost" aria-label={`Actions for ${p.name}`}>
                        <MoreHorizontal />
                      </Button>
                    </MenuTrigger>
                    <MenuContent align="end">
                      <MenuItem onSelect={() => setEditing(p)}>
                        <Pencil /> Edit
                      </MenuItem>
                      <MenuItem onSelect={() => setDeleting(p)} className="text-bad">
                        <Trash2 /> Delete
                      </MenuItem>
                    </MenuContent>
                  </Menu>
                </div>
                <p className="mt-1 line-clamp-2 min-h-8 text-xs text-muted">{p.description || "No description."}</p>
                <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-subtle tabular-nums">
                  <span>Budget {p.max_cost_usd ? money(p.max_cost_usd) : "default"}</span>
                  <span>Tokens {p.max_tokens ? tokens(p.max_tokens) : "no ceiling"}</span>
                  <span>Created {timeAgo(p.created_at)}</span>
                </div>
                <Link href={`/workflows?project=${p.id}`} className="mt-3 text-xs text-accent hover:underline">
                  View workflows →
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>

      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent title="New project">{creating && <ProjectForm onDone={() => setCreating(false)} />}</DialogContent>
      </Dialog>
      <Dialog open={editing !== null} onOpenChange={(o) => !o && setEditing(null)}>
        <DialogContent title="Edit project">{editing && <ProjectForm project={editing} onDone={() => setEditing(null)} />}</DialogContent>
      </Dialog>
      <Confirm
        open={deleting !== null}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Delete ${deleting?.name ?? "project"}?`}
        confirmLabel="Delete project"
        busy={remove.isPending}
        onConfirm={() => deleting && remove.mutate(deleting)}
      >
        Its workflows, generated files, attachments and memories are deleted with it. This cannot be undone.
      </Confirm>
    </div>
  );
}
