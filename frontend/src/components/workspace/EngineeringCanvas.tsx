"use client";

import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  MiniMap,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Maximize } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { Tooltip } from "@/components/ui/tooltip";
import type { Graph, StatusFilter } from "@/lib/workflow/graph";
import { lineage, matchesFilter, NODE_WIDTH, STAGE_HEIGHT, TASK_HEIGHT } from "@/lib/workflow/graph";
import { nodeTypes, type StageFlowNode, type TaskFlowNode } from "./nodes";

const FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "active", label: "Active" },
  { value: "attention", label: "Needs attention" },
  { value: "waiting", label: "Waiting" },
  { value: "done", label: "Done" },
];

// Fit the graph, but never below a zoom at which node text stays legible;
// a large plan is then centred and the rest is a pan (or the minimap) away.
const FIT = { padding: 0.1, minZoom: 0.6, maxZoom: 1, duration: 250 };

const EDGE_COLOR = {
  idle: "var(--line-strong)",
  active: "var(--active)",
  failed: "var(--bad)",
  selected: "var(--accent)",
};

type Props = {
  graph: Graph;
  selected: string | null;
  onSelect: (id: string | null) => void;
  statusFilter: StatusFilter;
  onStatusFilter: (value: StatusFilter) => void;
  agentFilter: string | null;
  onAgentFilter: (value: string | null) => void;
  agentOptions: { value: string; label: string }[];
};

