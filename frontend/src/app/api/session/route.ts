import { NextResponse, type NextRequest } from "next/server";
import {
  ACCESS_COOKIE,
  API_URL,
  describeError,
  REFRESH_COOKIE,
  refreshTokens,
  sameOrigin,
  sessionCookies,
  unreachable,
  type TokenPair,
} from "@/lib/api/server";

/**
 * Sign in or create an account against the backend, keeping the tokens in
 * httpOnly cookies. Tokens are never returned to page JavaScript.
 */
export async function POST(request: NextRequest) {
  if (!sameOrigin(request)) {
    return NextResponse.json({ message: "Cross-site request refused" }, { status: 403 });
  }

  const body = await request.json().catch(() => null);
  const mode = body?.mode === "register" ? "register" : "login";
  const email = typeof body?.email === "string" ? body.email.trim() : "";
  const password = typeof body?.password === "string" ? body.password : "";
  const fullName = typeof body?.full_name === "string" ? body.full_name.trim() : "";
  if (!email || !password) {
    return NextResponse.json({ message: "Enter your email and password." }, { status: 400 });
  }

  const forwarded = request.headers.get("x-forwarded-for")?.split(",").at(-1)?.trim();
  let upstream: Response;
  try {
    upstream = await fetch(`${API_URL}/api/v1/auth/${mode}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(forwarded ? { "x-forwarded-for": forwarded } : {}),
        "user-agent": request.headers.get("user-agent") ?? "",
      },
      body: JSON.stringify(
        mode === "register" && fullName ? { email, password, full_name: fullName } : { email, password },
      ),
      cache: "no-store",
    });
  } catch {
    return NextResponse.json(unreachable(), { status: 502 });
  }

  const data = await upstream.json().catch(() => null);
  if (!upstream.ok) {
    return NextResponse.json(
      { message: describeError(data, "Could not sign in") },
      { status: upstream.status },
    );
  }

  const response = NextResponse.json({ ok: true });
  for (const cookie of sessionCookies(data as TokenPair, request)) {
    response.cookies.set(cookie);
  }
  return response;
}

/**
 * Sign out. The backend ends the session (or every session), so the tokens
 * stop working at once rather than whenever they would have expired; the
 * cookies are cleared either way.
 */
export async function DELETE(request: NextRequest) {
  if (!sameOrigin(request)) {
    return NextResponse.json({ message: "Cross-site request refused" }, { status: 403 });
  }
  const everywhere = request.nextUrl.searchParams.get("everywhere") === "true";

  let access = request.cookies.get(ACCESS_COOKIE)?.value;
  const refresh = request.cookies.get(REFRESH_COOKIE)?.value;
  if (!access && refresh) {
    const renewal = await refreshTokens(refresh);
    if (typeof renewal === "object") access = renewal.access_token;
  }

  let ended = true;
  if (access) {
    try {
      const forwarded = request.headers.get("x-forwarded-for")?.split(",").at(-1)?.trim();
      const upstream = await fetch(`${API_URL}/api/v1/auth/logout`, {
        method: "POST",
        // The client's address and browser, so the security log records who
        // signed out — not this server.
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${access}`,
          ...(forwarded ? { "x-forwarded-for": forwarded } : {}),
          "user-agent": request.headers.get("user-agent") ?? "",
        },
        body: JSON.stringify({ everywhere }),
        cache: "no-store",
      });
      // 401: the session was already over, which is what was asked for.
      ended = upstream.ok || upstream.status === 401;
    } catch {
      ended = false;
    }
  }

  const response = ended
    ? new NextResponse(null, { status: 204 })
    : NextResponse.json(
        {
          message:
            "Signed out of this browser, but the server could not be told. " +
            "The session ends on its own when its token expires.",
        },
        { status: 202 },
      );
  response.cookies.delete(ACCESS_COOKIE);
  response.cookies.delete(REFRESH_COOKIE);
  return response;
}
