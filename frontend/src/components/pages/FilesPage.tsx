"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FileCode2, FileText, Search, Trash2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { EmptyState, Notice, PageHeader, Skeleton, Spinner } from "@/components/ui/feedback";
import { Input } from "@/components/ui/form";
import { Select } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { CodeBlock } from "@/components/workspace/bits";
import { api, errorText } from "@/lib/api/client";
import { keys, useAttachments, useFiles, useProjects } from "@/lib/api/queries";
import type { Artifact, ArtifactSummary, Attachment } from "@/lib/api/types";
import { bytes, timeAgo } from "@/lib/format";

function ArtifactViewer({ summary }: { summary: ArtifactSummary }) {
  const file = useQuery({ queryKey: ["artifact", summary.id], queryFn: () => api<Artifact>(`/artifacts/${summary.id}`) });
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
        <span>
          v{summary.version} · {bytes(summary.size)} · from{" "}
          <Link href={`/workflows/${summary.workflow_id}`} className="text-fg underline">
            {summary.workflow_goal}
          </Link>
        </span>
        <Button asChild size="sm" className="ml-auto">
          <a href={`/api/v1/artifacts/${summary.id}/download`} download>
            <Download /> Download
          </a>
        </Button>
      </div>
      {file.isPending ? <Spinner /> : file.isError ? <Notice tone="bad">{file.error.message}</Notice> : <CodeBlock text={file.data.content} maxHeight="max-h-[60vh]" />}
    </div>
  );
}

function Generated({ projectId }: { projectId?: string }) {
  const files = useFiles(projectId);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<ArtifactSummary | null>(null);
  const q = query.trim().toLowerCase();
  const visible = (files.data ?? []).filter((f) => !q || f.path.toLowerCase().includes(q) || f.workflow_goal.toLowerCase().includes(q));

  return (
    <>
      <div className="relative mb-3 w-full sm:w-72">
        <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-subtle" />
        <Input aria-label="Search files" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search by path or request" className="pl-8" />
      </div>
      <div className="overflow-hidden rounded-lg border border-line bg-panel">
        {files.isPending ? (
          <div className="space-y-2 p-4">
            <Skeleton className="h-5" />
            <Skeleton className="h-5" />
          </div>
        ) : files.isError ? (
          <div className="p-4">
            <Notice tone="bad">{files.error.message}</Notice>
          </div>
        ) : visible.length === 0 ? (
          <EmptyState icon={FileCode2} title={q ? "No file matches" : "No generated files yet"}>
            Files written by agents appear here, across every workflow.
          </EmptyState>
        ) : (
          <ul className="divide-y divide-line">
            {visible.map((f) => (
              <li key={f.id}>
                <button type="button" onClick={() => setOpen(f)} className="grid w-full grid-cols-[1fr_auto] items-center gap-x-4 px-4 py-2.5 text-left hover:bg-panel-2 sm:grid-cols-[minmax(0,2fr)_minmax(0,3fr)_5rem_6rem]">
                  <span className="flex min-w-0 items-center gap-2">
                    <FileCode2 className="size-4 shrink-0 text-subtle" />
                    <span className="truncate font-mono text-xs text-fg">{f.path}</span>
                  </span>
                  <span className="hidden truncate text-xs text-muted sm:block">{f.workflow_goal}</span>
                  <span className="text-right text-xs tabular-nums text-subtle">{bytes(f.size)}</span>
                  <span className="hidden text-right text-xs text-subtle sm:block">{timeAgo(f.updated_at)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <Dialog open={open !== null} onOpenChange={(o) => !o && setOpen(null)}>
        <DialogContent title={<span className="font-mono">{open?.path}</span>} wide>
          {open && <ArtifactViewer summary={open} />}
        </DialogContent>
      </Dialog>
    </>
  );
}

function Uploaded({ projectId }: { projectId?: string }) {
  const attachments = useAttachments(projectId);
  const queries = useQueryClient();
  const [deleting, setDeleting] = useState<Attachment | null>(null);
  const remove = useMutation({
    mutationFn: (a: Attachment) => api(`/attachments/${a.id}`, { method: "DELETE" }),
    onSuccess: () => {
      void queries.invalidateQueries({ queryKey: keys.attachments(projectId ?? "") });
      toast.success("File deleted");
      setDeleting(null);
    },
    onError: (e) => toast.error(errorText(e)),
  });
  if (!projectId) return <Notice>Choose a project to see the files uploaded to it.</Notice>;
  return (
    <div className="overflow-hidden rounded-lg border border-line bg-panel">
      {attachments.isPending ? (
        <div className="p-4">
          <Skeleton className="h-5" />
        </div>
      ) : attachments.isError ? (
        <div className="p-4">
          <Notice tone="bad">{attachments.error.message}</Notice>
        </div>
      ) : attachments.data.length === 0 ? (
        <EmptyState icon={FileText} title="No uploads">Attach text files when you create a task; the planner reads them as context.</EmptyState>
      ) : (
        <ul className="divide-y divide-line">
          {attachments.data.map((a) => (
            <li key={a.id} className="flex items-center gap-3 px-4 py-2.5">
              <FileText className="size-4 text-subtle" />
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm text-fg">{a.filename}</div>
                <div className="text-[11px] text-subtle">
                  {bytes(a.size_bytes)} · {timeAgo(a.created_at)} · sha256 {a.sha256.slice(0, 12)}…
                  {a.workflow_id && (
                    <>
                      {" · "}
                      <Link href={`/workflows/${a.workflow_id}`} className="underline">
                        used by a workflow
                      </Link>
                    </>
                  )}
                </div>
              </div>
              <Button asChild size="sm" variant="ghost">
                <a href={`/api/v1/attachments/${a.id}/download`} download aria-label={`Download ${a.filename}`}>
                  <Download />
                </a>
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setDeleting(a)} aria-label={`Delete ${a.filename}`}>
                <Trash2 />
              </Button>
            </li>
          ))}
        </ul>
      )}
      <Confirm open={deleting !== null} onOpenChange={(o) => !o && setDeleting(null)} title={`Delete ${deleting?.filename ?? ""}?`} confirmLabel="Delete" busy={remove.isPending} onConfirm={() => deleting && remove.mutate(deleting)}>
        The stored file is removed. Workflows that already used it keep what they produced.
      </Confirm>
    </div>
  );
}

export function FilesPage() {
  const projects = useProjects();
  const [project, setProject] = useState<string | undefined>();
  const [tab, setTab] = useState("generated");
  const uploadsProject = project ?? projects.data?.[0]?.id;

  return (
    <div className="h-full overflow-y-auto">
      <PageHeader title="Files & artifacts" description="What agents produced, and what you gave them to read." />
      <div className="px-4 py-4 sm:px-6">
        <Tabs value={tab} onValueChange={setTab}>
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <TabsList className="rounded-md border border-line bg-panel p-0.5">
              <TabsTrigger value="generated">Generated</TabsTrigger>
              <TabsTrigger value="uploaded">Uploaded</TabsTrigger>
            </TabsList>
            <div className="w-56">
              <Select
                aria-label="Project"
                value={project ?? (tab === "uploaded" ? uploadsProject : "__all")}
                onValueChange={(v) => setProject(v === "__all" ? undefined : v)}
                options={[...(tab === "generated" ? [{ value: "__all", label: "All projects" }] : []), ...(projects.data ?? []).map((p) => ({ value: p.id, label: p.name }))]}
              />
            </div>
          </div>
          <TabsContent value="generated">
            <Generated projectId={project} />
          </TabsContent>
          <TabsContent value="uploaded">
            <Uploaded projectId={uploadsProject} />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
