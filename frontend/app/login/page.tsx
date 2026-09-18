"use client";
import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";
import { api } from "@/lib/api";
import { User } from "@/types";

export default function LoginPage() {
  const router = useRouter(); const [expired, setExpired] = useState(false);
  const [error, setError] = useState(""); const [loading, setLoading] = useState(false);
  useEffect(() => { setExpired(new URLSearchParams(window.location.search).has("expired")); }, []);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(""); setLoading(true);
    const form = new FormData(event.currentTarget);
    try {
      const user = await api<User>("/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: form.get("username"), password: form.get("password") }) });
      router.push(user.role === "ADMIN" ? "/admin" : "/"); router.refresh();
    } catch (e) { setError(e instanceof Error ? e.message : "Không thể đăng nhập"); } finally { setLoading(false); }
  }
  return <main className="flex min-h-screen items-center justify-center p-4 sm:p-8">
    <div className="grid w-full max-w-4xl overflow-hidden rounded-3xl bg-white shadow-[0_24px_80px_rgba(15,23,42,0.18)] ring-1 ring-white md:grid-cols-[0.9fr_1.1fr]">
      <section className="relative hidden overflow-hidden bg-gradient-to-br from-slate-900 via-cyan-950 to-cyan-800 p-10 text-white md:flex md:flex-col md:justify-between"><div className="absolute -right-24 -top-20 h-72 w-72 rounded-full border-[46px] border-white/5" /><div className="relative"><Image src="/sbtech-logo.png" alt="SBTech" width={120} height={80} priority className="h-20 w-[120px] rounded-xl bg-white object-contain p-1 shadow-lg" /><p className="mt-8 text-xs font-bold uppercase tracking-[0.25em] text-cyan-200">SBTech Portal</p><h2 className="mt-3 text-3xl font-extrabold leading-tight">Tra soát hồ sơ<br />rõ ràng, đơn giản.</h2><p className="mt-4 max-w-xs text-sm leading-6 text-cyan-100/80">Gửi hồ sơ, theo dõi tiến độ và nhận kết quả trên một nền tảng duy nhất.</p></div><p className="relative text-xs text-white/40">BOQ Audit Portal V1</p></section>
      <section className="p-7 sm:p-10 md:p-12"><div className="mb-8 md:hidden"><Image src="/sbtech-logo.png" alt="SBTech" width={90} height={60} priority className="h-[60px] w-[90px] object-contain" /></div><p className="eyebrow">BOQ Audit</p><h1 className="mt-2 text-3xl font-extrabold tracking-tight">Chào mừng trở lại</h1><p className="mt-2 text-sm leading-6 text-slate-500">Đăng nhập để quản lý và theo dõi hồ sơ tra soát.</p>
        {expired && <p className="mt-6 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.</p>}
        {error && <p className="error mt-6">{error}</p>}
        <form onSubmit={submit} className="mt-7 space-y-5">
          <div><label htmlFor="username">Tên đăng nhập</label><input id="username" name="username" type="text" minLength={3} maxLength={80} autoComplete="username" placeholder="Nhập tên đăng nhập" required /></div>
          <div><label htmlFor="password">Mật khẩu</label><input id="password" name="password" type="password" autoComplete="current-password" placeholder="Nhập mật khẩu" required /></div>
          <button disabled={loading} className="btn-primary mt-2 w-full">{loading ? "Đang đăng nhập…" : "ĐĂNG NHẬP"}</button>
        </form>
      </section>
    </div>
  </main>;
}
