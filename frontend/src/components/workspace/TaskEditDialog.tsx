"use client";

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Notice } from "@/components/ui/feedback";
import { Field, Input, Textarea } from "@/components/ui/form";
import { Select } from "@/components/ui/select";
import { api, errorText } from "@/lib/api/client";
import type { Agent, Model, Task, TaskUpdate } from "@/lib/api/types";

/** Amend a planned task before it runs — the point of approving a plan. */
export function TaskEditDialog({
  task,
  agents,
  models,
  open,
  onOpenChange,
  onSaved,
}: {
  task: Task;
  agents: Agent[];
  models: Model[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
}) {
  const [title, setTitle] = useState(task.title);
  const [description, setDescription] = useState(task.description);
  const [priority, setPriority] = useState(String(task.priority));
  const [agent, setAgent] = useState(task.agent_name ?? "");
  const [model, setModel] = useState(task.model_name ?? "__auto");

  const save = useMutation({
    mutationFn: () => {
      const body: TaskUpdate = {};
      if (title !== task.title) body.title = title;
      if (description !== task.description) body.description = description;
      if (Number(priority) !== task.priority) body.priority = Number(priority);
      if (agent && agent !== task.agent_name) body.agent = agent;
      const pinned = model === "__auto" ? "" : model;
      if (pinned !== (task.model_name ?? "")) body.model = pinned;
      return api<Task>(`/tasks/${task.id}`, { method: "PATCH", json: body });
    },
    onSuccess: () => {
      onSaved();
      onOpenChange(false);
    },
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={`Edit ${task.key}`} description="Changes apply when the plan runs. A new engineer gets a freshly routed model unless you pin one.">
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <Field label="Title">{(p) => <Input {...p} value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={300} />}</Field>
          <Field label="Instructions">{(p) => <Textarea {...p} value={description} onChange={(e) => setDescription(e.target.value)} rows={7} required />}</Field>
          <div className="grid gap-4 sm:grid-cols-[1fr_6rem]">
            <Field label="Engineer">
              {(p) => (
                <Select
                  id={p.id}
                  value={agent}
                  onValueChange={setAgent}
                  options={agents.filter((a) => a.assignable && a.is_enabled).map((a) => ({ value: a.name, label: a.display_name, hint: a.handles.join(", ") }))}
                />
              )}
            </Field>
            <Field label="Priority">{(p) => <Input {...p} type="number" min={1} max={10} value={priority} onChange={(e) => setPriority(e.target.value)} />}</Field>
          </div>
          <Field label="Model" hint="Automatic lets the router choose by the workflow's strategy.">
            {(p) => (
              <Select
                id={p.id}
                value={model}
                onValueChange={setModel}
                options={[
                  { value: "__auto", label: "Automatic (router decides)" },
                  ...models.filter((m) => m.is_enabled).map((m) => ({ value: m.name, label: m.display_name, hint: m.provider_name })),
                ]}
              />
            )}
          </Field>
          {save.isError && <Notice tone="bad">{errorText(save.error)}</Notice>}
          <div className="flex justify-end gap-2">
            <Button onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" variant="primary" disabled={save.isPending}>
              Save
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
