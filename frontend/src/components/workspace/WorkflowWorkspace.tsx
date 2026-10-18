"use client";

import * as RD from "@radix-ui/react-dialog";
import { useQueryClient } from "@tanstack/react-query";
import { PanelBottomClose, PanelBottomOpen, SearchX } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { EmptyState, Notice, Skeleton } from "@/components/ui/feedback";
import { ApiError } from "@/lib/api/client";
import {
  keys,
  useAgents,
  useExecutions,
  useModels,
  useProjects,
  useTasks,
  useWorkflow,
  useWorkflowArtifacts,
  useWorkflowReviews,
  useWorkflowToolCalls,
} from "@/lib/api/queries";
import type { LiveEvent } from "@/lib/api/types";
import { TERMINAL_WORKFLOW } from "@/lib/api/types";
import { cn } from "@/lib/cn";
import { useMediaQuery } from "@/lib/useMediaQuery";
import { appendLines, describeEvent, REFRESHES, type LogLine } from "@/lib/workflow/events";
import { applyActivity, buildGraph, type Activity, type StatusFilter } from "@/lib/workflow/graph";
import { useWorkflowStream } from "@/lib/workflow/useWorkflowStream";
import { Console, type ConsoleTab } from "./Console";
import { EngineeringCanvas, useCanvasShortcuts } from "./EngineeringCanvas";
import { Inspector } from "./Inspector";
import { ApprovalBanner, WorkflowHeader } from "./WorkflowHeader";

type RefreshGroup = keyof typeof REFRESHES;

const MIN_CONSOLE = 120;
const COLLAPSED_CONSOLE = 37;

