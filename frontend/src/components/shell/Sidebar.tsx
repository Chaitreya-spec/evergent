"use client";

import {
  Bot,
  ChartColumn,
  ChevronsUpDown,
  Cpu,
  FileCode2,
  FolderKanban,
  History,
  LogOut,
  MonitorSmartphone,
  Plus,
  Settings,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Kbd, Skeleton } from "@/components/ui/feedback";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { StatusDot } from "@/components/ui/status";
import { useActiveWorkflows, useMe, useProjects } from "@/lib/api/queries";
import { cn } from "@/lib/cn";
import { useSignOut } from "./useSignOut";
import { Wordmark } from "./Wordmark";

type NavItem = { href: string; label: string; icon: LucideIcon };

const NAV: NavItem[] = [
  { href: "/workflows", label: "Execution history", icon: History },
  { href: "/projects", label: "Projects", icon: FolderKanban },
  { href: "/agents", label: "Agent registry", icon: Bot },
  { href: "/models", label: "Models & providers", icon: Cpu },
  { href: "/files", label: "Files & artifacts", icon: FileCode2 },
  { href: "/usage", label: "Usage & cost", icon: ChartColumn },
  { href: "/settings", label: "Settings", icon: Settings },
];

function SectionTitle({ children, count }: { children: React.ReactNode; count?: number }) {
  return (
    <div className="flex items-center justify-between px-2 pb-1 pt-4 text-[11px] font-medium uppercase tracking-wide text-subtle">
      <span>{children}</span>
      {count !== undefined && count > 0 && <span className="tabular-nums">{count}</span>}
    </div>
  );
}

export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const search = useSearchParams();
  const active = useActiveWorkflows();
  const projects = useProjects();
  const me = useMe();
  const signOut = useSignOut();
  const activeItems = active.data?.items ?? [];

  return (
    <nav aria-label="Workspace" className="flex h-full flex-col bg-panel">
      <div className="px-4 pb-3 pt-4">
        <Link href="/" onClick={onNavigate} aria-label="Evergent home">
          <Wordmark />
        </Link>
      </div>

      <div className="px-3">
        <Button asChild variant="primary" className="w-full justify-between">
          <Link href="/" onClick={onNavigate}>
            <span className="flex items-center gap-1.5">
              <Plus /> New task
            </span>
            <Kbd>N</Kbd>
          </Link>
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
        <ul className="mt-3 space-y-0.5">
          {NAV.map(({ href, label, icon: Icon }) => {
            const exact = pathname === href || pathname.startsWith(`${href}/`);
            return (
              <li key={href}>
                <Link
                  href={href}
                  onClick={onNavigate}
                  aria-current={exact ? "page" : undefined}
                  className={cn(
                    "flex h-8 items-center gap-2.5 rounded-md px-2 text-sm text-muted transition-colors hover:bg-panel-3 hover:text-fg",
                    exact && "bg-panel-3 text-fg",
                  )}
                >
                  <Icon className="size-4 shrink-0" />
                  {label}
                </Link>
              </li>
            );
          })}
        </ul>

        <SectionTitle count={activeItems.length}>Active workflows</SectionTitle>
        {active.isPending ? (
          <div className="space-y-1.5 px-2">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-2/3" />
          </div>
        ) : active.isError ? (
          <p className="px-2 text-xs text-bad">Could not load.</p>
        ) : activeItems.length === 0 ? (
          <p className="px-2 text-xs text-subtle">Nothing running.</p>
        ) : (
          <ul className="space-y-0.5">
            {activeItems.map((w) => {
              const href = `/workflows/${w.id}`;
              return (
                <li key={w.id}>
                  <Link
                    href={href}
                    onClick={onNavigate}
                    aria-current={pathname === href ? "page" : undefined}
                    title={w.goal}
                    className={cn(
                      "flex h-7 items-center gap-2 rounded-md px-2 text-[13px] text-muted hover:bg-panel-3 hover:text-fg",
                      pathname === href && "bg-panel-3 text-fg",
                    )}
                  >
                    <StatusDot status={w.status} />
                    <span className="truncate">{w.goal}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}

        <SectionTitle>Projects</SectionTitle>
        {projects.isPending ? (
          <Skeleton className="mx-2 h-4" />
        ) : (projects.data ?? []).length === 0 ? (
          <Link href="/projects" onClick={onNavigate} className="block px-2 text-xs text-subtle hover:text-fg">
            Create a project →
          </Link>
        ) : (
          <ul className="space-y-0.5">
            {(projects.data ?? []).slice(0, 8).map((p) => {
              const selected = pathname === "/workflows" && search.get("project") === p.id;
              return (
                <li key={p.id}>
                  <Link
                    href={`/workflows?project=${p.id}`}
                    onClick={onNavigate}
                    className={cn(
                      "flex h-7 items-center gap-2 rounded-md px-2 text-[13px] text-muted hover:bg-panel-3 hover:text-fg",
                      selected && "bg-panel-3 text-fg",
                    )}
                  >
                    <FolderKanban className="size-3.5 shrink-0 text-subtle" />
                    <span className="truncate">{p.name}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <div className="border-t border-line p-2">
        <Menu>
          <MenuTrigger asChild>
            <button
              type="button"
              className="flex w-full items-center gap-2.5 rounded-md p-2 text-left hover:bg-panel-3"
              aria-label="Account menu"
            >
              <span className="grid size-7 shrink-0 place-items-center rounded-full bg-panel-3 text-xs font-semibold uppercase text-fg ring-1 ring-line-strong">
                {(me.data?.full_name || me.data?.email || "?").charAt(0)}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm text-fg">{me.data?.full_name || me.data?.email || "…"}</span>
                <span className="block truncate text-[11px] capitalize text-subtle">{me.data?.role ?? ""}</span>
              </span>
              <ChevronsUpDown className="size-4 text-subtle" />
            </button>
          </MenuTrigger>
          <MenuContent align="start" side="top" className="w-56">
            <MenuLabel className="truncate">{me.data?.email}</MenuLabel>
            <MenuSeparator />
            <MenuItem asChild>
              <Link href="/settings" onClick={onNavigate}>
                <Settings /> Settings
              </Link>
            </MenuItem>
            <MenuItem onSelect={() => void signOut(false)}>
              <LogOut /> Sign out
            </MenuItem>
            <MenuItem onSelect={() => void signOut(true)}>
              <MonitorSmartphone /> Sign out everywhere
            </MenuItem>
          </MenuContent>
        </Menu>
      </div>
    </nav>
  );
}
