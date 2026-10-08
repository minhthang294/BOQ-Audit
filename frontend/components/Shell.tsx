"use client";
import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { usePathname } from "next/navigation";
import { api } from "@/lib/api";
import { CompletionNotifications } from "@/components/CompletionNotifications";

export function Shell({ children, admin = false, wide = false }: { children: React.ReactNode; admin?: boolean; wide?: boolean }) {
  const router = useRouter(); const pathname = usePathname();
  async function logout() { await api("/auth/logout", { method: "POST" }); router.push("/login"); router.refresh(); }
  const navClass = (active: boolean) => `focus-ring inline-flex min-h-11 items-center rounded-lg px-3 text-sm font-semibold transition ${active ? "bg-cyan-50 text-cyan-900" : "text-slate-600 hover:bg-slate-100 hover:text-slate-950"}`;
  return <>
    <header className="sticky top-0 z-40 border-b border-slate-200 bg-white/95 backdrop-blur-lg">
      <div className={`mx-auto flex min-h-16 items-center justify-between gap-4 px-4 sm:px-6 ${wide ? "max-w-[1600px]" : "max-w-6xl"}`}>
        <Link href={admin ? "/admin" : "/"} className="focus-ring flex min-h-11 items-center gap-3 rounded-lg">
          <Image src="/sbtech-logo.png" alt="SBTech" width={54} height={36} priority className="h-9 w-[54px] object-contain" />
          <span className="hidden sm:block"><span className="block text-base font-extrabold tracking-tight text-slate-950">{admin ? "BOQ Admin" : "BOQ Audit"}</span><span className="block text-[11px] font-medium text-slate-500">SBTech Portal</span></span>
        </Link>
        <nav aria-label="Điều hướng chính" className="flex items-center gap-1">{admin && <><Link href="/admin" className={navClass(pathname === "/admin" || pathname.startsWith("/admin/jobs"))}>Hồ sơ</Link><Link href="/admin/users" className={navClass(pathname.startsWith("/admin/users"))}>Tài khoản</Link></>}{!admin && <CompletionNotifications />}<button onClick={logout} className={navClass(false)}>Đăng xuất</button></nav>
      </div>
    </header>
    <main className={`mx-auto px-4 py-6 sm:px-6 sm:py-9 ${wide ? "max-w-[1600px]" : "max-w-6xl"}`}>{children}</main>
  </>;
}
