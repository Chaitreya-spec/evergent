"use client";

import { ChartColumn } from "lucide-react";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip as ChartTooltip, XAxis, YAxis } from "recharts";
import { EmptyState, Notice, PageHeader, Panel, PanelHeader, Skeleton } from "@/components/ui/feedback";
import { Segmented } from "@/components/ui/segmented";
import { Select } from "@/components/ui/select";
import { useProjects, useUsage } from "@/lib/api/queries";
import type { UsageDay } from "@/lib/api/types";
import { cn } from "@/lib/cn";
import { money, tokens } from "@/lib/format";

/** Every day in the window, including the quiet ones, so the chart's x-axis is honest. */
export function fillDays(daily: UsageDay[], days: number, today = new Date()): UsageDay[] {
  const byDay = new Map(daily.map((d) => [d.day, d]));
  const out: UsageDay[] = [];
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - i));
    const key = d.toISOString().slice(0, 10);
    out.push(byDay.get(key) ?? { day: key, cost_usd: 0, tokens: 0, calls: 0 });
  }
  return out;
}

function Breakdown({ title, data }: { title: string; data: Record<string, number> }) {
  const rows = Object.entries(data).sort((a, b) => b[1] - a[1]);
  const max = Math.max(0.000001, ...rows.map((r) => r[1]));
  return (
    <Panel>
      <PanelHeader title={title} />
      {rows.length === 0 ? (
        <p className="px-4 py-6 text-center text-xs text-subtle">Nothing in this window.</p>
      ) : (
        <ul className="space-y-2.5 p-4">
          {rows.map(([name, cost]) => (
            <li key={name}>
              <div className="flex justify-between gap-2 text-xs">
                <span className="truncate font-mono text-muted">{name}</span>
                <span className="tabular-nums text-fg">{money(cost)}</span>
              </div>
              <div className="mt-1 h-1.5 rounded-full bg-panel-3">
                <div className="h-full rounded-full bg-accent/70" style={{ width: `${(cost / max) * 100}%` }} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function Kpi({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: "warn" | "bad" }) {
  return (
    <div className="rounded-lg border border-line bg-panel px-4 py-3">
      <div className="text-[11px] uppercase tracking-wide text-subtle">{label}</div>
      <div className={cn("mt-1 text-xl font-semibold tabular-nums text-fg", tone === "warn" && "text-warn", tone === "bad" && "text-bad")}>{value}</div>
      {hint && <div className="mt-0.5 text-[11px] text-subtle">{hint}</div>}
    </div>
  );
}

export function UsagePage() {
  const [days, setDays] = useState<"7" | "30" | "90">("30");
  const [project, setProject] = useState<string | undefined>();
  const projects = useProjects();
  const usage = useUsage(Number(days), project);
  const u = usage.data;
  const series = u ? fillDays(u.daily ?? [], Number(days)) : [];
  const todayShare = u && u.daily_limit_usd ? (u.spent_today_usd ?? 0) / u.daily_limit_usd : 0;

  return (
    <div className="h-full overflow-y-auto">
      <PageHeader title="Usage & cost" description="Every model call is recorded. Costs are estimates from token counts and list prices, not provider invoices." />
      <div className="space-y-5 px-4 py-4 sm:px-6">
        <div className="flex flex-wrap items-end gap-3">
          <Segmented
            legend="Window"
            value={days}
            onChange={setDays}
            options={[
              { value: "7", label: "7 days" },
              { value: "30", label: "30 days" },
              { value: "90", label: "90 days" },
            ]}
            className="w-64"
          />
          <div className="w-56">
            <Select
              aria-label="Project"
              value={project ?? "__all"}
              onValueChange={(v) => setProject(v === "__all" ? undefined : v)}
              options={[{ value: "__all", label: "All projects" }, ...(projects.data ?? []).map((p) => ({ value: p.id, label: p.name }))]}
            />
          </div>
        </div>

        {usage.isPending ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-20" />
            ))}
          </div>
        ) : usage.isError ? (
          <Notice tone="bad">{usage.error.message}</Notice>
        ) : !u ? null : (
          <>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Kpi label="Estimated cost" value={money(u.total_cost_usd)} hint={`Last ${days} days`} />
              <Kpi label="Tokens" value={tokens(u.total_tokens)} hint={`${tokens(u.input_tokens)} in · ${tokens(u.output_tokens)} out`} />
              <Kpi label="Model calls" value={String(u.call_count)} hint={u.failed_calls ? `${u.failed_calls} failed` : "None failed"} tone={u.failed_calls ? "warn" : undefined} />
              <Kpi
                label="Spent today"
                value={money(u.spent_today_usd)}
                hint={u.daily_limit_usd ? `of ${money(u.daily_limit_usd)} daily limit (all projects)` : undefined}
                tone={todayShare >= 1 ? "bad" : todayShare >= 0.8 ? "warn" : undefined}
              />
            </div>

            <Panel>
              <PanelHeader title="Daily cost" description="UTC days. Hover a bar for tokens and calls." />
              {u.call_count === 0 ? (
                <EmptyState icon={ChartColumn} title="No model calls in this window" />
              ) : (
                <div className="h-64 px-2 py-4" role="img" aria-label={`Daily estimated cost over the last ${days} days`}>
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={series} margin={{ top: 4, right: 12, left: 4, bottom: 0 }}>
                      <CartesianGrid stroke="#22252b" vertical={false} />
                      <XAxis dataKey="day" tickFormatter={(d: string) => d.slice(5)} tick={{ fill: "#6d737d", fontSize: 11 }} axisLine={{ stroke: "#22252b" }} tickLine={false} minTickGap={16} />
                      <YAxis tickFormatter={(v: number) => `$${v < 1 ? v.toFixed(2) : v.toFixed(0)}`} tick={{ fill: "#6d737d", fontSize: 11 }} axisLine={false} tickLine={false} width={48} />
                      <ChartTooltip
                        cursor={{ fill: "rgba(141,151,255,0.06)" }}
                        contentStyle={{ background: "#1a1d22", border: "1px solid #30343b", borderRadius: 6, fontSize: 12 }}
                        labelStyle={{ color: "#e7e9ec" }}
                        formatter={(value, _name, item) => {
                          const d = item.payload as UsageDay;
                          return [`${money(Number(value))} · ${tokens(d.tokens)} tokens · ${d.calls} calls`, "Estimated"];
                        }}
                      />
                      <Bar dataKey="cost_usd" fill="#8d97ff" radius={[3, 3, 0, 0]} maxBarSize={28} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}
            </Panel>

            <div className="grid gap-4 lg:grid-cols-3">
              <Breakdown title="By model" data={u.by_model} />
              <Breakdown title="By provider" data={u.by_provider} />
              <Breakdown title="By role" data={u.by_role ?? {}} />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
