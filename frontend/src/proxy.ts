import { NextResponse, type NextRequest } from "next/server";

/**
 * An optimistic sign-in check for pages: no session cookie, no workspace.
 * The backend still checks the token on every call; this only spares a
 * signed-out visitor a page of failing requests.
 */
const PUBLIC = ["/signin", "/forgot-password", "/reset-password", "/verify-email"];

export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const signedIn = request.cookies.has("evg_rt") || request.cookies.has("evg_at");
  const isPublic = PUBLIC.some((p) => pathname === p || pathname.startsWith(`${p}/`));

  if (!signedIn && !isPublic) {
    const url = request.nextUrl.clone();
    url.pathname = "/signin";
    url.search = pathname === "/" ? "" : `?next=${encodeURIComponent(pathname + search)}`;
    return NextResponse.redirect(url);
  }
  if (signedIn && pathname === "/signin") {
    return NextResponse.redirect(new URL("/", request.url));
  }
  return NextResponse.next();
}

export const config = {
  // Pages only: API routes answer for themselves, assets need no session.
  matcher: ["/((?!api/|_next/|favicon.ico|.*\\.(?:svg|png|jpg|ico|webp|txt)$).*)"],
};
