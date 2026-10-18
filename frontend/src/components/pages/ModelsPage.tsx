"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Cpu, KeyRound, RefreshCw, ShieldCheck } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { EmptyState, Notice, PageHeader, Panel, PanelHeader, Skeleton, Spinner } from "@/components/ui/feedback";
import { Field, Input } from "@/components/ui/form";
import { Badge, StatusBadge } from "@/components/ui/status";
import { Switch } from "@/components/ui/switch";
import { api, errorText } from "@/lib/api/client";
import { keys, useMe, useModels, useProviders } from "@/lib/api/queries";
import type { Model, Provider } from "@/lib/api/types";
import { cn } from "@/lib/cn";
import { duration, perMillion, timeAgo, tokens } from "@/lib/format";

export function providerState(p: Provider): { status: string; label: string } {
  if (!p.is_enabled) return { status: "cancelled", label: "Disabled" };
  if (!p.configured) return { status: "pending", label: "Not configured" };
  if (p.verification_ok === true) return { status: "completed", label: "Verified" };
  if (p.verification_ok === false) return { status: "failed", label: "Verification failed" };
  return { status: "paused", label: "Configured, not verified" };
}

function KeyDialog({ provider, onDone }: { provider: Provider; onDone: () => void }) {
  const queries = useQueryClient();
  const save = useMutation({
    mutationFn: async ({ form, verify }: { form: FormData; verify: boolean }) => {
      const key = String(form.get("api_key") ?? "");
      const base = String(form.get("base_url") ?? "").trim();
      const body: Record<string, string | null> = {};
      if (key) body.api_key = key;
      if (base !== (provider.base_url ?? "")) body.base_url = base || null;
      let result = Object.keys(body).length ? await api<Provider>(`/providers/${provider.id}`, { method: "PATCH", json: body }) : provider;
      if (verify) result = await api<Provider>(`/providers/${provider.id}/verify`, { method: "POST" });
      return result;
    },
    onSuccess: (result, { verify }) => {
      void queries.invalidateQueries({ queryKey: keys.providers });
      void queries.invalidateQueries({ queryKey: keys.readiness });
      if (verify && result.verification_ok === false) toast.error(`Saved, but verification failed: ${result.verification_error}`);
      else toast.success(verify ? "Saved and verified" : "Saved");
      onDone();
    },
  });
  const verify = useRef(true);
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate({ form: new FormData(e.currentTarget), verify: verify.current });
      }}
      className="space-y-4"
    >
      <Field label="API key" hint="Encrypted at rest. It is never shown again or sent to the browser. Leave empty to keep the current one.">
        {(p) => <Input {...p} name="api_key" type="password" autoComplete="off" spellCheck={false} placeholder={provider.api_key_preview ?? "sk-…"} />}
      </Field>
      {(provider.name === "compatible" || provider.name === "openrouter" || provider.base_url) && (
        <Field label="Base URL" hint="For OpenAI-compatible gateways, e.g. http://127.0.0.1:20128/v1">
          {(p) => <Input {...p} name="base_url" type="url" defaultValue={provider.base_url ?? ""} />}
        </Field>
      )}
      {save.isError && <Notice tone="bad">{errorText(save.error)}</Notice>}
      <div className="flex justify-end gap-2">
        <Button onClick={onDone}>Cancel</Button>
        <Button type="submit" onClick={() => (verify.current = false)} disabled={save.isPending}>
          Save
        </Button>
        <Button type="submit" variant="primary" onClick={() => (verify.current = true)} disabled={save.isPending}>
          {save.isPending && <Spinner className="text-accent-fg" />} Save and verify
        </Button>
      </div>
    </form>
  );
}

