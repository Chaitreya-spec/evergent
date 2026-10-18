import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LiveEvent } from "@/lib/api/types";
import { useWorkflowStream } from "@/lib/workflow/useWorkflowStream";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  listeners = new Map<string, ((e: MessageEvent<string>) => void)[]>();
  onerror: (() => void) | null = null;
  closed = false;
  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(name: string, fn: (e: MessageEvent<string>) => void) {
    this.listeners.set(name, [...(this.listeners.get(name) ?? []), fn]);
  }
  close() {
    this.closed = true;
  }
  emit(payload: Partial<LiveEvent> & { event: string }) {
    for (const fn of this.listeners.get(payload.event) ?? []) fn(new MessageEvent(payload.event, { data: JSON.stringify({ workflow_id: "wf", data: {}, ...payload }) }));
  }
  fail() {
    this.onerror?.();
  }
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useWorkflowStream", () => {
  it("delivers events once and resumes after the last one on reconnect", () => {
    const received: LiveEvent[] = [];
    const { result } = renderHook(() => useWorkflowStream("wf", (e) => received.push(e)));
    const first = FakeEventSource.instances[0]!;
    expect(first.url).toBe("/api/v1/workflows/wf/stream?after=0");

    act(() => {
      first.emit({ event: "subscribed" });
      first.emit({ event: "task_started", seq: 1 });
      first.emit({ event: "task_started", seq: 1 }); // duplicate
      first.emit({ event: "task_completed", seq: 2 });
    });
    expect(result.current).toBe("live");
    expect(received.map((e) => e.seq)).toEqual([1, 2]);

    act(() => first.fail());
    expect(result.current).toBe("reconnecting");
    expect(first.closed).toBe(true);
    act(() => void vi.advanceTimersByTime(1000));

    const second = FakeEventSource.instances[1]!;
    expect(second.url).toBe("/api/v1/workflows/wf/stream?after=2");
    act(() => {
      second.emit({ event: "subscribed" });
      second.emit({ event: "task_completed", seq: 2 }); // replayed overlap
      second.emit({ event: "workflow_completed", seq: 3 });
    });
    expect(received.map((e) => e.seq)).toEqual([1, 2, 3]);
  });

  it("stops for good when the server says the stream is over", () => {
    const { result } = renderHook(() => useWorkflowStream("wf", () => {}));
    const source = FakeEventSource.instances[0]!;
    act(() => {
      source.emit({ event: "subscribed" });
      source.emit({ event: "stream_end" });
    });
    expect(result.current).toBe("ended");
    expect(source.closed).toBe(true);
    act(() => void vi.advanceTimersByTime(60_000));
    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it("backs off between failed attempts", () => {
    renderHook(() => useWorkflowStream("wf", () => {}));
    act(() => FakeEventSource.instances[0]!.fail());
    act(() => void vi.advanceTimersByTime(1000));
    act(() => FakeEventSource.instances[1]!.fail());
    act(() => void vi.advanceTimersByTime(1500));
    expect(FakeEventSource.instances).toHaveLength(2); // second retry waits 2s
    act(() => void vi.advanceTimersByTime(500));
    expect(FakeEventSource.instances).toHaveLength(3);
  });
});
