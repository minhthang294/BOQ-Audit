"use client";
import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
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
  return <main className="flex min-h-screen items-center justify-center px-4">
    <div className="w-full max-w-md rounded-2xl border border-line bg-white p-7 shadow-sm">
      <div className="mb-7"><p className="text-sm font-bold tracking-[0.2em] text-brand">BOQ AUDIT</p><h1 className="mt-2 text-2xl font-bold">Đăng nhập</h1><p className="mt-2 text-sm text-slate-500">Quản lý và theo dõi hồ sơ tra soát của bạn.</p></div>
      {expired && <p className="mb-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.</p>}
      {error && <p className="error mb-4">{error}</p>}
      <form onSubmit={submit} className="space-y-4">
        <div><label htmlFor="username">Tên đăng nhập</label><input id="username" name="username" type="text" minLength={3} maxLength={80} autoComplete="username" required /></div>
        <div><label htmlFor="password">Mật khẩu</label><input id="password" name="password" type="password" autoComplete="current-password" required /></div>
        <button disabled={loading} className="btn-primary w-full">{loading ? "Đang đăng nhập…" : "ĐĂNG NHẬP"}</button>
      </form>
    </div>
  </main>;
}