function ProviderCard({ provider, admin, onKey }: { provider: Provider; admin: boolean; onKey: () => void }) {
  const queries = useQueryClient();
  const state = providerState(provider);
  const refresh = () => {
    void queries.invalidateQueries({ queryKey: keys.providers });
    void queries.invalidateQueries({ queryKey: keys.readiness });
  };
  const verify = useMutation({
    mutationFn: () => api<Provider>(`/providers/${provider.id}/verify`, { method: "POST" }),
    onSuccess: (p) => {
      refresh();
      if (p.verification_ok) toast.success(`${p.display_name} answered: ${p.verified_model_count} models listed`);
      else toast.error(`${p.display_name}: ${p.verification_error}`);
    },
    onError: (e) => toast.error(errorText(e)),
  });
  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<Provider>(`/providers/${provider.id}`, { method: "PATCH", json: body }),
    onSuccess: refresh,
    onError: (e) => toast.error(errorText(e)),
  });

  return (
    <li className="flex flex-col rounded-lg border border-line bg-panel p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-fg">{provider.display_name}</div>
          <div className="font-mono text-[11px] text-subtle">{provider.name}</div>
        </div>
        {admin ? (
          <Switch checked={provider.is_enabled} onCheckedChange={(on) => patch.mutate({ is_enabled: on })} aria-label={`${provider.is_enabled ? "Disable" : "Enable"} ${provider.display_name}`} />
        ) : null}
      </div>
      <div className="mt-3">
        <StatusBadge status={state.status} label={state.label} />
      </div>
      <dl className="mt-3 space-y-1 text-xs">
        <div className="flex justify-between gap-2">
          <dt className="text-subtle">Credential</dt>
          <dd className="truncate text-muted">
            {provider.configured ? (
              <>
                {provider.credential_source === "database" ? "Stored key" : "Server environment"}
                {provider.api_key_preview && <span className="ml-1 font-mono text-subtle">{provider.api_key_preview}</span>}
              </>
            ) : (
              "None"
            )}
          </dd>
        </div>
        {provider.base_url && (
          <div className="flex justify-between gap-2">
            <dt className="text-subtle">Endpoint</dt>
            <dd className="truncate font-mono text-muted" title={provider.base_url}>
              {provider.base_url}
            </dd>
          </div>
        )}
        <div className="flex justify-between gap-2">
          <dt className="text-subtle">Catalogue models</dt>
          <dd className="text-muted tabular-nums">{provider.model_count ?? 0}</dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt className="text-subtle">Last check</dt>
          <dd className="text-muted">{provider.verified_at ? `${timeAgo(provider.verified_at)}${provider.verification_ok ? ` · ${provider.verified_model_count} listed` : ""}` : "Never"}</dd>
        </div>
      </dl>
      {provider.verification_ok === false && provider.verification_error && (
        <p className="mt-2 line-clamp-3 break-words rounded bg-bad-soft px-2 py-1.5 text-[11px] text-bad" title={provider.verification_error}>
          {provider.verification_error}
        </p>
      )}
      {admin && (
        <div className="mt-auto flex flex-wrap gap-1.5 pt-3">
          <Button size="sm" onClick={() => verify.mutate()} disabled={verify.isPending || !provider.configured}>
            {verify.isPending ? <Spinner /> : <ShieldCheck />} Verify
          </Button>
          <Button size="sm" variant="ghost" onClick={onKey}>
            <KeyRound /> {provider.credential_source === "database" ? "Change key" : "Set key"}
          </Button>
          {provider.credential_source === "database" && (
            <Button size="sm" variant="ghost" onClick={() => patch.mutate({ api_key: "" })} disabled={patch.isPending}>
              Remove stored key
            </Button>
          )}
        </div>
      )}
    </li>
  );
}

function capabilityLeaders(model: Model): string[] {
  return Object.entries(model.capabilities)
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .slice(0, 3)
    .map(([name, score]) => `${name} ${Number(score).toFixed(1)}`);
}

