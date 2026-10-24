import { timingSafeEqual } from "node:crypto";
import { NextResponse, type NextRequest } from "next/server";
import {
  API_URL,
  clientHeaders,
  describeError,
  publicOrigin,
  sessionCookies,
  unreachable,
  type TokenPair,
} from "@/lib/api/server";
import { safeNext } from "@/lib/safe-next";
import { GOOGLE_STATE_COOKIE, signInError } from "../shared";

function sameState(a: string, b: string): boolean {
  const left = Buffer.from(a);
  const right = Buffer.from(b);
  return left.length === right.length && timingSafeEqual(left, right);
}

/**
 * Google sends the browser back here with a one-time code. The backend trades
 * it for the account's identity and opens a session, whose tokens go into the
 * same httpOnly cookies as a password sign-in.
 */
export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams;
  let saved: { state?: unknown; next?: unknown } = {};
  try {
    saved = JSON.parse(request.cookies.get(GOOGLE_STATE_COOKIE)?.value ?? "{}");
  } catch {
    // A mangled cookie is the same as none.
  }
  const next = safeNext(typeof saved.next === "string" ? saved.next : null);

  if (params.get("error")) {
    // "access_denied": they closed or cancelled Google's page.
    return signInError(request, "Google sign-in was cancelled.", next);
  }
  const code = params.get("code");
  const state = params.get("state");
  if (!code || !state || typeof saved.state !== "string" || !sameState(state, saved.state)) {
    return signInError(request, "That Google sign-in expired or came from elsewhere. Try again.", next);
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${API_URL}/api/v1/auth/google`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...clientHeaders(request),
        "user-agent": request.headers.get("user-agent") ?? "",
      },
      body: JSON.stringify({ code, redirect_uri: `${publicOrigin(request)}/api/auth/google/callback` }),
      cache: "no-store",
    });
  } catch {
    return signInError(request, unreachable().message, next);
  }

  const data = await upstream.json().catch(() => null);
  if (!upstream.ok) {
    return signInError(request, describeError(data, "Could not sign in with Google."), next);
  }

  const response = NextResponse.redirect(new URL(next, publicOrigin(request)));
  for (const cookie of sessionCookies(data as TokenPair, request)) {
    response.cookies.set(cookie);
  }
  response.cookies.delete({ name: GOOGLE_STATE_COOKIE, path: "/api/auth/google" });
  return response;
}
