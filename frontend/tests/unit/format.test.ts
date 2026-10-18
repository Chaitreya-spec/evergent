import { describe, expect, it } from "vitest";
import { bytes, duration, humanize, money, perMillion, timeAgo, tokens } from "@/lib/format";
import { safeNext } from "@/lib/safe-next";
import { collectProblems } from "@/lib/workflow/problems";
import { fillDays } from "@/components/pages/UsagePage";
import { execution, task, workflow } from "../fixtures/data";

describe("format", () => {
  it("formats money without hiding tiny amounts", () => {
    expect(money(0)).toBe("$0.00");
    expect(money(0.0042)).toBe("$0.0042");
    expect(money(12.5)).toBe("$12.50");
  });
  it("formats sizes and counts", () => {
    expect(tokens(1500)).toBe("1.5k");
    expect(tokens(2_500_000)).toBe("2.5M");
    expect(bytes(2048)).toBe("2.0 KB");
    expect(duration(65_000)).toBe("1m 5s");
    expect(duration(0)).toBe("—");
    expect(perMillion(3e-6)).toBe("$3.00");
    expect(humanize("highest_quality")).toBe("Highest quality");
  });
  it("says how long ago", () => {
    const now = Date.parse("2026-10-14T12:00:00Z");
    expect(timeAgo("2026-10-14T11:58:00Z", now)).toBe("2m ago");
    expect(timeAgo("2026-10-14T11:59:50Z", now)).toBe("just now");
  });
});

describe("safeNext", () => {
  it("only allows this app's own pages", () => {
    expect(safeNext("/workflows/1")).toBe("/workflows/1");
    expect(safeNext("//evil.example")).toBe("/");
    expect(safeNext("https://evil.example")).toBe("/");
    expect(safeNext("/\\evil.example")).toBe("/");
    expect(safeNext("/api/v1/auth/me")).toBe("/");
    expect(safeNext(undefined)).toBe("/");
  });
});

describe("collectProblems", () => {
  it("gathers every recorded failure", () => {
    const problems = collectProblems(
      workflow({ status: "failed", error: "Budget reached" }),
      [task("t1", { status: "failed", error: "boom" }), task("t2", { status: "completed" })],
      [{ id: "c", task_id: "id-t1", task_key: "t1", tool_name: "run_sql", arguments: {}, result: null, succeeded: false, error: "syntax", duration_ms: 1, created_at: "" }],
      [execution("backend", { task_id: "id-t1", succeeded: false, error: "429" })],
    );
    expect(problems.map((p) => [p.source, p.taskKey ?? null])).toEqual([
      ["workflow", null],
      ["task", "t1"],
      ["tool", "t1"],
      ["model", "t1"],
    ]);
  });
});

describe("fillDays", () => {
  it("includes quiet days so the chart does not compress time", () => {
    const days = fillDays([{ day: "2026-10-13", cost_usd: 1, tokens: 10, calls: 1 }], 3, new Date("2026-10-14T08:00:00Z"));
    expect(days.map((d) => [d.day, d.cost_usd])).toEqual([
      ["2026-10-12", 0],
      ["2026-10-13", 1],
      ["2026-10-14", 0],
    ]);
  });
});

describe("parseTime", async () => {
  const { parseTime } = await import("@/lib/time");
  it("reads a zoneless stamp as UTC, not local time", () => {
    expect(parseTime("2026-10-14T10:00:00")).toBe(Date.parse("2026-10-14T10:00:00Z"));
    expect(parseTime("2026-10-14T10:00:00+05:30")).toBe(Date.parse("2026-10-14T04:30:00Z"));
    expect(parseTime(null)).toBeNaN();
  });
});
