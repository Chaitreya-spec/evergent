/**
 * What each workflow and task status means to a person, and how it looks.
 * One table, so a status reads the same on the canvas, in lists and pills.
 */

export type Tone = "idle" | "active" | "ok" | "warn" | "bad" | "muted";

type StatusInfo = { label: string; tone: Tone };

const STATUS: Record<string, StatusInfo> = {
  pending: { label: "Pending", tone: "idle" },
  planning: { label: "Planning", tone: "active" },
  awaiting_approval: { label: "Awaiting approval", tone: "warn" },
  queued: { label: "Queued", tone: "idle" },
  waiting: { label: "Waiting", tone: "idle" },
  running: { label: "Running", tone: "active" },
  validating: { label: "Reviewing", tone: "active" },
  synthesizing: { label: "Integrating", tone: "active" },
  retrying: { label: "Retrying", tone: "warn" },
  paused: { label: "Paused", tone: "warn" },
  completed: { label: "Completed", tone: "ok" },
  failed: { label: "Failed", tone: "bad" },
  cancelled: { label: "Cancelled", tone: "muted" },
  skipped: { label: "Skipped", tone: "muted" },
};

export function statusInfo(status: string | null | undefined): StatusInfo {
  if (!status) return { label: "Unknown", tone: "muted" };
  return STATUS[status] ?? { label: status.replace(/_/g, " "), tone: "muted" };
}

export const isBusy = (status: string | null | undefined) => statusInfo(status).tone === "active";