export function ModelsPage() {
  const me = useMe();
  const providers = useProviders();
  const models = useModels();
  const queries = useQueryClient();
  const admin = me.data?.role === "admin";
  const [keyFor, setKeyFor] = useState<Provider | null>(null);
  const byName = new Map((providers.data ?? []).map((p) => [p.name, p]));

  const stats = useMutation({
    mutationFn: () => api("/models/refresh-stats", { method: "POST" }),
    onSuccess: () => {
      void queries.invalidateQueries({ queryKey: keys.models });
      toast.success("Latency and reliability recomputed from recorded calls");
    },
    onError: (e) => toast.error(errorText(e)),
  });

  const usableCount = (providers.data ?? []).filter((p) => p.is_enabled && p.configured).length;

  return (
    <div className="h-full overflow-y-auto">
      <PageHeader
        title="Models & providers"
        description="Configured means a credential exists. Verified means the provider actually answered."
        actions={
          admin && (
            <Button onClick={() => stats.mutate()} disabled={stats.isPending}>
              <RefreshCw /> Recompute model stats
            </Button>
          )
        }
      />
      <div className="space-y-6 px-4 py-4 sm:px-6">
        {providers.isSuccess && usableCount === 0 && (
          <Notice tone="warn" title="No provider is configured">
            {admin
              ? "Set an API key on at least one provider, then verify it. Keys can also come from the server environment (see .env.example)."
              : "Ask an administrator to configure a model provider."}
          </Notice>
        )}
        <section>
          <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-subtle">Providers</h2>
          {providers.isPending ? (
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-48" />
              ))}
            </div>
          ) : providers.isError ? (
            <Notice tone="bad">{providers.error.message}</Notice>
          ) : (
            <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {providers.data.map((p) => (
                <ProviderCard key={p.id} provider={p} admin={admin} onKey={() => setKeyFor(p)} />
              ))}
            </ul>
          )}
        </section>

        <Panel>
          <PanelHeader title="Model catalogue" description="The router scores these per task. Prices are list prices used for estimates." />
          {models.isPending ? (
            <div className="space-y-2 p-4">
              <Skeleton className="h-5" />
              <Skeleton className="h-5" />
            </div>
          ) : models.isError ? (
            <div className="p-4">
              <Notice tone="bad">{models.error.message}</Notice>
            </div>
          ) : models.data.length === 0 ? (
            <EmptyState icon={Cpu} title="No models in the catalogue" />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[56rem] text-left text-xs">
                <thead className="border-b border-line text-[11px] uppercase tracking-wide text-subtle">
                  <tr>
                    <th scope="col" className="px-4 py-2 font-medium">Model</th>
                    <th scope="col" className="px-3 py-2 font-medium">Provider</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">Context</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">In / 1M</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">Out / 1M</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">Latency</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">Reliability</th>
                    <th scope="col" className="px-3 py-2 font-medium">Strongest at</th>
                    <th scope="col" className="px-4 py-2 font-medium">Reachable</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {[...models.data]
                    .sort((a, b) => a.provider_name.localeCompare(b.provider_name) || a.display_name.localeCompare(b.display_name))
                    .map((m) => {
                      const provider = byName.get(m.provider_name);
                      const reachable = Boolean(provider?.is_enabled && provider.configured && m.is_enabled);
                      return (
                        <tr key={m.id} className={cn(!m.is_enabled && "opacity-50")}>
                          <td className="px-4 py-2">
                            <div className="text-fg">{m.display_name}</div>
                            <div className="font-mono text-[10px] text-subtle">{m.remote_id}</div>
                          </td>
                          <td className="px-3 py-2 text-muted">{provider?.display_name ?? m.provider_name}</td>
                          <td className="px-3 py-2 text-right tabular-nums text-muted">{tokens(m.context_window)}</td>
                          <td className="px-3 py-2 text-right tabular-nums text-muted">{perMillion(m.cost_per_input_token)}</td>
                          <td className="px-3 py-2 text-right tabular-nums text-muted">{perMillion(m.cost_per_output_token)}</td>
                          <td className="px-3 py-2 text-right tabular-nums text-muted">{duration(m.avg_latency_ms)}</td>
                          <td className="px-3 py-2 text-right tabular-nums text-muted">{(m.reliability * 100).toFixed(0)}%</td>
                          <td className="px-3 py-2">
                            <div className="flex flex-wrap gap-1">
                              {capabilityLeaders(m).map((c) => (
                                <Badge key={c} className="font-mono">
                                  {c}
                                </Badge>
                              ))}
                            </div>
                          </td>
                          <td className="px-4 py-2">
                            <span className="flex items-center gap-1.5">
                              <StatusBadge status={reachable ? "completed" : "pending"} label={reachable ? "Yes" : "No"} />
                              {m.listed_by_provider === true && (
                                <span title="Listed by the provider at the last verification">
                                  <CheckCircle2 className="size-3.5 text-ok" aria-label="Listed by the provider" />
                                </span>
                              )}
                              {m.listed_by_provider === false && <span className="text-[10px] text-warn">not listed</span>}
                            </span>
                          </td>
                        </tr>
                      );
                    })}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>

      <Dialog open={keyFor !== null} onOpenChange={(o) => !o && setKeyFor(null)}>
        <DialogContent title={`Credential for ${keyFor?.display_name ?? ""}`}>{keyFor && <KeyDialog provider={keyFor} onDone={() => setKeyFor(null)} />}</DialogContent>
      </Dialog>
    </div>
  );
}
