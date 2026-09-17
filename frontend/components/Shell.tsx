"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

export function Shell({ children, admin = false }: { children: React.ReactNode; admin?: boolean }) {
  const router = useRouter();
  async function logout() { await api("/auth/logout", { method: "POST" }); router.push("/login"); router.refresh(); }
  return <>
    <header className="border-b border-line bg-white">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-4 sm:px-6">
        <Link href={admin ? "/admin" : "/"} className="text-lg font-bold tracking-wide text-brand">{admin ? "BOQ ADMIN" : "BOQ AUDIT"}</Link>
        <button onClick={logout} className="text-sm font-medium text-slate-600 hover:text-ink">Đăng xuất</button>
      </div>
    </header>
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6">{children}</main>
  </>;
}

