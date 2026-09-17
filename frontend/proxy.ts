import { NextRequest, NextResponse } from "next/server";

export async function proxy(request: NextRequest) {
  const backend = process.env.BACKEND_INTERNAL_URL || "http://localhost:8000";
  try {
    const response = await fetch(`${backend}/api/auth/me`, { headers: { cookie: request.headers.get("cookie") || "" }, cache: "no-store" });
    if (!response.ok) return NextResponse.redirect(new URL("/login", request.url));
    const user = await response.json();
    if (request.nextUrl.pathname.startsWith("/admin") && user.role !== "ADMIN") return NextResponse.redirect(new URL("/", request.url));
    if (!request.nextUrl.pathname.startsWith("/admin") && user.role === "ADMIN") return NextResponse.redirect(new URL("/admin", request.url));
  } catch { return NextResponse.redirect(new URL("/login", request.url)); }
  return NextResponse.next();
}

export const config = { matcher: ["/", "/jobs/:path*", "/admin/:path*"] };
