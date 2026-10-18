import { cn } from "@/lib/cn";

/** The mark: three offset nodes joined into one — many agents, one result. */
export function Mark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden className={cn("size-6", className)}>
      <rect width="24" height="24" rx="6" fill="var(--panel-3)" stroke="var(--line-strong)" />
      <path d="M7 8.5 L12 12 L7 15.5 M12 12 H17" fill="none" stroke="var(--fg-subtle)" strokeWidth="1.4" strokeLinecap="round" />
      <circle cx="7" cy="8.5" r="1.9" fill="var(--fg-muted)" />
      <circle cx="7" cy="15.5" r="1.9" fill="var(--fg-muted)" />
      <circle cx="17" cy="12" r="2.3" fill="var(--accent)" />
    </svg>
  );
}

export function Wordmark({ className }: { className?: string }) {
  return (
    <div className={cn("flex items-center gap-2.5", className)}>
      <Mark />
      <div className="leading-tight">
        <div className="text-sm font-semibold tracking-tight text-fg">Evergent</div>
        <div className="text-[11px] text-subtle">AI Engineering Room</div>
      </div>
    </div>
  );
}
