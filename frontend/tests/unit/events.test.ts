import { describe, expect, it } from "vitest";
import { appendLines, describeEvent, type LogLine } from "@/lib/workflow/events";

const ev = (event: string, data: Record<string, unknown> = {}, seq?: number) => ({
  event,
  workflow_id: "wf",
  timestamp: "2026-10-14T10:00:00Z",
  data,
  seq,
});

describe("describeEvent", () => {
  it("skips transport messages", () => {
    expect(describeEvent(ev("subscribed"))).toBeNull();
    expect(describeEvent(ev("stream_end"))).toBeNull();
  });

  it("states failures as errors, with their reason", () => {
    const line = describeEvent(ev("task_failed", { task_key: "t2", error: "Model timed out" }, 9))!;
    expect(line).toMatchObject({ level: "error", task: "t2", key: "s9" });
    expect(line.text).toContain("Model timed out");
  });

  it("distinguishes tests that did not run from tests that failed", () => {
    expect(describeEvent(ev("tests_skipped", { reason: "Docker is not running" }))!.level).toBe("warn");
    expect(describeEvent(ev("tests_completed", { passed: false, stage: "test", exit_code: 1 }))!.text).toBe("Tests failed (test exit 1)");
    expect(describeEvent(ev("tests_completed", { passed: true }))!.level).toBe("success");
  });

  it("numbers retries as the attempt about to run", () => {
    expect(describeEvent(ev("retry_started", { attempt: 1 }))!.text).toBe("Retrying (attempt 2)");
  });

  it("marks plan readiness that waits for approval", () => {
    expect(describeEvent(ev("plan_ready", { task_count: 3, awaiting_approval: true }))!.text).toBe("Plan ready: 3 tasks — waiting for approval");
  });
});

describe("appendLines", () => {
  const line = (seq: number): LogLine => ({ key: `s${seq}`, seq, event: "x", level: "info", text: String(seq) });

  it("drops events delivered twice by a reconnect", () => {
    const log = appendLines([line(1), line(2)], [line(2), line(3)]);
    expect(log.map((l) => l.seq)).toEqual([1, 2, 3]);
  });

  it("keeps sequence order even if events arrive out of order", () => {
    expect(appendLines([line(1), line(4)], [line(2)]).map((l) => l.seq)).toEqual([1, 2, 4]);
  });

  it("caps the log, keeping the newest", () => {
    const log = appendLines([], [line(1), line(2), line(3)], 2);
    expect(log.map((l) => l.seq)).toEqual([2, 3]);
  });
});
