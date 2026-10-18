import { NextResponse } from "next/server";
import { API_URL } from "@/lib/api/server";

/**
 * Liveness of this server, plus whether it can reach the backend. Used by the
 * container health check; reveals nothing beyond up/down.
 */
export async function GET() {
  let backend: "ok" | "unreachable" = "unreachable";
  try {
    const response = await fetch(`${API_URL}/api/v1/health`, {
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });
    if (response.ok) backend = "ok";
  } catch {
    // reported below
  }
  return NextResponse.json({ status: "ok", backend }, { headers: { "cache-control": "no-store" } });
}
