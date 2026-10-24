import { NextResponse, type NextRequest } from "next/server";
import { publicOrigin } from "@/lib/api/server";

export const GOOGLE_STATE_COOKIE = "evg_google";

/** Back to the sign-in page, saying what went wrong. */
export function signInError(request: NextRequest, message: string, next: string): NextResponse {
  const url = new URL("/signin", publicOrigin(request));
  url.searchParams.set("error", message);
  if (next !== "/") url.searchParams.set("next", next);
  const response = NextResponse.redirect(url);
  response.cookies.delete({ name: GOOGLE_STATE_COOKIE, path: "/api/auth/google" });
  return response;
}
