"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowUp, FileText, FolderPlus, Paperclip, Users, X } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Notice, Spinner } from "@/components/ui/feedback";
import { Field, Input, Textarea } from "@/components/ui/form";
import { Segmented } from "@/components/ui/segmented";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { api, errorText } from "@/lib/api/client";
import { keys, useAgents, useMe, useModels, useProjects, useProviders } from "@/lib/api/queries";
import type { Attachment, Project, Provider, Workflow, WorkflowCreate } from "@/lib/api/types";
import { cn } from "@/lib/cn";
import { bytes, money } from "@/lib/format";
import { recall, remember } from "@/lib/storage";

type Strategy = "balanced" | "highest_quality" | "fastest" | "cheapest" | "manual";

const STRATEGIES: { value: Strategy; label: string; hint: string }[] = [
  { value: "balanced", label: "Balanced", hint: "Weigh capability against cost and speed" },
  { value: "highest_quality", label: "Quality", hint: "The most capable model for each task" },
  { value: "fastest", label: "Speed", hint: "The quickest capable model" },
  { value: "cheapest", label: "Cost", hint: "The cheapest capable model" },
  { value: "manual", label: "Manual", hint: "Choose one model for every task" },
];

const ACCEPT = ".txt,.md,.csv,.json,.yaml,.yml,.toml,.xml,.html,.css,.py,.js,.jsx,.ts,.tsx,.sql,.sh,.go,.rs,.java,.rb";
const MAX_FILES = 10;

const usable = (p: Provider) => p.is_enabled && p.configured && p.verification_ok !== false;

function newKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/**
 * Name a first project, inline. Not a <form>: it sits inside the composer's
 * form, and forms cannot nest.
 */
function FirstProject({ onCreated }: { onCreated: (project: Project) => void }) {
  const queries = useQueryClient();
  const [name, setName] = useState("");
  const create = useMutation({
    mutationFn: (value: string) => api<Project>("/projects", { method: "POST", json: { name: value } }),
    onSuccess: (project) => {
      void queries.invalidateQueries({ queryKey: keys.projects });
      onCreated(project);
    },
  });
  const submit = () => {
    if (name.trim()) create.mutate(name.trim());
  };
  return (
    <div className="flex flex-wrap items-end gap-2" role="group" aria-label="First project">
      <Field label="Project" hint="Work is organised in projects. Name your first one." className="min-w-56 flex-1">
        {(p) => (
          <Input
            {...p}
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                submit();
              }
            }}
            maxLength={200}
            placeholder="e.g. Customer portal"
          />
        )}
      </Field>
      <Button onClick={submit} disabled={create.isPending || !name.trim()}>
        <FolderPlus /> Create
      </Button>
      {create.isError && <p className="w-full text-xs text-bad">{errorText(create.error)}</p>}
    </div>
  );
}

