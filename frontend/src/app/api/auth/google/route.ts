import { randomBytes } from "node:crypto";
import { NextResponse, type NextRequest } from "next/server";
import { googleClientId, publicOrigin } from "@/lib/api/server";
import { safeNext } from "@/lib/safe-next";
import { GOOGLE_STATE_COOKIE, signInError } from "./shared";

/**
 * Send the browser to Google's sign-in page. A random state, kept in an
 * httpOnly cookie, ties Google's answer to this browser so another site
 * cannot sign it into someone else's account.
 */
export async function GET(request: NextRequest) {
  const next = safeNext(request.nextUrl.searchParams.get("next"));
  const clientId = await googleClientId();
  if (!clientId) return signInError(request, "Google sign-in is not set up on this server.", next);

  const state = randomBytes(24).toString("base64url");
  const google = new URL("https://accounts.google.com/o/oauth2/v2/auth");
  google.search = new URLSearchParams({
    client_id: clientId,
    redirect_uri: `${publicOrigin(request)}/api/auth/google/callback`,
    response_type: "code",
    scope: "openid email profile",
    state,
    prompt: "select_account",
  }).toString();

  const response = NextResponse.redirect(google);
  response.cookies.set({
    name: GOOGLE_STATE_COOKIE,
    value: JSON.stringify({ state, next }),
    httpOnly: true,
    // Lax still sends it on the top-level navigation back from Google.
    sameSite: "lax",
    secure: publicOrigin(request).startsWith("https:"),
    path: "/api/auth/google",
    maxAge: 600,
  });
  return response;
}
