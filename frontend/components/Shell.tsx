"use client";
import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { CompletionNotifications } from "@/components/CompletionNotifications";

export function Shell({ children, admin = false, wide = false }: { children: React.ReactNode; admin?: boolean; wide?: boolean }) {
  const router = useRouter();
  async function logout() { await api("/auth/logout", { method: "POST" }); router.push("/login"); router.refresh(); }
  return <>
    <header className="sticky top-0 z-40 border-b border-white/70 bg-white/80 shadow-sm backdrop-blur-xl">
      <div className={`mx-auto flex items-center justify-between px-4 py-3 sm:px-6 ${wide ? "max-w-[1600px]" : "max-w-6xl"}`}>
        <Link href={admin ? "/admin" : "/"} className="flex items-center gap-3">
          <Image src="/sbtech-logo.png" alt="SBTech" width={66} height={44} priority className="h-11 w-[66px] object-contain" />
          <span><span className="block text-lg font-extrabold tracking-wide text-brand">{admin ? "BOQ ADMIN" : "BOQ AUDIT"}</span><span className="hidden text-[10px] font-semibold uppercase tracking-[0.2em] text-slate-400 sm:block">SBTech Portal</span></span>
        </Link>
        <div className="flex items-center gap-2">{!admin && <CompletionNotifications />}<button onClick={logout} className="rounded-lg px-3 py-2 text-sm font-semibold text-slate-500 transition hover:bg-slate-100 hover:text-ink">Đăng xuất</button></div>
      </div>
    </header>
    <main className={`mx-auto px-4 py-6 sm:px-6 sm:py-8 ${wide ? "max-w-[1600px]" : "max-w-6xl"}`}>{children}</main>
  </>;
}
