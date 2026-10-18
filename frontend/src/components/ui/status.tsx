import { cn } from "@/lib/cn";
import { statusInfo, type Tone } from "@/lib/workflow/status";

export const toneText: Record<Tone, string> = {
  idle: "text-idle",
  active: "text-active",
  ok: "text-ok",
  warn: "text-warn",
  bad: "text-bad",
  muted: "text-subtle",
};

export const toneBg: Record<Tone, string> = {
  idle: "bg-idle-soft",
  active: "bg-active-soft",
  ok: "bg-ok-soft",
  warn: "bg-warn-soft",
  bad: "bg-bad-soft",
  muted: "bg-idle-soft",
};

const toneDot: Record<Tone, string> = {
  idle: "bg-idle",
  active: "bg-active animate-pulse-dot",
  ok: "bg-ok",
  warn: "bg-warn",
  bad: "bg-bad",
  muted: "bg-subtle",
};

export function StatusDot({ status, className }: { status: string; className?: string }) {
  const { tone, label } = statusInfo(status);
  return (
    <span
      role="img"
      aria-label={label}
      className={cn("inline-block size-2 shrink-0 rounded-full", toneDot[tone], className)}
    />
  );
}

/** A status as a small pill: dot plus words. Never colour alone. */
export function StatusBadge({
  status,
  label,
  className,
}: {
  status: string;
  label?: string;
  className?: string;
}) {
  const info = statusInfo(status);
  return (
    <span
      className={cn(
        "inline-flex h-5 items-center gap-1.5 rounded-full px-2 text-[11px] font-medium",
        toneBg[info.tone],
        toneText[info.tone],
        className,
      )}
    >
      <StatusDot status={status} className="size-1.5" />
      {label ?? info.label}
    </span>
  );
}

export function Badge({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex h-5 items-center rounded border border-line bg-panel-2 px-1.5 text-[11px] text-muted",
        className,
      )}
    >
      {children}
    </span>
  );
}
