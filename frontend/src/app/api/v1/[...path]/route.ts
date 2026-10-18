import { NextResponse, type NextRequest } from "next/server";
import {
  ACCESS_COOKIE,
  API_URL,
  REFRESH_COOKIE,
  refreshTokens,
  sameOrigin,
  sessionCookies,
  unreachable,
  type Renewal,
  type TokenPair,
} from "@/lib/api/server";

/**
 * Every /api/v1 call from the browser, forwarded to the backend with the
 * session's bearer token. Responses are streamed straight through, which is
 * what makes the live event stream and archive downloads work.
 */

const SAFE_METHODS = new Set(["GET", "HEAD"]);

// These issue tokens. They are answered by /api/session instead, so a token
// never lands somewhere page JavaScript could read it.
const TOKEN_ENDPOINTS = new Set(["auth/login", "auth/register", "auth/refresh", "auth/logout"]);

const FORWARDED = ["content-type", "accept", "last-event-id", "idempotency-key", "user-agent"];

const PASSED_BACK = [
  "content-type",
  "content-disposition",
  "cache-control",
  "retry-after",
  "x-request-id",
  "x-content-type-options",
];

// Scripts and CI authenticate with a personal API key instead of a session.
// Such a request carries no cookies, so it is not a CSRF risk, and the key is
// passed through unchanged. Session tokens (JWTs) never start with this.
const API_KEY = /^Bearer evg_[A-Za-z0-9_-]+$/;

const isPair = (renewal: Renewal | null): renewal is TokenPair =>
  typeof renewal === "object" && renewal !== null;

function outgoingHeaders(request: NextRequest, token: string | undefined): Headers {
  const headers = new Headers();
  for (const name of FORWARDED) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  // The backend rate-limits per client. Without this every browser would
  // share the one bucket of this server's address.
  const forwarded = request.headers.get("x-forwarded-for")?.split(",").at(-1)?.trim();
  if (forwarded) headers.set("x-forwarded-for", forwarded);
  if (token) headers.set("authorization", `Bearer ${token}`);
  return headers;
}

async function forward(
  request: NextRequest,
  ctx: RouteContext<"/api/v1/[...path]">,
): Promise<Response> {
  const { path } = await ctx.params;
  if (path.some((segment) => segment === "" || segment === "." || segment === "..")) {
    return NextResponse.json({ message: "Bad path" }, { status: 400 });
  }
  if (TOKEN_ENDPOINTS.has(path.join("/"))) {
    return NextResponse.json({ message: "Use /api/session" }, { status: 404 });
  }
  const apiKey = request.headers.get("authorization")?.trim() ?? "";
  const usingKey = API_KEY.test(apiKey);
  if (!usingKey && !SAFE_METHODS.has(request.method) && !sameOrigin(request)) {
    return NextResponse.json({ message: "Cross-site request refused" }, { status: 403 });
  }

  // With an API key, the session cookies are ignored entirely.
  const refresh = usingKey ? undefined : request.cookies.get(REFRESH_COOKIE)?.value;
  let access = usingKey ? apiKey.slice("Bearer ".length) : request.cookies.get(ACCESS_COOKIE)?.value;
  let renewal: Renewal | null = null;

  // The access cookie expires with its token; the refresh cookie outlives it.
  if (!usingKey && !access && refresh) {
    renewal = await refreshTokens(refresh);
    if (isPair(renewal)) access = renewal.access_token;
  }

  // Read once, so the request can be replayed after a token refresh.
  const body = SAFE_METHODS.has(request.method) ? undefined : await request.arrayBuffer();
  const target =
    `${API_URL}/api/v1/${path.map(encodeURIComponent).join("/")}` + request.nextUrl.search;

  const send = () =>
    fetch(target, {
      method: request.method,
      headers: outgoingHeaders(request, access),
      body,
      redirect: "manual",
      cache: "no-store",
      // A closed tab ends the upstream request too — for the event stream,
      // that is what releases the backend's subscriber.
      signal: request.signal,
    });

  let upstream: Response;
  try {
    upstream = await send();
    if (upstream.status === 401 && refresh && renewal === null) {
      renewal = await refreshTokens(refresh);
      if (isPair(renewal)) {
        access = renewal.access_token;
        await upstream.body?.cancel();
        upstream = await send();
      }
    }
  } catch {
    if (request.signal.aborted) return new Response(null, { status: 499 });
    return NextResponse.json(unreachable(), { status: 502 });
  }

  const headers = new Headers();
  for (const name of PASSED_BACK) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  if (headers.get("content-type")?.startsWith("text/event-stream")) {
    // no-transform stops any compression layer from buffering the stream.
    headers.set("cache-control", "no-cache, no-transform");
    headers.set("x-accel-buffering", "no");
  }

  const response = new NextResponse(upstream.body, { status: upstream.status, headers });
  if (isPair(renewal)) {
    for (const cookie of sessionCookies(renewal, request)) response.cookies.set(cookie);
  } else if (!usingKey && (renewal === "rejected" || (upstream.status === 401 && !refresh))) {
    // The session cannot be renewed, so drop it rather than leave a cookie
    // that only ever earns a 401.
    response.cookies.delete(ACCESS_COOKIE);
    response.cookies.delete(REFRESH_COOKIE);
  }
  return response;
}

export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const PATCH = forward;
export const DELETE = forward;
