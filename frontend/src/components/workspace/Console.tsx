"use client";

import {
  AlertOctagon,
  ArrowDown,
  Download,
  Eye,
  FileCode2,
  FileOutput,
  FlaskConical,
  ScrollText,
  SquareTerminal,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { EmptyState, Notice } from "@/components/ui/feedback";
import { Input } from "@/components/ui/form";
import { Select } from "@/components/ui/select";
import { StatusBadge } from "@/components/ui/status";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { Artifact, Execution, Task, Workflow, WorkflowToolCall } from "@/lib/api/types";
import { cn } from "@/lib/cn";
import { bytes, clock, duration } from "@/lib/format";
import type { LogLevel, LogLine } from "@/lib/workflow/events";
import { buildPreview } from "@/lib/workflow/preview";
import { collectProblems } from "@/lib/workflow/problems";
import type { StreamState } from "@/lib/workflow/useWorkflowStream";
import { CodeBlock, copy, Disclosure, json } from "./bits";

export type ConsoleTab = "logs" | "output" | "terminal" | "tests" | "errors" | "files";

const LEVEL_STYLE: Record<LogLevel, string> = {
  debug: "text-subtle",
  info: "text-muted",
  success: "text-ok",
  warn: "text-warn",
  error: "text-bad",
};

const LEVELS: Record<string, LogLevel[]> = {
  all: ["debug", "info", "success", "warn", "error"],
  info: ["info", "success", "warn", "error"],
  warn: ["warn", "error"],
  error: ["error"],
};

const STREAM_LABEL: Record<StreamState, { text: string; className: string }> = {
  connecting: { text: "Connecting", className: "text-subtle" },
  live: { text: "Live", className: "text-ok" },
  reconnecting: { text: "Reconnecting", className: "text-warn" },
  ended: { text: "Finished", className: "text-subtle" },
  off: { text: "Off", className: "text-subtle" },
};

function Logs({ log, stream, selectedKey }: { log: LogLine[]; stream: StreamState; selectedKey: string | null }) {
  const [level, setLevel] = useState("info");
  const [onlySelected, setOnlySelected] = useState(false);
  const [query, setQuery] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const [pinned, setPinned] = useState(true);

  const visible = useMemo(() => {
    const allowed = LEVELS[level] ?? LEVELS.all!;
    const q = query.trim().toLowerCase();
    return log.filter(
      (l) =>
        allowed.includes(l.level) &&
        (!onlySelected || !selectedKey || l.task === selectedKey) &&
        (!q || l.text.toLowerCase().includes(q) || l.task?.toLowerCase().includes(q)),
    );
  }, [log, level, onlySelected, selectedKey, query]);

  // Follow new lines while the view is at the bottom; stop following when
  // the user scrolls up to read.
  useEffect(() => {
    if (pinned && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [visible, pinned]);

  const status = STREAM_LABEL[stream];
  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-1.5">
        <span className={cn("flex items-center gap-1.5 text-[11px] font-medium", status.className)} role="status">
          <span className={cn("size-1.5 rounded-full bg-current", stream === "live" && "animate-pulse-dot")} />
          {status.text}
        </span>
        <div className="w-32">
          <Select
            aria-label="Log level"
            value={level}
            onValueChange={setLevel}
            className="h-7 text-xs"
            options={[
              { value: "all", label: "Everything" },
              { value: "info", label: "Info and up" },
              { value: "warn", label: "Warnings" },
              { value: "error", label: "Errors" },
            ]}
          />
        </div>
        <Input aria-label="Search logs" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search" className="h-7 w-40 text-xs" />
        <label className={cn("flex items-center gap-1.5 text-xs text-muted", !selectedKey && "opacity-50")}>
          <input type="checkbox" checked={onlySelected} disabled={!selectedKey} onChange={(e) => setOnlySelected(e.target.checked)} className="accent-[var(--accent)]" />
          Selected task only
        </label>
        <span className="ml-auto text-[11px] tabular-nums text-subtle">{visible.length} lines</span>
      </div>
      <div className="relative min-h-0 flex-1">
        <div
          ref={box}
          onScroll={(e) => {
            const el = e.currentTarget;
            setPinned(el.scrollHeight - el.scrollTop - el.clientHeight < 24);
          }}
          className="h-full overflow-y-auto py-1 font-mono text-[11.5px] leading-[1.6]"
          role="log"
          aria-live="polite"
          aria-relevant="additions"
        >
          {visible.length === 0 ? (
            <p className="px-3 py-2 text-subtle">{log.length === 0 ? "Events appear here as the workflow runs." : "No lines match."}</p>
          ) : (
            visible.map((l) => (
              <div key={l.key} className="flex gap-3 px-3 hover:bg-panel-2">
                <span className="shrink-0 tabular-nums text-subtle">{clock(l.at)}</span>
                <span className="w-10 shrink-0 text-subtle">{l.task ?? ""}</span>
                <span className={cn("min-w-0 break-words", LEVEL_STYLE[l.level])}>{l.text}</span>
              </div>
            ))
          )}
        </div>
        {!pinned && (
          <Button size="sm" className="absolute bottom-2 right-3" onClick={() => setPinned(true)}>
            <ArrowDown /> Latest
          </Button>
        )}
      </div>
    </div>
  );
}

function Outputs({ workflow, tasks, selected }: { workflow: Workflow; tasks: Task[]; selected: Task | null }) {
  if (selected) {
    return (
      <div className="h-full overflow-y-auto p-3">
        <div className="mb-2 text-xs text-muted">
          Output of <span className="font-mono text-fg">{selected.key}</span> — {selected.title}
        </div>
        {selected.output ? <CodeBlock text={selected.output} maxHeight="" /> : <p className="text-xs text-subtle">This task has no output yet.</p>}
      </div>
    );
  }
  const done = tasks.filter((t) => t.output);
  return (
    <div className="h-full space-y-2 overflow-y-auto p-3">
      {workflow.final_result && (
        <Disclosure defaultOpen summary={<span className="font-medium text-fg">Final result</span>}>
          <CodeBlock text={workflow.final_result} maxHeight="max-h-96" />
        </Disclosure>
      )}
      {done.length === 0 && !workflow.final_result ? (
        <p className="text-xs text-subtle">Task outputs appear here as tasks finish. Select a task to see only its output.</p>
      ) : (
        done.map((t) => (
          <Disclosure key={t.id} summary={<span><span className="font-mono text-subtle">{t.key}</span> <span className="text-fg">{t.title}</span></span>}>
            <CodeBlock text={t.output ?? ""} maxHeight="max-h-80" />
          </Disclosure>
        ))
      )}
    </div>
  );
}

function Tests({ calls }: { calls: WorkflowToolCall[] }) {
  const runs = calls.filter((c) => c.tool_name === "run_tests" || c.tool_name === "run_code");
  if (runs.length === 0) {
    return (
      <EmptyState icon={FlaskConical} title="No test runs yet">
        When a testing agent writes tests, they are executed in the sandbox and recorded here with their full output.
      </EmptyState>
    );
  }
  return (
    <ul className="h-full space-y-1.5 overflow-y-auto p-3">
      {runs.map((c) => {
        const result = (c.result ?? {}) as { exit_code?: number; output?: string; skipped?: boolean; stage?: string; timed_out?: boolean; stdout?: string; stderr?: string };
        const state = result.skipped ? "skipped" : c.succeeded ? "completed" : "failed";
        const label = result.skipped ? "Not run" : c.succeeded ? "Passed" : result.timed_out ? "Timed out" : "Failed";
        const args = c.arguments as { command?: string; runtime?: string };
        const output = result.output ?? [result.stdout, result.stderr].filter(Boolean).join("\n");
        return (
          <li key={c.id}>
            <Disclosure
              summary={
                <div className="flex items-center justify-between gap-2">
                  <span className="flex min-w-0 items-center gap-2">
                    <span className="font-mono text-subtle">{c.task_key}</span>
                    <span className="truncate font-mono text-fg">{args.command ?? c.tool_name}</span>
                  </span>
                  <span className="flex shrink-0 items-center gap-2 text-subtle tabular-nums">
                    {result.exit_code !== undefined && <span>exit {result.exit_code}</span>}
                    <span>{duration(c.duration_ms)}</span>
                    <StatusBadge status={state} label={label} />
                  </span>
                </div>
              }
            >
              {c.error && <p className="mb-2 text-xs text-bad">{c.error}</p>}
              {output ? <CodeBlock text={output} maxHeight="max-h-72" /> : <CodeBlock text={json(c.result)} maxHeight="max-h-72" />}
            </Disclosure>
          </li>
        );
      })}
    </ul>
  );
}

function Problems({ workflow, tasks, calls, executions }: { workflow: Workflow; tasks: Task[]; calls: WorkflowToolCall[]; executions: Execution[] }) {
  const problems = collectProblems(workflow, tasks, calls, executions);
  if (problems.length === 0) {
    return <EmptyState icon={AlertOctagon} title="No errors">Failed tasks, tool calls and model calls are listed here with their messages.</EmptyState>;
  }
  return (
    <ul className="h-full space-y-1.5 overflow-y-auto p-3">
      {problems.map((p) => (
        <li key={p.id} className="rounded-md border border-bad/20 bg-bad-soft/40 px-3 py-2 text-xs">
          <div className="flex items-center justify-between gap-2">
            <span className="font-medium text-fg">
              {p.taskKey && <span className="mr-1.5 font-mono text-subtle">{p.taskKey}</span>}
              {p.title}
            </span>
            <span className="shrink-0 text-subtle">
              {p.source}
              {p.at ? ` · ${clock(p.at)}` : ""}
            </span>
          </div>
          <p className="mt-1 whitespace-pre-wrap break-words text-muted">{p.detail}</p>
        </li>
      ))}
    </ul>
  );
}

function Files({ workflowId, artifacts, tasks, activeId, onActive }: { workflowId: string; artifacts: Artifact[]; tasks: Task[]; activeId: string | null; onActive: (id: string) => void }) {
  const [preview, setPreview] = useState(false);
  const active = artifacts.find((a) => a.id === activeId) ?? artifacts[0];
  const keyOf = new Map(tasks.map((t) => [t.id, t.key]));
  const isHtml = active ? /\.html?$/i.test(active.path) : false;
  const sorted = useMemo(() => [...artifacts].sort((a, b) => a.path.localeCompare(b.path)), [artifacts]);

  if (artifacts.length === 0) {
    return <EmptyState icon={FileCode2} title="No files yet">Files the agents write appear here, versioned, with the task that owns each.</EmptyState>;
  }
  return (
    <div className="flex h-full min-h-0 flex-col md:flex-row">
      <div className="flex max-h-40 shrink-0 flex-col border-b border-line md:max-h-none md:w-60 md:border-b-0 md:border-r">
        <div className="flex items-center justify-between px-3 py-1.5">
          <span className="text-[11px] text-subtle">{artifacts.length} files</span>
          <Button asChild size="sm" variant="ghost">
            <a href={`/api/v1/workflows/${workflowId}/archive`} download>
              <Download /> All (.zip)
            </a>
          </Button>
        </div>
        <ul className="min-h-0 flex-1 overflow-y-auto pb-2" role="listbox" aria-label="Generated files">
          {sorted.map((a) => (
            <li key={a.id} role="option" aria-selected={a.id === active?.id}>
              <button
                type="button"
                onClick={() => {
                  onActive(a.id);
                  setPreview(false);
                }}
                className={cn("flex w-full items-center gap-2 px-3 py-1 text-left text-xs hover:bg-panel-2", a.id === active?.id && "bg-panel-3")}
              >
                <FileCode2 className="size-3.5 shrink-0 text-subtle" />
                <span className="truncate font-mono text-fg">{a.path}</span>
                <span className="ml-auto shrink-0 text-subtle">{a.task_id ? keyOf.get(a.task_id) : ""}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
      {active && (
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-1.5">
            <span className="truncate font-mono text-xs text-fg">{active.path}</span>
            <span className="text-[11px] text-subtle">
              v{active.version} · {bytes(new Blob([active.content]).size)} · {active.content_type}
            </span>
            <div className="ml-auto flex gap-1">
              {isHtml && (
                <Button size="sm" variant={preview ? "primary" : "ghost"} onClick={() => setPreview((p) => !p)} aria-pressed={preview}>
                  <Eye /> Preview
                </Button>
              )}
              <Button size="sm" variant="ghost" onClick={() => copy(active.content, "File copied")}>
                Copy
              </Button>
              <Button asChild size="sm" variant="ghost">
                <a href={`/api/v1/artifacts/${active.id}/download`} download>
                  <Download /> Download
                </a>
              </Button>
            </div>
          </div>
          {preview && isHtml ? (
            <div className="min-h-0 flex-1 bg-white">
              {/* No allow-same-origin: the page runs in an opaque origin with no access to this app or its cookies. */}
              <iframe title={`Preview of ${active.path}`} sandbox="allow-scripts" srcDoc={buildPreview(active, artifacts)} className="h-full w-full border-0" />
            </div>
          ) : (
            <pre className="min-h-0 flex-1 overflow-auto bg-bg p-3 font-mono text-[11.5px] leading-relaxed text-muted">
              <code>
                {active.content.split("\n").map((line, i) => (
                  <div key={i} className="flex">
                    <span className="mr-4 inline-block w-8 shrink-0 select-none text-right text-subtle/60">{i + 1}</span>
                    <span className="whitespace-pre-wrap break-all">{line || " "}</span>
                  </div>
                ))}
              </code>
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

export function Console({
  tab,
  onTab,
  workflow,
  tasks,
  log,
  stream,
  toolCalls,
  executions,
  artifacts,
  selectedTask,
  activeFile,
  onActiveFile,
}: {
  tab: ConsoleTab;
  onTab: (tab: ConsoleTab) => void;
  workflow: Workflow;
  tasks: Task[];
  log: LogLine[];
  stream: StreamState;
  toolCalls: WorkflowToolCall[];
  executions: Execution[];
  artifacts: Artifact[];
  selectedTask: Task | null;
  activeFile: string | null;
  onActiveFile: (id: string) => void;
}) {
  const problemCount = collectProblems(workflow, tasks, toolCalls, executions).length;
  const testCount = toolCalls.filter((c) => c.tool_name === "run_tests" || c.tool_name === "run_code").length;
  return (
    <Tabs value={tab} onValueChange={(v) => onTab(v as ConsoleTab)} className="flex h-full flex-col">
      <TabsList className="shrink-0 overflow-x-auto border-b border-line px-2 py-1" aria-label="Console">
        <TabsTrigger value="logs">
          <ScrollText /> Logs
        </TabsTrigger>
        <TabsTrigger value="output">
          <FileOutput /> Task output
        </TabsTrigger>
        <TabsTrigger value="terminal">
          <SquareTerminal /> Terminal
        </TabsTrigger>
        <TabsTrigger value="tests">
          <FlaskConical /> Test results {testCount > 0 && <span className="text-subtle">{testCount}</span>}
        </TabsTrigger>
        <TabsTrigger value="errors">
          <AlertOctagon /> Errors {problemCount > 0 && <span className="rounded bg-bad-soft px-1 text-bad">{problemCount}</span>}
        </TabsTrigger>
        <TabsTrigger value="files">
          <FileCode2 /> Generated files {artifacts.length > 0 && <span className="text-subtle">{artifacts.length}</span>}
        </TabsTrigger>
      </TabsList>
      <TabsContent value="logs" className="min-h-0 flex-1">
        <Logs log={log} stream={stream} selectedKey={selectedTask?.key ?? null} />
      </TabsContent>
      <TabsContent value="output" className="min-h-0 flex-1">
        <Outputs workflow={workflow} tasks={tasks} selected={selectedTask} />
      </TabsContent>
      <TabsContent value="terminal" className="min-h-0 flex-1 overflow-y-auto p-3">
        <Notice title="No interactive terminal">
          Agents never get a shell on this server. Code runs only inside the isolated test sandbox — a throwaway container with CPU,
          memory, process and time limits, no host Docker socket, and network only while dependencies install. Every command
          it ran, with its exit code and output, is under <button type="button" className="text-fg underline" onClick={() => onTab("tests")}>Test results</button>.
        </Notice>
      </TabsContent>
      <TabsContent value="tests" className="min-h-0 flex-1">
        <Tests calls={toolCalls} />
      </TabsContent>
      <TabsContent value="errors" className="min-h-0 flex-1">
        <Problems workflow={workflow} tasks={tasks} calls={toolCalls} executions={executions} />
      </TabsContent>
      <TabsContent value="files" className="min-h-0 flex-1">
        <Files workflowId={workflow.id} artifacts={artifacts} tasks={tasks} activeId={activeFile} onActive={onActiveFile} />
      </TabsContent>
    </Tabs>
  );
}