function CanvasInner({ graph, selected, onSelect, statusFilter, onStatusFilter, agentFilter, onAgentFilter, agentOptions }: Props) {
  const flow = useReactFlow();
  const nodeCount = graph.nodes.length;
  const fitted = useRef(0);

  // Refit when the shape changes (a plan arrives, tasks are added), not on
  // every status tick — that would yank the view from under the user.
  useEffect(() => {
    if (fitted.current === nodeCount) return;
    fitted.current = nodeCount;
    const id = requestAnimationFrame(() => void flow.fitView(FIT));
    return () => cancelAnimationFrame(id);
  }, [flow, nodeCount]);

  const related = useMemo(() => (selected ? lineage(selected, graph.edges) : null), [selected, graph.edges]);
  const filtering = statusFilter !== "all" || agentFilter !== null;

  const nodes = useMemo(
    () =>
      graph.nodes.map((node) => {
        const hiddenByFilter = filtering && !matchesFilter(node.data, statusFilter, agentFilter);
        const outsideLineage = related !== null && !related.has(node.id);
        const dimmed = hiddenByFilter || outsideLineage;
        return {
          id: node.id,
          type: node.type,
          position: node.position,
          // Fixed sizes, declared. Every live update builds new node objects;
          // without known dimensions React Flow hides a node until it is
          // measured again, which for an unchanged size never happens.
          width: NODE_WIDTH,
          height: node.type === "task" ? TASK_HEIGHT : STAGE_HEIGHT,
          data: node.data,
          selected: node.id === selected,
          draggable: false,
          connectable: false,
          className: dimmed ? "dimmed" : undefined,
        } as StageFlowNode | TaskFlowNode;
      }),
    [graph.nodes, filtering, statusFilter, agentFilter, related, selected],
  );

  const edges: Edge[] = useMemo(
    () =>
      graph.edges.map((edge) => {
        const inLineage = related !== null && related.has(edge.source) && related.has(edge.target);
        const color = edge.failed ? EDGE_COLOR.failed : inLineage ? EDGE_COLOR.selected : edge.active ? EDGE_COLOR.active : EDGE_COLOR.idle;
        return {
          id: edge.id,
          source: edge.source,
          target: edge.target,
          sourceHandle: edge.sourceHandle,
          targetHandle: edge.targetHandle,
          type: "smoothstep",
          animated: edge.active,
          focusable: false,
          className: related !== null && !inLineage ? "dimmed" : undefined,
          style: { stroke: color, strokeWidth: inLineage || edge.active ? 1.75 : 1.25 },
          markerEnd: { type: MarkerType.ArrowClosed, color, width: 14, height: 14 },
        };
      }),
    [graph.edges, related],
  );

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodeClick={(_, node) => onSelect(node.id)}
      onPaneClick={() => onSelect(null)}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable
      nodesFocusable
      edgesFocusable={false}
      minZoom={0.2}
      maxZoom={1.75}
      fitView
      fitViewOptions={FIT}
      proOptions={{ hideAttribution: true }}
      aria-label="Workflow graph"
      onKeyDown={(event) => {
        // Enter on a focused node selects it, like a click.
        if (event.key === "Enter") {
          const id = (event.target as HTMLElement).closest("[data-id]")?.getAttribute("data-id");
          if (id) onSelect(id);
        }
      }}
    >
      <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#22252b" />
      <Controls showInteractive={false} position="bottom-left" />
      <Panel position="bottom-center" className="!mb-3 hidden rounded-md border border-line bg-panel/90 px-2.5 py-1 text-[11px] text-subtle backdrop-blur md:block">
        Select a node for details · <kbd className="font-mono">j</kbd>/<kbd className="font-mono">k</kbd> step · <kbd className="font-mono">f</kbd> fit ·{" "}
        <kbd className="font-mono">Esc</kbd> clear
      </Panel>
      <MiniMap
        style={{ width: 168, height: 104 }}
        position="bottom-right"
        pannable
        zoomable
        nodeColor={(n) => {
          const status = (n.data as { status?: string }).status;
          if (status === "failed") return "#f07070";
          if (status === "completed") return "#2e6b50";
          if (status === "running" || status === "retrying") return "#5ab0ff";
          return "#30343b";
        }}
        maskColor="rgba(11,12,14,0.72)"
        className="!hidden md:!block"
      />
      <Panel position="top-left" className="!m-3 flex flex-wrap items-center gap-2">
        <div role="group" aria-label="Filter by status" className="flex rounded-md border border-line bg-panel/95 p-0.5 backdrop-blur">
          {FILTERS.map((f) => (
            <button
              key={f.value}
              type="button"
              aria-pressed={statusFilter === f.value}
              onClick={() => onStatusFilter(f.value)}
              className={`h-6 rounded px-2 text-[11px] font-medium transition-colors ${statusFilter === f.value ? "bg-panel-3 text-fg" : "text-subtle hover:text-fg"}`}
            >
              {f.label}
            </button>
          ))}
        </div>
        {agentOptions.length > 1 && (
          <div className="w-44">
            <Select
              aria-label="Filter by agent"
              value={agentFilter ?? "__all"}
              onValueChange={(v) => onAgentFilter(v === "__all" ? null : v)}
              options={[{ value: "__all", label: "All agents" }, ...agentOptions]}
              className="h-7 bg-panel/95 text-xs"
            />
          </div>
        )}
        <Tooltip content="Fit to view (F)">
          <Button size="icon-sm" variant="secondary" className="bg-panel/95" aria-label="Fit to view" onClick={() => void flow.fitView(FIT)}>
            <Maximize />
          </Button>
        </Tooltip>
      </Panel>
    </ReactFlow>
  );
}

export function EngineeringCanvas(props: Props) {
  return (
    <ReactFlowProvider>
      <CanvasInner {...props} />
    </ReactFlowProvider>
  );
}

/** Lets the page's keyboard shortcuts drive the canvas. */
export function useCanvasShortcuts({
  order,
  selected,
  onSelect,
}: {
  order: string[];
  selected: string | null;
  onSelect: (id: string | null) => void;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (target && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))) return;
      if (event.key === "Escape" && selected) {
        onSelect(null);
      } else if (event.key === "j" || event.key === "k") {
        if (order.length === 0) return;
        const index = selected ? order.indexOf(selected) : -1;
        const next = event.key === "j" ? (index + 1) % order.length : (index - 1 + order.length) % order.length;
        onSelect(order[next] ?? null);
      } else if (event.key === "f") {
        document.querySelector<HTMLButtonElement>('button[aria-label="Fit to view"]')?.click();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [order, selected, onSelect]);
}
