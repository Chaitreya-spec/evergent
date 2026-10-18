"use client";

import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import {
  AlertTriangle,
  Brain,
  CheckCheck,
  Combine,
  ListTree,
  MessageSquareText,
  RotateCcw,
  Route,
  type LucideIcon,
} from "lucide-react";
import { memo } from "react";
import { StatusBadge, toneText } from "@/components/ui/status";
import { cn } from "@/lib/cn";
import { duration, money, tokens } from "@/lib/format";
import { NODE_WIDTH, STAGE_HEIGHT, TASK_HEIGHT, type StageId, type StageNodeData, type TaskNodeData } from "@/lib/workflow/graph";
import { statusInfo, type Tone } from "@/lib/workflow/status";

export type ViewFlags = { dimmed?: boolean };
export type StageFlowNode = Node<StageNodeData & ViewFlags, "stage">;
export type TaskFlowNode = Node<TaskNodeData & ViewFlags, "task">;

const STAGE_ICON: Record<StageId, LucideIcon> = {
  request: MessageSquareText,
  planner: Brain,
  decomposition: ListTree,
  router: Route,
  review: CheckCheck,
  integration: Combine,
};

const toneBorder: Record<Tone, string> = {
  idle: "before:bg-idle/50",
  active: "before:bg-active",
  ok: "before:bg-ok",
  warn: "before:bg-warn",
  bad: "before:bg-bad",
  muted: "before:bg-subtle/40",
};

const handleClass = "!h-2 !w-2 !min-w-0 !border-0 !bg-line-strong";

function Shell({
  status,
  selected,
  height,
  children,
  label,
}: {
  status: string;
  selected: boolean;
  height: number;
  children: React.ReactNode;
  label: string;
}) {
  const { tone } = statusInfo(status);
  return (
    <div
      aria-label={label}
      style={{ width: NODE_WIDTH, height }}
      className={cn(
        "relative flex flex-col overflow-hidden rounded-lg border bg-panel-2 pl-3.5 pr-3 py-2.5 text-left shadow-[0_1px_0_rgba(255,255,255,0.03)_inset] transition-[border-color,box-shadow]",
        "before:absolute before:inset-y-0 before:left-0 before:w-[3px]",
        toneBorder[tone],
        selected ? "border-accent shadow-[0_0_0_1px_var(--accent)]" : "border-line hover:border-line-strong",
        tone === "active" && !selected && "border-active/35",
      )}
    >
      {children}
    </div>
  );
}

export const StageNode = memo(function StageNode({ data, selected }: NodeProps<StageFlowNode>) {
  const Icon = STAGE_ICON[data.stage];
  return (
    <>
      {/* Side handles first: edges that name no handle use the first one. */}
      <Handle type="target" position={Position.Left} isConnectable={false} className={handleClass} />
      <Handle type="target" position={Position.Top} id="top" isConnectable={false} className={handleClass} />
      <Shell status={data.status} selected={selected} height={STAGE_HEIGHT} label={`${data.title}: ${data.statusLabel ?? statusInfo(data.status).label}`}>
        <div className="flex items-center justify-between gap-2">
          <span className="flex min-w-0 items-center gap-1.5 text-[13px] font-semibold text-fg">
            <Icon className={cn("size-3.5 shrink-0", toneText[statusInfo(data.status).tone])} />
            <span className="truncate">{data.title}</span>
          </span>
          <StatusBadge status={data.status} label={data.statusLabel} />
        </div>
        <p className="mt-1 line-clamp-2 text-xs leading-snug text-muted">{data.subtitle}</p>
        <div className="mt-auto flex items-end justify-between gap-2 pt-1.5">
          <div className="grid min-w-0 flex-1 grid-cols-2 gap-x-3">
            {data.metrics.map((m) => (
              <div key={m.label} className="min-w-0">
                <div className="truncate text-[10px] uppercase tracking-wide text-subtle">{m.label}</div>
                <div className="truncate text-[11px] capitalize tabular-nums text-fg">{m.value}</div>
              </div>
            ))}
          </div>
          {data.model && (
            <span className="max-w-[38%] shrink-0 truncate font-mono text-[10px] text-subtle" title={`${data.model.displayName} · ${data.model.provider}`}>
              {data.model.displayName}
            </span>
          )}
        </div>
      </Shell>
      <Handle type="source" position={Position.Right} isConnectable={false} className={handleClass} />
      <Handle type="source" position={Position.Bottom} id="bottom" isConnectable={false} className={handleClass} />
    </>
  );
});

export const TaskNode = memo(function TaskNode({ data, selected }: NodeProps<TaskFlowNode>) {
  const { task } = data;
  const failed = task.status === "failed";
  return (
    <>
      <Handle type="target" position={Position.Left} isConnectable={false} className={handleClass} />
      <Shell status={data.status} selected={selected} height={TASK_HEIGHT} label={`${task.title}, ${data.agentLabel}: ${statusInfo(task.status).label}`}>
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-[11px] font-medium uppercase tracking-wide text-subtle" title={data.agentLabel}>
            {data.agentLabel}
          </span>
          <StatusBadge status={data.status} />
        </div>
        <div className="mt-1 line-clamp-2 text-[13px] font-medium leading-snug text-fg" title={task.title}>
          {task.title}
        </div>
        <div className="mt-1 flex items-center gap-1.5 truncate text-[11px] text-muted">
          {data.model ? (
            <span className="truncate font-mono" title={`${data.model.displayName} via ${data.model.provider}`}>
              {data.model.displayName}
              <span className="text-subtle"> · {data.model.provider}</span>
            </span>
          ) : (
            <span className="text-subtle">No model yet</span>
          )}
        </div>
        <div className="mt-auto flex items-center justify-between gap-2 pt-1 text-[11px] tabular-nums text-subtle">
          {data.activity ? (
            <span className="truncate text-active">{data.activity}</span>
          ) : failed && task.error ? (
            <span className="flex min-w-0 items-center gap-1 text-bad" title={task.error}>
              <AlertTriangle className="size-3 shrink-0" />
              <span className="truncate">{task.error}</span>
            </span>
          ) : (
            <span>
              {money(task.cost_usd)} · {tokens(task.input_tokens + task.output_tokens)} tok
              {task.execution_time_ms ? ` · ${duration(task.execution_time_ms)}` : ""}
            </span>
          )}
          {task.retry_count > 0 && (
            <span className="flex shrink-0 items-center gap-0.5 text-warn" title={`${task.retry_count} retr${task.retry_count === 1 ? "y" : "ies"}`}>
              <RotateCcw className="size-3" />
              {task.retry_count}
            </span>
          )}
        </div>
      </Shell>
      <Handle type="source" position={Position.Right} isConnectable={false} className={handleClass} />
    </>
  );
});

export const nodeTypes = { stage: StageNode, task: TaskNode };