export function WorkflowWorkspace({ id }: { id: string }) {
  const queries = useQueryClient();
  const desktop = useMediaQuery("(min-width: 1024px)");

  // --- live events --------------------------------------------------------
  const [log, setLog] = useState<LogLine[]>([]);
  const [activity, setActivity] = useState<Activity>({});
  const [generation, setGeneration] = useState(0);
  const batch = useRef<{ lines: LogLine[]; dirty: Set<RefreshGroup>; timer: ReturnType<typeof setTimeout> | null }>({
    lines: [],
    dirty: new Set(),
    timer: null,
  });

  const flush = useCallback(() => {
    const { lines, dirty } = batch.current;
    batch.current = { lines: [], dirty: new Set(), timer: null };
    if (lines.length) setLog((current) => appendLines(current, lines));
    const map: Record<RefreshGroup, readonly unknown[]> = {
      workflow: keys.workflow(id),
      tasks: keys.tasks(id),
      artifacts: keys.artifacts(id),
      toolCalls: keys.toolCalls(id),
      reviews: keys.reviews(id),
      executions: keys.executions(id),
    };
    for (const group of dirty) void queries.invalidateQueries({ queryKey: map[group], exact: true });
    if (dirty.has("workflow")) void queries.invalidateQueries({ queryKey: keys.allWorkflows });
  }, [id, queries]);

  // Events arrive in bursts (a replay can be hundreds at once), so lines and
  // refetches are batched rather than applied one by one.
  const onEvent = useCallback(
    (event: LiveEvent) => {
      const line = describeEvent(event);
      if (line) batch.current.lines.push(line);
      for (const group of Object.keys(REFRESHES) as RefreshGroup[]) {
        if (REFRESHES[group].has(event.event)) batch.current.dirty.add(group);
      }
      setActivity((current) => applyActivity(current, event));
      // Half a second: live enough to watch, and a busy run costs a few
      // refetches a second rather than one per event.
      if (!batch.current.timer) batch.current.timer = setTimeout(flush, 500);
    },
    [flush],
  );
  useEffect(() => () => {
    if (batch.current.timer) clearTimeout(batch.current.timer);
  }, []);

  const stream = useWorkflowStream(id, onEvent, generation);
  // Without a live stream, poll — the page must never sit on stale state.
  const poll = stream !== "live";
  const workflow = useWorkflow(id, {
    refetchInterval: (q) => (poll && q.state.data && !TERMINAL_WORKFLOW.has(q.state.data.status) ? 4000 : false),
  });
  const active = workflow.data ? !TERMINAL_WORKFLOW.has(workflow.data.status) : false;
  const tasks = useTasks(id, { refetchInterval: poll && active ? 4000 : false });
  const agents = useAgents();
  const models = useModels();
  const projects = useProjects();
  const executions = useExecutions(id);
  const reviews = useWorkflowReviews(id);
  const toolCalls = useWorkflowToolCalls(id);
  const artifacts = useWorkflowArtifacts(id);

  // --- view state ----------------------------------------------------------
  const [selected, setSelected] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [agentFilter, setAgentFilter] = useState<string | null>(null);
  const [tab, setTab] = useState<ConsoleTab>("logs");
  const [activeFile, setActiveFile] = useState<string | null>(null);
  const [consoleOpen, setConsoleOpen] = useState(true);
  const [consoleHeight, setConsoleHeight] = useState(260);
  const column = useRef<HTMLDivElement>(null);

  const graph = useMemo(
    () =>
      workflow.data && tasks.data
        ? buildGraph({
            workflow: workflow.data,
            tasks: tasks.data,
            agents: agents.data,
            models: models.data,
            executions: executions.data,
            reviews: reviews.data,
            toolCalls: toolCalls.data,
            activity,
          })
        : null,
    [workflow.data, tasks.data, agents.data, models.data, executions.data, reviews.data, toolCalls.data, activity],
  );
  const order = useMemo(
    () => (graph ? [...graph.nodes].sort((a, b) => a.position.x - b.position.x || a.position.y - b.position.y).map((n) => n.id) : []),
    [graph],
  );
  useCanvasShortcuts({ order, selected, onSelect: setSelected });

  const selectedNode = graph?.nodes.find((n) => n.id === selected) ?? null;
  const selectedTask = selectedNode?.data.kind === "task" ? selectedNode.data.task : null;
  const agentOptions = useMemo(() => {
    const names = [...new Set((tasks.data ?? []).map((t) => t.agent_name).filter((n): n is string => Boolean(n)))];
    return names.map((n) => ({ value: n, label: agents.data?.find((a) => a.name === n)?.display_name ?? n }));
  }, [tasks.data, agents.data]);

  const reopenStream = useCallback(() => {
    if (stream === "ended") setGeneration((g) => g + 1);
  }, [stream]);

  const openFile = useCallback((artifactId: string) => {
    setActiveFile(artifactId);
    setTab("files");
    setConsoleOpen(true);
  }, []);

  // --- console resizing ------------------------------------------------------
  const startResize = (event: React.PointerEvent) => {
    event.preventDefault();
    const bottom = column.current?.getBoundingClientRect().bottom ?? window.innerHeight;
    const max = (column.current?.getBoundingClientRect().height ?? window.innerHeight) - 160;
    const move = (e: PointerEvent) => setConsoleHeight(Math.max(MIN_CONSOLE, Math.min(max, bottom - e.clientY)));
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    setConsoleOpen(true);
  };

  // --- states ------------------------------------------------------------------
  if (workflow.isError) {
    const missing = workflow.error instanceof ApiError && workflow.error.status === 404;
    return (
      <EmptyState icon={SearchX} title={missing ? "Workflow not found" : "Could not load this workflow"} className="h-full">
        {missing ? "It may have been deleted, or it belongs to another account." : workflow.error.message}
        <div className="mt-4">
          <Button asChild>
            <Link href="/workflows">All workflows</Link>
          </Button>
        </div>
      </EmptyState>
    );
  }
  if (!workflow.data || !tasks.data || !graph) {
    return (
      <div className="flex h-full flex-col" aria-busy="true">
        <div className="border-b border-line px-4 py-3">
          <Skeleton className="h-4 w-1/2" />
          <Skeleton className="mt-2 h-3 w-1/3" />
        </div>
        <div className="flex flex-1 items-center justify-center gap-6 p-8">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-24 w-56" />
          ))}
        </div>
      </div>
    );
  }

  const wf = workflow.data;
  const projectName = projects.data?.find((p) => p.id === wf.project_id)?.name;
  const inspector = (
    <Inspector
      node={selectedNode}
      onSelect={setSelected}
      onOpenFile={openFile}
      workflow={wf}
      tasks={tasks.data}
      agents={agents.data ?? []}
      models={models.data ?? []}
      executions={executions.data ?? []}
      reviews={reviews.data ?? []}
      toolCalls={toolCalls.data ?? []}
      artifacts={artifacts.data ?? []}
    />
  );

  return (
    <div className="flex h-full flex-col">
      <WorkflowHeader workflow={wf} tasks={tasks.data} projectName={projectName} onStarted={reopenStream} />
      <ApprovalBanner workflow={wf} tasks={tasks.data} onStarted={reopenStream} />
      {(wf.status === "failed" || (wf.status === "paused" && wf.error)) && wf.error && (
        <div className="border-b border-line bg-panel px-3 py-2 sm:px-4">
          <Notice
            tone={wf.status === "failed" ? "bad" : "warn"}
            action={
              <Button size="sm" variant="ghost" onClick={() => { setTab("errors"); setConsoleOpen(true); }}>
                Details
              </Button>
            }
          >
            <span className="line-clamp-2">{wf.error}</span>
          </Notice>
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        <div ref={column} className="flex min-w-0 flex-1 flex-col">
          <div className="relative min-h-0 flex-1">
            <EngineeringCanvas
              graph={graph}
              selected={selected}
              onSelect={setSelected}
              statusFilter={statusFilter}
              onStatusFilter={setStatusFilter}
              agentFilter={agentFilter}
              onAgentFilter={setAgentFilter}
              agentOptions={agentOptions}
            />
          </div>

          <div
            role="separator"
            aria-orientation="horizontal"
            aria-label="Resize console"
            aria-valuenow={consoleOpen ? consoleHeight : COLLAPSED_CONSOLE}
            aria-valuemin={MIN_CONSOLE}
            tabIndex={0}
            onPointerDown={startResize}
            onKeyDown={(e) => {
              if (e.key === "ArrowUp") setConsoleHeight((h) => h + 32);
              if (e.key === "ArrowDown") setConsoleHeight((h) => Math.max(MIN_CONSOLE, h - 32));
            }}
            className="group relative h-1.5 shrink-0 cursor-row-resize border-t border-line bg-panel hover:bg-accent/20 focus-visible:bg-accent/30"
          />
          <div
            style={{ height: consoleOpen ? consoleHeight : COLLAPSED_CONSOLE }}
            className="relative shrink-0 overflow-hidden bg-panel"
            onClickCapture={(e) => {
              if (!consoleOpen && (e.target as HTMLElement).closest('[role="tab"]')) setConsoleOpen(true);
            }}
          >
            <Console
              tab={tab}
              onTab={setTab}
              workflow={wf}
              tasks={tasks.data}
              log={log}
              stream={stream}
              toolCalls={toolCalls.data ?? []}
              executions={executions.data ?? []}
              artifacts={artifacts.data ?? []}
              selectedTask={selectedTask}
              activeFile={activeFile}
              onActiveFile={setActiveFile}
            />
            <Button
              size="icon-sm"
              variant="ghost"
              className="absolute right-2 top-1"
              aria-label={consoleOpen ? "Collapse console" : "Expand console"}
              aria-expanded={consoleOpen}
              onClick={() => setConsoleOpen((o) => !o)}
            >
              {consoleOpen ? <PanelBottomClose /> : <PanelBottomOpen />}
            </Button>
          </div>
        </div>

        {/* The canvas gets the full width until there is something to inspect. */}
        {desktop && selectedNode && (
          <aside aria-label="Inspector" className={cn("w-[22rem] shrink-0 border-l border-line bg-panel xl:w-96")}>
            {inspector}
          </aside>
        )}
      </div>

      {!desktop && (
        <RD.Root open={selected !== null} onOpenChange={(open) => !open && setSelected(null)}>
          <RD.Portal>
            <RD.Overlay className="fixed inset-0 z-40 bg-black/50" />
            <RD.Content className="fixed inset-x-0 bottom-0 z-50 h-[80dvh] rounded-t-xl border-t border-line-strong bg-panel focus:outline-none">
              <RD.Title className="sr-only">Details</RD.Title>
              <RD.Description className="sr-only">Details of the selected node</RD.Description>
              {inspector}
            </RD.Content>
          </RD.Portal>
        </RD.Root>
      )}
    </div>
  );
}
