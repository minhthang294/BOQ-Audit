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
  const navClass = (active: boolean) => `focus-ring flex min-h-11 items-center border-l-2 px-4 text-sm font-semibold transition-colors ${active ? "border-signal bg-white/10 text-white" : "border-transparent text-slate-400 hover:border-slate-600 hover:text-white"}`;
  const home = admin ? "/admin" : "/";
  return <div className="min-h-screen lg:grid lg:grid-cols-[232px_minmax(0,1fr)]">
    <aside className="hidden min-h-screen flex-col bg-night text-white lg:sticky lg:top-0 lg:flex lg:h-screen">
      <Link href={home} className="focus-ring mx-5 mt-6 flex items-center gap-3 border-b border-white/15 pb-6">
        <Image src="/sbtech-logo.png" alt="SBTech" width={58} height={40} priority className="h-10 w-[58px] bg-white object-contain p-1" />
        <span><span className="display-face block text-xl font-bold uppercase leading-none tracking-wide">{admin ? "BOQ Admin" : "BOQ Audit"}</span><span className="mt-1 block text-[10px] uppercase tracking-[0.22em] text-slate-400">SBTech Portal</span></span>
      </Link>
      <nav aria-label="Điều hướng chính" className="mt-8 space-y-1 px-3">{admin ? <><Link href="/admin" className={navClass(pathname === "/admin" || pathname.startsWith("/admin/jobs"))}>Hồ sơ kiểm toán</Link><Link href="/admin/users" className={navClass(pathname.startsWith("/admin/users"))}>Tài khoản</Link></> : <Link href="/" className={navClass(pathname === "/" || pathname.startsWith("/jobs"))}>Hồ sơ của tôi</Link>}</nav>
      <div className="mt-auto border-t border-white/10 p-4">{!admin && <CompletionNotifications />}<button onClick={logout} className="mt-1 flex min-h-11 w-full items-center px-4 text-sm font-semibold text-slate-400 transition-colors hover:text-white">Đăng xuất <span className="ml-auto" aria-hidden="true">↗</span></button></div>
    </aside>
    <div className="min-w-0">
      <header className="sticky top-0 z-40 border-b border-slate-300 bg-night text-white lg:hidden"><div className="flex min-h-16 items-center justify-between gap-3 px-4"><Link href={home} className="focus-ring flex min-h-11 items-center gap-2"><Image src="/sbtech-logo.png" alt="SBTech" width={48} height={32} priority className="h-8 w-12 bg-white object-contain p-0.5" /><span className="display-face text-lg font-bold uppercase tracking-wide">{admin ? "BOQ Admin" : "BOQ Audit"}</span></Link><nav aria-label="Điều hướng chính" className="flex items-center gap-1">{admin && <Link href="/admin/users" className="min-h-11 px-2 py-3 text-sm font-semibold">Tài khoản</Link>}<button onClick={logout} className="min-h-11 px-2 text-sm font-semibold text-slate-300">Thoát</button></nav></div></header>
      <main className={`mx-auto px-4 py-7 sm:px-8 sm:py-10 ${wide ? "max-w-[1560px]" : "max-w-7xl"}`}>{children}</main>
    </div>
  </div>;
}
