"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Bot, Pencil, Plus, RotateCcw, Trash2, Wrench } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { EmptyState, Notice, PageHeader, Panel, PanelHeader, Skeleton } from "@/components/ui/feedback";
import { Field, Input, Textarea } from "@/components/ui/form";
import { Badge, StatusBadge } from "@/components/ui/status";
import { Switch } from "@/components/ui/switch";
import { api, errorText } from "@/lib/api/client";
import { keys, useAgents, useMe, useTools } from "@/lib/api/queries";
import type { Agent, Tool } from "@/lib/api/types";
import { cn } from "@/lib/cn";
import { humanize } from "@/lib/format";

const CAPABILITIES = [
  "reasoning",
  "coding",
  "frontend",
  "backend",
  "debugging",
  "mathematics",
  "research",
  "writing",
  "vision",
  "long_context",
  "structured_output",
  "tool_use",
] as const;

// Every agent can read and write files; these are the optional extras.
const OPTIONAL_TOOLS = ["web_search", "fetch_url", "run_code", "run_sql"];

function Toggles({ legend, all, value, onChange }: { legend: string; all: readonly string[]; value: string[]; onChange: (v: string[]) => void }) {
  return (
    <fieldset>
      <legend className="mb-1.5 text-xs font-medium text-muted">{legend}</legend>
      <div className="flex flex-wrap gap-1.5">
        {all.map((item) => {
          const on = value.includes(item);
          return (
            <button
              key={item}
              type="button"
              aria-pressed={on}
              onClick={() => onChange(on ? value.filter((v) => v !== item) : [...value, item])}
              className={cn(
                "h-7 rounded-md border px-2 font-mono text-[11px] transition-colors",
                on ? "border-accent/60 bg-accent-soft text-fg" : "border-line bg-panel-2 text-subtle hover:text-fg",
              )}
            >
              {item}
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}

function AgentForm({ agent, onDone }: { agent?: Agent; onDone: () => void }) {
  const queries = useQueryClient();
  const [capabilities, setCapabilities] = useState<string[]>(agent?.capabilities ?? ["reasoning", "coding"]);
  const [tools, setTools] = useState<string[]>(agent ? agent.allowed_tools.filter((t) => OPTIONAL_TOOLS.includes(t)) : ["web_search", "fetch_url"]);
  const save = useMutation({
    mutationFn: (form: FormData) => {
      const common = {
        display_name: String(form.get("display_name") ?? "").trim(),
        description: String(form.get("description") ?? "").trim(),
        instructions: String(form.get("instructions") ?? "").trim(),
        capabilities,
        tools,
      };
      return agent
        ? api<Agent>(`/agents/${agent.name}`, { method: "PATCH", json: common })
        : api<Agent>("/agents", { method: "POST", json: { ...common, name: String(form.get("name") ?? "").trim() } });
    },
    onSuccess: () => {
      void queries.invalidateQueries({ queryKey: keys.agents });
      toast.success(agent ? "Agent saved" : "Agent added");
      onDone();
    },
  });
  return (
    <form action={(f) => save.mutate(f)} className="space-y-4">
      {!agent && (
        <Field label="Name" hint="Lowercase letters, digits and dashes (2–40). Used in plans and logs.">
          {(p) => <Input {...p} name="name" required pattern="[a-z][a-z0-9\-]{1,39}" placeholder="devops" />}
        </Field>
      )}
      <Field label="Display name">{(p) => <Input {...p} name="display_name" required maxLength={120} defaultValue={agent?.display_name} />}</Field>
      <Field label="What it is for" hint="The planner reads this to decide what to hand the agent.">
        {(p) => <Textarea {...p} name="description" required maxLength={500} rows={2} defaultValue={agent?.description} />}
      </Field>
      <Field label="Instructions" hint="The role only; the output format is added automatically.">
        {(p) => <Textarea {...p} name="instructions" required maxLength={20000} rows={8} defaultValue={agent?.instructions} className="font-mono text-xs" />}
      </Field>
      <Toggles legend="Capabilities the router matches models on" all={CAPABILITIES} value={capabilities} onChange={setCapabilities} />
      <Toggles legend="Tools beyond file read/write" all={OPTIONAL_TOOLS} value={tools} onChange={setTools} />
      {save.isError && <Notice tone="bad">{errorText(save.error)}</Notice>}
      <div className="flex justify-end gap-2">
        <Button onClick={onDone}>Cancel</Button>
        <Button type="submit" variant="primary" disabled={save.isPending || capabilities.length === 0}>
          {agent ? "Save" : "Add agent"}
        </Button>
      </div>
    </form>
  );
}

function ToolsPanel({ tools }: { tools: Tool[] }) {
  return (
    <Panel>
      <PanelHeader title="Tools" description="What agents can call, and whether each is usable on this server right now." />
      <ul className="divide-y divide-line">
        {tools.map((t) => (
          <li key={t.name} className="flex flex-wrap items-start gap-3 px-4 py-3">
            <Wrench className="mt-0.5 size-4 text-subtle" />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm text-fg">{t.name}</span>
                <Badge>{t.permission}</Badge>
              </div>
              <p className="mt-0.5 text-xs text-muted">{t.description}</p>
              {!t.available && <p className="mt-1 text-xs text-warn">{t.status}</p>}
            </div>
            <StatusBadge status={t.available ? "completed" : "paused"} label={t.available ? "Available" : "Unavailable"} />
          </li>
        ))}
      </ul>
    </Panel>
  );
}

export function AgentsPage() {
  const agents = useAgents();
  const tools = useTools();
  const me = useMe();
  const queries = useQueryClient();
  const admin = me.data?.role === "admin";
  const [editing, setEditing] = useState<Agent | null>(null);
  const [creating, setCreating] = useState(false);
  const [removing, setRemoving] = useState<Agent | null>(null);

  const toggle = useMutation({
    mutationFn: ({ agent, on }: { agent: Agent; on: boolean }) => api<Agent>(`/agents/${agent.name}`, { method: "PATCH", json: { is_enabled: on } }),
    onSuccess: () => void queries.invalidateQueries({ queryKey: keys.agents }),
    onError: (e) => toast.error(errorText(e)),
  });
  const remove = useMutation({
    mutationFn: (agent: Agent) => api(`/agents/${agent.name}`, { method: "DELETE" }),
    onSuccess: (_, agent) => {
      void queries.invalidateQueries({ queryKey: keys.agents });
      toast.success(agent.source === "override" ? "Restored the built-in agent" : "Agent removed");
      setRemoving(null);
    },
    onError: (e) => toast.error(errorText(e)),
  });

  const engineers = (agents.data ?? []).filter((a) => a.assignable);
  const roles = (agents.data ?? []).filter((a) => !a.assignable);

  return (
    <div className="h-full overflow-y-auto">
      <PageHeader
        title="Agent registry"
        description="The specialists the planner can assign work to, and the engine's own roles."
        actions={
          admin && (
            <Button variant="primary" onClick={() => setCreating(true)}>
              <Plus /> Add agent
            </Button>
          )
        }
      />
      <div className="space-y-6 px-4 py-4 sm:px-6">
        {!admin && me.isSuccess && <Notice>Only administrators can change agents. You can see how each one is set up.</Notice>}
        {agents.isPending ? (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {[0, 1, 2, 3, 4, 5].map((i) => (
              <Skeleton key={i} className="h-44" />
            ))}
          </div>
        ) : agents.isError ? (
          <Notice tone="bad">{agents.error.message}</Notice>
        ) : agents.data.length === 0 ? (
          <EmptyState icon={Bot} title="No agents" />
        ) : (
          <>
            {[{ title: "Engineers", list: engineers }, { title: "Engine roles", list: roles }].map((group) => (
              <section key={group.title}>
                <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-subtle">
                  {group.title} <span className="tabular-nums">({group.list.length})</span>
                </h2>
                <ul className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                  {group.list.map((a) => (
                    <li key={a.name} className={cn("flex flex-col rounded-lg border border-line bg-panel p-4", !a.is_enabled && "opacity-60")}>
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="truncate text-sm font-semibold text-fg">{a.display_name}</div>
                          <div className="font-mono text-[11px] text-subtle">{a.name}</div>
                        </div>
                        <div className="flex items-center gap-1.5">
                          {a.source !== "builtin" && <Badge>{a.source}</Badge>}
                          {admin && a.assignable && (
                            <Switch
                              checked={a.is_enabled}
                              onCheckedChange={(on) => toggle.mutate({ agent: a, on })}
                              aria-label={`${a.is_enabled ? "Disable" : "Enable"} ${a.display_name}`}
                            />
                          )}
                        </div>
                      </div>
                      <p className="mt-2 text-xs leading-relaxed text-muted">{a.description}</p>
                      {a.handles.length > 0 && (
                        <p className="mt-2 text-[11px] text-subtle">
                          Handles: <span className="text-muted">{a.handles.map(humanize).join(", ")}</span>
                        </p>
                      )}
                      <div className="mt-2 flex flex-wrap gap-1">
                        {a.capabilities.map((c) => (
                          <span key={c} className="rounded bg-panel-3 px-1.5 py-0.5 text-[10px] text-muted">
                            {c}
                          </span>
                        ))}
                      </div>
                      <div className="mt-1.5 flex flex-wrap gap-1">
                        {a.allowed_tools.map((t) => (
                          <span key={t} className="rounded border border-line px-1.5 py-0.5 font-mono text-[10px] text-subtle">
                            {t}
                          </span>
                        ))}
                      </div>
                      <div className="mt-auto flex items-center justify-between gap-2 pt-3 text-[11px] text-subtle">
                        <span>{a.requires_review ? "Work is reviewed" : "Not reviewed"}</span>
                        {admin && a.assignable && (
                          <div className="flex gap-1">
                            <Button size="sm" variant="ghost" onClick={() => setEditing(a)}>
                              <Pencil /> Edit
                            </Button>
                            {a.source !== "builtin" && (
                              <Button size="sm" variant="ghost" onClick={() => setRemoving(a)}>
                                {a.source === "override" ? <RotateCcw /> : <Trash2 />}
                                {a.source === "override" ? "Restore" : "Remove"}
                              </Button>
                            )}
                          </div>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </>
        )}
        {tools.data && <ToolsPanel tools={tools.data} />}
      </div>

      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent title="Add an agent" wide>
          {creating && <AgentForm onDone={() => setCreating(false)} />}
        </DialogContent>
      </Dialog>
      <Dialog open={editing !== null} onOpenChange={(o) => !o && setEditing(null)}>
        <DialogContent title={`Edit ${editing?.display_name ?? ""}`} description={editing?.source === "builtin" ? "Saving creates an override; Restore brings back the built-in." : undefined} wide>
          {editing && <AgentForm agent={editing} onDone={() => setEditing(null)} />}
        </DialogContent>
      </Dialog>
      <Confirm
        open={removing !== null}
        onOpenChange={(o) => !o && setRemoving(null)}
        title={removing?.source === "override" ? `Restore ${removing?.display_name}?` : `Remove ${removing?.display_name}?`}
        confirmLabel={removing?.source === "override" ? "Restore built-in" : "Remove agent"}
        busy={remove.isPending}
        onConfirm={() => removing && remove.mutate(removing)}
      >
        {removing?.source === "override"
          ? "Your changes are discarded and the built-in definition comes back."
          : "Planned tasks already assigned to it are handed to the closest built-in agent."}
      </Confirm>
    </div>
  );
}
