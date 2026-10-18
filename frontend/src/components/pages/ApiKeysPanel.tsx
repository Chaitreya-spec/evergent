"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, Plus } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { Notice, Panel, PanelHeader, Skeleton } from "@/components/ui/feedback";
import { Input } from "@/components/ui/form";
import { StatusBadge } from "@/components/ui/status";
import { copy } from "@/components/workspace/bits";
import { api, errorText } from "@/lib/api/client";
import { keys, useApiKeys } from "@/lib/api/queries";
import type { ApiKey, ApiKeyCreated } from "@/lib/api/types";
import { timeAgo } from "@/lib/format";

/** Personal API keys for scripts and CI. The secret is shown exactly once. */
export function ApiKeysPanel({ readOnly }: { readOnly: boolean }) {
  const list = useApiKeys();
  const queries = useQueryClient();
  const [name, setName] = useState("");
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);
  const [revoking, setRevoking] = useState<ApiKey | null>(null);

  const create = useMutation({
    mutationFn: (value: string) => api<ApiKeyCreated>("/api-keys", { method: "POST", json: { name: value } }),
    onSuccess: (key) => {
      setCreated(key);
      setName("");
      void queries.invalidateQueries({ queryKey: keys.apiKeys });
    },
    onError: (e) => toast.error(errorText(e)),
  });
  const revoke = useMutation({
    mutationFn: (key: ApiKey) => api(`/api-keys/${key.id}`, { method: "DELETE" }),
    onSuccess: () => {
      void queries.invalidateQueries({ queryKey: keys.apiKeys });
      toast.success("Key revoked — it stops working immediately");
      setRevoking(null);
    },
    onError: (e) => toast.error(errorText(e)),
  });

  const origin = typeof window === "undefined" ? "" : window.location.origin;

  return (
    <Panel className="xl:col-span-2">
      <PanelHeader
        title="API keys"
        description="For scripts and CI. A key acts as you, with your role; revoke it the moment it might have leaked."
      />
      <div className="space-y-4 p-4">
        {readOnly ? (
          <Notice>Read-only accounts cannot create keys.</Notice>
        ) : (
          <form
            className="flex flex-wrap items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (name.trim()) create.mutate(name.trim());
            }}
          >
            <Input aria-label="Key name" value={name} onChange={(e) => setName(e.target.value)} placeholder="What it is for, e.g. CI pipeline" maxLength={120} className="max-w-xs" />
            <Button type="submit" disabled={!name.trim() || create.isPending}>
              <Plus /> Create key
            </Button>
          </form>
        )}

        {created && (
          <Notice
            tone="warn"
            title={`Copy "${created.name}" now — it will not be shown again`}
            action={
              <Button size="sm" onClick={() => copy(created.key, "Key copied")}>
                <Copy /> Copy
              </Button>
            }
          >
            <code className="block break-all rounded bg-bg px-2 py-1.5 font-mono text-xs text-fg" data-testid="new-api-key">
              {created.key}
            </code>
            <span className="mt-1.5 block text-xs">
              Use it as <code className="font-mono">Authorization: Bearer …</code>, e.g.{" "}
              <code className="font-mono">curl -H &quot;Authorization: Bearer $KEY&quot; {origin}/api/v1/workflows</code>
            </span>
          </Notice>
        )}

        {list.isPending ? (
          <Skeleton className="h-10" />
        ) : list.isError ? (
          <Notice tone="bad">{list.error.message}</Notice>
        ) : list.data.length === 0 ? (
          <p className="text-xs text-subtle">No keys yet.</p>
        ) : (
          <ul className="divide-y divide-line rounded-md border border-line">
            {list.data.map((key) => (
              <li key={key.id} className="flex flex-wrap items-center gap-3 px-3 py-2.5 text-sm">
                <KeyRound className="size-4 text-subtle" />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-fg">{key.name}</div>
                  <div className="text-[11px] text-subtle">
                    <span className="font-mono">{key.prefix}…</span> · created {timeAgo(key.created_at)} ·{" "}
                    {key.last_used_at ? `last used ${timeAgo(key.last_used_at)}` : "never used"}
                  </div>
                </div>
                {key.revoked_at ? (
                  <StatusBadge status="cancelled" label={`Revoked ${timeAgo(key.revoked_at)}`} />
                ) : (
                  <>
                    <StatusBadge status="completed" label="Active" />
                    {!readOnly && (
                      <Button size="sm" variant="danger" onClick={() => setRevoking(key)}>
                        Revoke
                      </Button>
                    )}
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
      <Confirm
        open={revoking !== null}
        onOpenChange={(o) => !o && setRevoking(null)}
        title={`Revoke "${revoking?.name ?? ""}"?`}
        confirmLabel="Revoke key"
        busy={revoke.isPending}
        onConfirm={() => revoking && revoke.mutate(revoking)}
      >
        Anything using it stops working immediately. This cannot be undone.
      </Confirm>
    </Panel>
  );
}
