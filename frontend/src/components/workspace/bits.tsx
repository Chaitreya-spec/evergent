"use client";

import { ChevronRight, Copy } from "lucide-react";
import type { ReactNode } from "react";
import { toast } from "sonner";
import { cn } from "@/lib/cn";

export function Section({ title, children, action, className }: { title: string; children: ReactNode; action?: ReactNode; className?: string }) {
  return (
    <section className={cn("border-b border-line px-4 py-3.5", className)}>
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="text-[11px] font-medium uppercase tracking-wide text-subtle">{title}</h3>
        {action}
      </div>
      {children}
    </section>
  );
}

/** Label/value rows. */
export function Facts({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1.5 text-xs">
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-subtle">{label}</dt>
          <dd className="min-w-0 break-words text-fg tabular-nums">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Chips({ items, empty = "None" }: { items: string[]; empty?: string }) {
  if (items.length === 0) return <span className="text-xs text-subtle">{empty}</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {items.map((item) => (
        <span key={item} className="rounded border border-line bg-panel-2 px-1.5 py-0.5 font-mono text-[11px] text-muted">
          {item}
        </span>
      ))}
    </div>
  );
}

export function copy(text: string, what = "Copied") {
  navigator.clipboard?.writeText(text).then(
    () => toast.success(what),
    () => toast.error("Could not copy"),
  );
}

export function CodeBlock({ text, className, maxHeight = "max-h-80", copyable = true }: { text: string; className?: string; maxHeight?: string; copyable?: boolean }) {
  return (
    <div className={cn("group relative", className)}>
      <pre className={cn("overflow-auto rounded-md border border-line bg-bg p-3 font-mono text-[11.5px] leading-relaxed text-muted whitespace-pre-wrap break-words", maxHeight)}>{text}</pre>
      {copyable && (
        <button
          type="button"
          onClick={() => copy(text)}
          className="absolute right-2 top-2 rounded border border-line bg-panel-2 p-1 text-subtle opacity-0 transition-opacity hover:text-fg focus:opacity-100 group-hover:opacity-100"
          aria-label="Copy"
        >
          <Copy className="size-3.5" />
        </button>
      )}
    </div>
  );
}

/** A collapsible row: summary always visible, detail on demand. Native <details>, so it is keyboard accessible. */
export function Disclosure({ summary, children, defaultOpen }: { summary: ReactNode; children: ReactNode; defaultOpen?: boolean }) {
  return (
    <details className="group rounded-md border border-line bg-panel-2/40 open:bg-panel-2/70" open={defaultOpen}>
      <summary className="flex cursor-pointer list-none items-center gap-2 px-2.5 py-2 text-xs [&::-webkit-details-marker]:hidden">
        <ChevronRight className="size-3.5 shrink-0 text-subtle transition-transform group-open:rotate-90" />
        <div className="min-w-0 flex-1">{summary}</div>
      </summary>
      <div className="border-t border-line px-2.5 py-2">{children}</div>
    </details>
  );
}

export const json = (value: unknown) => JSON.stringify(value, null, 2);
