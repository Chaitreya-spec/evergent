"use client";

import { useMutation } from "@tanstack/react-query";
import { MailCheck, MonitorSmartphone, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { Notice, PageHeader, Panel, PanelHeader, Skeleton } from "@/components/ui/feedback";
import { StatusBadge } from "@/components/ui/status";
import { useSignOut } from "@/components/shell/useSignOut";
import { Facts } from "@/components/workspace/bits";
import { ApiKeysPanel } from "./ApiKeysPanel";
import { api, errorText } from "@/lib/api/client";
import { useAuditLogs, useHealth, useMe, useReadiness } from "@/lib/api/queries";
import type { Accepted } from "@/lib/api/types";
import { clock, humanize, timeAgo } from "@/lib/format";
import { parseTime } from "@/lib/time";

export function SettingsPage() {
  const me = useMe();
  const audit = useAuditLogs();
  const health = useHealth();
  const ready = useReadiness();
  const signOut = useSignOut();
  const [confirmAll, setConfirmAll] = useState(false);
  const verify = useMutation({ mutationFn: () => api<Accepted>("/auth/verify-email/request", { method: "POST" }) });

  const user = me.data;
  return (
    <div className="h-full overflow-y-auto">
      <PageHeader title="Settings" description="Your account, sessions, API keys and security log." />
      <div className="grid gap-5 px-4 py-4 sm:px-6 xl:grid-cols-2">
        <Panel>
          <PanelHeader title="Account" />
          <div className="p-4">
            {!user ? (
              <Skeleton className="h-24" />
            ) : (
              <>
                <Facts
                  rows={[
                    ["Email", user.email],
                    ["Name", user.full_name || "—"],
                    ["Role", humanize(user.role)],
                    ["Member since", new Date(parseTime(user.created_at)).toLocaleDateString()],
                    [
                      "Email status",
                      user.email_verified_at ? (
                        <StatusBadge key="v" status="completed" label="Verified" />
                      ) : (
                        <StatusBadge key="v" status="paused" label="Not verified" />
                      ),
                    ],
                  ]}
                />
                {!user.email_verified_at && (
                  <div className="mt-4 space-y-3">
                    <Button size="sm" onClick={() => verify.mutate()} disabled={verify.isPending || verify.isSuccess}>
                      <MailCheck /> Send confirmation email
                    </Button>
                    {verify.isSuccess &&
                      (verify.data.email_delivery ? (
                        <Notice>{verify.data.message}</Notice>
                      ) : (
                        <Notice tone="warn" title="Email is not set up on this server">
                          No message was sent. An administrator needs to configure SMTP.
                        </Notice>
                      ))}
                    {verify.isError && <Notice tone="bad">{errorText(verify.error)}</Notice>}
                  </div>
                )}
              </>
            )}
          </div>
        </Panel>

        <Panel>
          <PanelHeader title="Sessions" description="Each sign-in is its own session. Signing out ends it on the server at once." />
          <div className="space-y-3 p-4 text-sm text-muted">
            <p>Lost a device, or signed in somewhere you should not have? End every session, including this one.</p>
            <Button variant="danger" onClick={() => setConfirmAll(true)}>
              <MonitorSmartphone /> Sign out everywhere
            </Button>
          </div>
        </Panel>

        <ApiKeysPanel readOnly={user?.role === "viewer"} />

        <Panel className="xl:col-span-2">
          <PanelHeader title="Security log" description="Sign-ins, failed attempts, key changes and other sensitive actions on your account." />
          {audit.isPending ? (
            <div className="p-4">
              <Skeleton className="h-5" />
            </div>
          ) : audit.isError ? (
            <div className="p-4">
              <Notice tone="bad">{audit.error.message}</Notice>
            </div>
          ) : audit.data.length === 0 ? (
            <p className="px-4 py-6 text-center text-xs text-subtle">Nothing recorded yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[40rem] text-left text-xs">
                <thead className="border-b border-line text-[11px] uppercase tracking-wide text-subtle">
                  <tr>
                    <th scope="col" className="px-4 py-2 font-medium">Action</th>
                    <th scope="col" className="px-3 py-2 font-medium">Target</th>
                    <th scope="col" className="px-3 py-2 font-medium">Address</th>
                    <th scope="col" className="px-4 py-2 text-right font-medium">When</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {audit.data.map((entry) => (
                    <tr key={entry.id}>
                      <td className="px-4 py-2">
                        <span className={entry.action.includes("failed") || entry.action.includes("reused") ? "font-mono text-warn" : "font-mono text-fg"}>{entry.action}</span>
                      </td>
                      <td className="px-3 py-2 text-muted">{entry.target_type ? `${entry.target_type} ${String(entry.target_id ?? "").slice(0, 12)}` : "—"}</td>
                      <td className="px-3 py-2 font-mono text-subtle">{entry.ip ?? "—"}</td>
                      <td className="px-4 py-2 text-right text-subtle" title={new Date(parseTime(entry.created_at)).toLocaleString()}>
                        {timeAgo(entry.created_at)} · {clock(entry.created_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        <Panel className="xl:col-span-2">
          <PanelHeader title="System" />
          <div className="flex flex-wrap gap-x-8 gap-y-3 p-4 text-xs">
            <span className="flex items-center gap-2 text-muted">
              <ShieldCheck className="size-4 text-subtle" /> Backend {health.data ? `v${health.data.version} (${health.data.environment})` : "…"}
            </span>
            <span className="text-muted">Database: {ready.data ? (ready.data.database ? "reachable" : "down") : "…"}</span>
            <span className="text-muted">Event bus: {ready.data ? (ready.data.redis ? "Redis" : "in-process fallback") : "…"}</span>
            <span className="text-muted">Providers: {ready.data ? ready.data.providers.join(", ") || "none" : "…"}</span>
          </div>
        </Panel>
      </div>

      <Confirm open={confirmAll} onOpenChange={setConfirmAll} title="Sign out everywhere?" confirmLabel="Sign out everywhere" onConfirm={() => void signOut(true)}>
        Every browser and device signed in to your account is signed out, including this one.
      </Confirm>
    </div>
  );
}