export function TaskComposer({ autoFocus = false }: { autoFocus?: boolean }) {
  const router = useRouter();
  const queries = useQueryClient();
  const projects = useProjects();
  const models = useModels();
  const providers = useProviders();
  const agents = useAgents();
  const me = useMe();
  const readOnly = me.data?.role === "viewer";

  const [goal, setGoal] = useState("");
  const [projectId, setProjectId] = useState<string | undefined>();
  const [strategy, setStrategy] = useState<Strategy>("balanced");
  const [model, setModel] = useState<string | undefined>();
  const [allowFallback, setAllowFallback] = useState(true);
  const [requireApproval, setRequireApproval] = useState(true);
  const [budget, setBudget] = useState("");
  const [team, setTeam] = useState<string[] | null>(null);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const idempotencyKey = useRef(newKey());
  const fileInput = useRef<HTMLInputElement>(null);

  // Default to the project used last in this browser, else the newest.
  const projectList = projects.data ?? [];
  const selectedProject = projectList.find((p) => p.id === projectId) ?? projectList.find((p) => p.id === recall("project")) ?? projectList[0];
  const effectiveProjectId = selectedProject?.id;

  const providerByName = useMemo(() => new Map((providers.data ?? []).map((p) => [p.name, p])), [providers.data]);
  const reachable = (providers.data ?? []).filter(usable);
  const noProvider = providers.isSuccess && reachable.length === 0;

  const modelOptions = (models.data ?? [])
    .filter((m) => m.is_enabled)
    .map((m) => {
      const provider = providerByName.get(m.provider_name);
      const ok = provider ? usable(provider) : false;
      return {
        value: m.name,
        label: m.display_name,
        hint: ok ? `${provider?.display_name ?? m.provider_name} · in ${money(m.cost_per_input_token * 1e6)}/M · out ${money(m.cost_per_output_token * 1e6)}/M` : `${m.provider_name} — not configured`,
        disabled: !ok,
      };
    })
    .sort((a, b) => Number(a.disabled) - Number(b.disabled) || a.label.localeCompare(b.label));

  const assignable = (agents.data ?? []).filter((a) => a.assignable && a.is_enabled);

  useEffect(() => {
    if (autoFocus) document.getElementById("task-goal")?.focus();
  }, [autoFocus]);

  async function upload(files: FileList | null) {
    if (!files || !effectiveProjectId) return;
    const room = MAX_FILES - attachments.length;
    const chosen = Array.from(files).slice(0, Math.max(0, room));
    if (files.length > room) toast.warning(`Up to ${MAX_FILES} files per task.`);
    for (const file of chosen) {
      setUploading((n) => n + 1);
      try {
        const form = new FormData();
        form.append("file", file);
        const saved = await api<Attachment>(`/projects/${effectiveProjectId}/attachments`, { method: "POST", form });
        setAttachments((list) => [...list, saved]);
      } catch (e) {
        toast.error(`${file.name}: ${errorText(e)}`);
      } finally {
        setUploading((n) => n - 1);
      }
    }
    if (fileInput.current) fileInput.current.value = "";
  }

  async function removeAttachment(attachment: Attachment) {
    setAttachments((list) => list.filter((a) => a.id !== attachment.id));
    try {
      await api(`/attachments/${attachment.id}`, { method: "DELETE" });
    } catch (e) {
      toast.error(`Could not delete ${attachment.filename}: ${errorText(e)}`);
    }
  }

  const submit = useMutation({
    mutationFn: (body: WorkflowCreate) =>
      api<Workflow>("/workflows", {
        method: "POST",
        json: body,
        headers: { "Idempotency-Key": idempotencyKey.current },
      }),
    onSuccess: (workflow) => {
      idempotencyKey.current = newKey();
      void queries.invalidateQueries({ queryKey: keys.allWorkflows });
      router.push(`/workflows/${workflow.id}`);
    },
    onError: (e) => setError(errorText(e)),
  });

  const trimmed = goal.trim();
  const budgetValue = budget.trim() === "" ? null : Number(budget);
  const budgetInvalid = budgetValue !== null && (!Number.isFinite(budgetValue) || budgetValue <= 0 || budgetValue > 1000);
  const blockers = [
    readOnly && "This account is read-only.",
    !effectiveProjectId && "Create a project first.",
    trimmed.length < 10 && "Describe the task in at least 10 characters.",
    strategy === "manual" && !model && "Choose the model to use.",
    team !== null && team.length === 0 && "Pick at least one engineer, or let the planner choose.",
    budgetInvalid && "The budget must be between $0 and $1,000.",
    noProvider && "No model provider is configured.",
    uploading > 0 && "Waiting for uploads to finish.",
  ].filter(Boolean) as string[];

  function send() {
    setError(null);
    if (blockers.length || !effectiveProjectId) return;
    remember("project", effectiveProjectId);
    submit.mutate({
      project_id: effectiveProjectId,
      goal: trimmed,
      routing_strategy: strategy === "manual" ? "balanced" : strategy,
      ...(strategy === "manual" && model ? { model, allow_fallback: allowFallback } : {}),
      require_approval: requireApproval,
      ...(budgetValue !== null ? { max_cost_usd: budgetValue } : {}),
      ...(team ? { team } : {}),
      attachment_ids: attachments.map((a) => a.id),
      autostart: true,
    });
  }

  return (
    <form
      className="rounded-lg border border-line bg-panel"
      onSubmit={(e) => {
        e.preventDefault();
        send();
      }}
      aria-label="New task"
    >
      <div className="p-4">
        <label htmlFor="task-goal" className="sr-only">
          What should the team build?
        </label>
        <Textarea
          id="task-goal"
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              send();
            }
          }}
          maxLength={8000}
          rows={5}
          placeholder="Describe what to build. For example: a FastAPI service with JWT auth, a Postgres schema for orders, tests, and a README."
          className="min-h-32 border-0 bg-transparent px-0 text-[15px] hover:border-0 focus:border-0"
        />

        {attachments.length > 0 && (
          <ul className="mt-2 flex flex-wrap gap-1.5" aria-label="Attached files">
            {attachments.map((a) => (
              <li key={a.id} className="flex h-7 items-center gap-1.5 rounded-md border border-line bg-panel-2 pl-2 pr-1 text-xs text-muted">
                <FileText className="size-3.5 text-subtle" />
                <span className="max-w-48 truncate text-fg">{a.filename}</span>
                <span className="text-subtle">{bytes(a.size_bytes)}</span>
                <button type="button" onClick={() => void removeAttachment(a)} className="rounded p-0.5 hover:bg-panel-3 hover:text-fg" aria-label={`Remove ${a.filename}`}>
                  <X className="size-3.5" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="space-y-4 border-t border-line p-4">
        {projects.isSuccess && projectList.length === 0 ? (
          <FirstProject onCreated={(p) => setProjectId(p.id)} />
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Project">
              {(p) => (
                <Select
                  id={p.id}
                  value={effectiveProjectId}
                  onValueChange={setProjectId}
                  placeholder={projects.isPending ? "Loading…" : "Choose a project"}
                  options={projectList.map((proj) => ({ value: proj.id, label: proj.name }))}
                />
              )}
            </Field>
            <Field
              label="Budget for this run (USD)"
              hint={selectedProject?.max_cost_usd ? `Leave empty for the project default (${money(selectedProject.max_cost_usd)}).` : "Leave empty for the server default."}
              error={budgetInvalid ? "Between $0 and $1,000." : null}
            >
              {(p) => <Input {...p} inputMode="decimal" value={budget} onChange={(e) => setBudget(e.target.value)} placeholder={selectedProject?.max_cost_usd ? selectedProject.max_cost_usd.toFixed(2) : "Default"} />}
            </Field>
          </div>
        )}

        <Segmented legend="Routing strategy" value={strategy} onChange={setStrategy} options={STRATEGIES} />

        {strategy === "manual" && (
          <div className="grid gap-4 rounded-md border border-line bg-panel-2/50 p-3 sm:grid-cols-[1fr_auto] sm:items-end">
            <Field label="Model for every task">
              {(p) => <Select id={p.id} value={model} onValueChange={setModel} options={modelOptions} placeholder={models.isPending ? "Loading…" : "Choose a model"} />}
            </Field>
            <label className="flex h-9 items-center gap-2 text-sm text-muted">
              <Switch checked={allowFallback} onCheckedChange={setAllowFallback} aria-label="Allow fallback" />
              Fall back to another model if it fails
            </label>
          </div>
        )}

        <div className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="flex items-center gap-1.5 text-xs font-medium text-muted">
              <Users className="size-3.5" /> Engineers
            </span>
            <label className="flex items-center gap-2 text-xs text-muted">
              <Switch checked={team === null} onCheckedChange={(on) => setTeam(on ? null : [])} aria-label="Let the planner choose engineers" />
              Planner chooses from everyone
            </label>
          </div>
          {team !== null && (
            <div className="flex flex-wrap gap-1.5" role="group" aria-label="Engineers on this task">
              {assignable.map((agent) => {
                const on = team.includes(agent.name);
                return (
                  <button
                    key={agent.name}
                    type="button"
                    aria-pressed={on}
                    title={agent.description}
                    onClick={() => setTeam((t) => (t ?? []).includes(agent.name) ? (t ?? []).filter((n) => n !== agent.name) : [...(t ?? []), agent.name])}
                    className={cn(
                      "h-7 rounded-md border px-2.5 text-xs transition-colors",
                      on ? "border-accent/60 bg-accent-soft text-fg" : "border-line bg-panel-2 text-muted hover:border-line-strong hover:text-fg",
                    )}
                  >
                    {agent.display_name}
                  </button>
                );
              })}
              {assignable.length === 0 && <span className="text-xs text-subtle">No engineers are enabled.</span>}
            </div>
          )}
        </div>
      </div>

      {readOnly && (
        <div className="px-4 pb-3">
          <Notice tone="warn" title="This account is read-only">
            Viewers can look at projects, workflows and results but cannot start or change work. Ask an administrator for member access.
          </Notice>
        </div>
      )}
      {noProvider && (
        <div className="px-4 pb-3">
          <Notice tone="warn" title="No model provider is configured">
            Every task needs a model. <Link className="text-fg underline" href="/models">Configure a provider</Link> first.
          </Notice>
        </div>
      )}
      {error && (
        <div className="px-4 pb-3">
          <Notice tone="bad">{error}</Notice>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3 border-t border-line px-4 py-3">
        <input ref={fileInput} type="file" multiple accept={ACCEPT} className="hidden" onChange={(e) => void upload(e.target.files)} aria-hidden tabIndex={-1} />
        <Button variant="ghost" size="sm" onClick={() => fileInput.current?.click()} disabled={!effectiveProjectId || attachments.length >= MAX_FILES} title="Text files up to 2 MB, handed to the planner as context">
          {uploading > 0 ? <Spinner /> : <Paperclip />} Attach files
        </Button>
        <label className="flex items-center gap-2 text-xs text-muted">
          <Switch checked={requireApproval} onCheckedChange={setRequireApproval} aria-label="Review the plan before work starts" />
          Review the plan before work starts
        </label>
        <div className="ml-auto flex items-center gap-3">
          {blockers[0] && trimmed.length > 0 && <span className="hidden text-xs text-subtle md:inline">{blockers[0]}</span>}
          <Button type="submit" variant="primary" disabled={blockers.length > 0 || submit.isPending} aria-describedby="composer-shortcut">
            {submit.isPending ? <Spinner className="text-accent-fg" /> : <ArrowUp />}
            {requireApproval ? "Plan it" : "Start"}
          </Button>
          <span id="composer-shortcut" className="sr-only">
            Or press Control or Command and Enter.
          </span>
        </div>
      </div>
    </form>
  );
}
