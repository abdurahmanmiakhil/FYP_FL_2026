import { NextResponse, type NextRequest } from "next/server";

/**
 * Route protection (UX layer - the API enforces every permission itself):
 * - no session cookie -> /login?next=...
 * - /admin/* needs the admin role (read from the access token's claims; it is httpOnly and
 *   signed by the API, which re-verifies it on every call). When the short-lived access token
 *   has expired the page loads and the client refreshes it; the admin layout re-checks the role.
 */
const PUBLIC = ["/login", "/about"];

function roleFromAccessToken(token: string | undefined): string | null {
  if (!token) return null;
  try {
    const payload = token.split(".")[1];
    if (!payload) return null;
    const json = JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/"))) as {
      role?: string;
      exp?: number;
    };
    if (json.exp && json.exp * 1000 < Date.now()) return null;
    return json.role ?? null;
  } catch {
    return null;
  }
}

export function middleware(req: NextRequest) {
  const { pathname, search } = req.nextUrl;
  const hasSession = req.cookies.has("csrf_token") || req.cookies.has("access_token");
  const isPublic = PUBLIC.some((p) => pathname === p || pathname.startsWith(`${p}/`));

  if (!hasSession && !isPublic) {
    const url = new URL("/login", req.url);
    if (pathname !== "/") url.searchParams.set("next", pathname + search);
    return NextResponse.redirect(url);
  }
  if (pathname.startsWith("/admin")) {
    const role = roleFromAccessToken(req.cookies.get("access_token")?.value);
    if (role && role !== "admin") return NextResponse.redirect(new URL("/dashboard?denied=1", req.url));
  }
  return NextResponse.next();
}

export const config = {
  // everything except the API proxy, Next internals and static files
  matcher: ["/((?!api/|_next/static|_next/image|favicon.ico|.*\\.(?:png|jpg|svg|ico)$).*)"],
};
