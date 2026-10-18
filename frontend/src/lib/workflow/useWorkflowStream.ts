"use client";

import { useEffect, useEffectEvent, useState } from "react";
import type { LiveEvent } from "@/lib/api/types";
import { STREAM_EVENTS } from "./events";

export type StreamState = "connecting" | "live" | "reconnecting" | "ended" | "off";

const MAX_BACKOFF_MS = 15_000;

/**
 * A workflow's events: everything recorded so far, then live ones, with
 * reconnection that resumes after the last event received — so a dropped
 * connection loses nothing and repeats nothing.
 *
 * `generation` reopens the stream; bump it when a finished workflow is run
 * again, since the server closes the stream of a finished workflow.
 */
export function useWorkflowStream(
  workflowId: string | null,
  onEvent: (event: LiveEvent) => void,
  generation = 0,
): StreamState {
  const deliver = useEffectEvent(onEvent);
  const [state, setState] = useState<StreamState>(workflowId ? "connecting" : "off");

  useEffect(() => {
    if (!workflowId) return;
    let lastSeq = 0;
    let source: EventSource | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let failures = 0;
    let stopped = false;

    const open = () => {
      setState(failures === 0 ? "connecting" : "reconnecting");
      // `after` is explicit, rather than relying on the browser's own
      // Last-Event-ID, because a hand-made reconnect starts a fresh source.
      source = new EventSource(`/api/v1/workflows/${workflowId}/stream?after=${lastSeq}`);

      const receive = (message: MessageEvent<string>) => {
        let event: LiveEvent;
        try {
          event = JSON.parse(message.data) as LiveEvent;
        } catch {
          return;
        }
        if (event.event === "subscribed") {
          failures = 0;
          setState("live");
          return;
        }
        if (event.event === "stream_end") {
          stopped = true;
          source?.close();
          setState("ended");
          return;
        }
        if (typeof event.seq === "number") {
          if (event.seq <= lastSeq) return; // already delivered
          lastSeq = event.seq;
        }
        deliver(event);
      };
      for (const name of STREAM_EVENTS) source.addEventListener(name, receive as EventListener);

      source.onerror = () => {
        if (stopped) return;
        // EventSource retries some failures itself, but gives up for good on
        // an HTTP error. Either way, restart by hand with a backoff, resuming
        // from the last event seen.
        source?.close();
        failures += 1;
        setState("reconnecting");
        const delay = Math.min(MAX_BACKOFF_MS, 1000 * 2 ** Math.min(failures - 1, 4));
        retryTimer = setTimeout(open, delay);
      };
    };

    open();
    return () => {
      stopped = true;
      if (retryTimer) clearTimeout(retryTimer);
      source?.close();
    };
  }, [workflowId, generation]);

  return workflowId ? state : "off";
}
