/**
 * The bridge to the backend (FastAPI), server side only.
 *
 * The browser never holds a token. The backend's access and refresh tokens
 * live in httpOnly cookies, and every API call goes through this app's
 * /api/v1 proxy, which turns the cookie into the bearer header the backend
 * expects. That is also what lets the browser's EventSource — which cannot
 * send headers — open the live event stream.
 */

import "server-only";

import type { NextRequest } from "next/server";

export const API_URL = (process.env.EVERGENT_API_URL ?? "http://localhost:8000").replace(
  /\/+$/,
  "",
);

export const ACCESS_COOKIE = "evg_at";
export const REFRESH_COOKIE = "evg_rt";

// Set when this app runs apart from the backend (on Vercel, say). Its outbound
// address then changes from request to request, so the backend cannot list it
// in TRUSTED_PROXIES; this shared secret vouches for it instead. It must equal
// the backend's FRONTEND_PROXY_SECRET.
const PROXY_SECRET = process.env.FRONTEND_PROXY_SECRET;

/**
 * Who the browser is, for the backend's per-client rate limits and security
 * log. Without it every browser would share the one bucket of this server's
 * address — and the strict sign-in limit with it.
 */
export function clientHeaders(request: NextRequest): Record<string, string> {
  const client = request.headers.get("x-forwarded-for")?.split(",").at(-1)?.trim();
  if (!client) return {};
  // X-Forwarded-For serves a backend that trusts this server's address; the
  // secret-backed pair serves one that cannot, and that no proxy rewrites.
  return PROXY_SECRET
    ? { "x-forwarded-for": client, "x-evergent-proxy-secret": PROXY_SECRET, "x-evergent-client-ip": client }
    : { "x-forwarded-for": client };
}

export type TokenPair = {
  access_token: string;
  refresh_token: string;
  expires_in: number;
};

type CookieSpec = {
  name: string;
  value: string;
  httpOnly: true;
  sameSite: "lax";
  secure: boolean;
  path: "/";
  maxAge: number;
};

/** Seconds until a JWT expires, read from its payload. Not a validity check. */
function secondsLeft(token: string): number | null {
  try {
    const payload = JSON.parse(
      Buffer.from(token.split(".")[1] ?? "", "base64url").toString("utf8"),
    );
    return typeof payload.exp === "number"
      ? Math.max(0, payload.exp - Math.floor(Date.now() / 1000))
      : null;
  } catch {
    return null;
  }
}

/** Whether cookies must be marked Secure: always behind HTTPS in production. */
function secureCookies(request: NextRequest): boolean {
  if (process.env.COOKIE_SECURE === "false") return false;
  if (process.env.COOKIE_SECURE === "true") return true;
  const forwarded = request.headers.get("x-forwarded-proto");
  return forwarded ? forwarded === "https" : request.nextUrl.protocol === "https:";
}

export function sessionCookies(tokens: TokenPair, request: NextRequest): CookieSpec[] {
  const base = {
    httpOnly: true as const,
    // Lax: not sent on cross-site POSTs, so another site cannot drive the API
    // with this session.
    sameSite: "lax" as const,
    secure: secureCookies(request),
    path: "/" as const,
  };
  return [
    { ...base, name: ACCESS_COOKIE, value: tokens.access_token, maxAge: tokens.expires_in },
    {
      ...base,
      name: REFRESH_COOKIE,
      value: tokens.refresh_token,
      maxAge: secondsLeft(tokens.refresh_token) ?? 60 * 60 * 24 * 14,
    },
  ];
}

/**
 * Whether a state-changing request came from this app's own pages — the CSRF
 * check, on top of SameSite=Lax cookies.
 */
export function sameOrigin(request: NextRequest): boolean {
  const origin = request.headers.get("origin");
  const host = request.headers.get("x-forwarded-host") ?? request.headers.get("host");
  if (!origin) {
    // Browsers send Origin on every cross-origin POST; a missing one is a
    // same-origin request or a non-browser client without the cookies.
    return request.headers.get("sec-fetch-site") !== "cross-site";
  }
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

export function unreachable(): { message: string } {
  return {
    message:
      process.env.NODE_ENV === "production"
        ? "The backend is not reachable right now."
        : `The backend is not reachable at ${API_URL}. Start it with: ` +
          "cd backend && .venv/bin/uvicorn app.main:app --port 8000",
  };
}

/** The backend's error body, turned into one readable sentence. */
export function describeError(body: unknown, fallback: string): string {
  if (!body || typeof body !== "object") return fallback;
  const { message, details } = body as {
    message?: unknown;
    details?: { errors?: { loc?: unknown[]; msg?: string }[] };
  };
  const first = details?.errors?.[0];
  if (first?.msg) {
    const field = first.loc?.at(-1);
    return typeof field === "string" ? `${field}: ${first.msg}` : first.msg;
  }
  return typeof message === "string" ? message : fallback;
}

export type Renewal = TokenPair | "rejected" | "unreachable";

async function exchange(refreshToken: string): Promise<Renewal> {
  try {
    const response = await fetch(`${API_URL}/api/v1/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
      cache: "no-store",
    });
    if (response.status === 401) return "rejected";
    if (!response.ok) return "unreachable";
    return (await response.json()) as TokenPair;
  } catch {
    return "unreachable";
  }
}

// Refresh tokens are single use, and the backend ends the whole session when
// one is presented twice (that is how a stolen token is caught). A page that
// fires several requests just after its access token expired would present
// the same refresh token several times at once, so concurrent refreshes of
// one token share a single exchange, and its answer is kept briefly for
// requests that arrive just after.
const inflight = new Map<string, Promise<Renewal>>();
const SHARE_FOR_MS = 30_000;

/**
 * Trade a refresh token for a fresh pair. "rejected" means the session is
 * over; "unreachable" means try again later — the two must not be confused,
 * or a backend restart would sign everyone out.
 */
export function refreshTokens(refreshToken: string): Promise<Renewal> {
  const pending = inflight.get(refreshToken);
  if (pending) return pending;
  const exchanged = exchange(refreshToken);
  inflight.set(refreshToken, exchanged);
  exchanged.then(
    (renewal) => {
      if (renewal === "unreachable") inflight.delete(refreshToken);
      else setTimeout(() => inflight.delete(refreshToken), SHARE_FOR_MS).unref?.();
    },
    () => inflight.delete(refreshToken),
  );
  return exchanged;
}

/**
 * Whether this browser has a session at all. Optimistic — the backend checks
 * the token on every call — so pages use it only to decide what to show.
 */
export function hasSession(cookies: { has(name: string): boolean }): boolean {
  return cookies.has(REFRESH_COOKIE) || cookies.has(ACCESS_COOKIE);
}
