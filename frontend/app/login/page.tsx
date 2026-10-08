"use client";
import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";
import { api } from "@/lib/api";
import { User } from "@/types";

export default function LoginPage() {
  const router = useRouter(); const [expired, setExpired] = useState(false);
  const [replaced, setReplaced] = useState(false);
  const [error, setError] = useState(""); const [loading, setLoading] = useState(false);
  useEffect(() => { const params = new URLSearchParams(window.location.search); setExpired(params.has("expired")); setReplaced(params.has("replaced")); }, []);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(""); setLoading(true);
    const form = new FormData(event.currentTarget);
    try {
      const user = await api<User>("/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: form.get("username"), password: form.get("password") }) });
      router.push(user.role === "ADMIN" ? "/admin" : "/"); router.refresh();
    } catch (e) { setError(e instanceof Error ? e.message : "Không thể đăng nhập"); } finally { setLoading(false); }
  }
  return <main className="min-h-screen bg-[#11181e] text-white md:grid md:grid-cols-[minmax(0,1.15fr)_minmax(420px,0.85fr)]">
    <section className="relative flex min-h-[42vh] flex-col justify-between overflow-hidden border-b border-white/10 p-6 sm:p-10 md:min-h-screen md:border-b-0 md:border-r md:p-14 lg:p-20"><div className="absolute inset-y-0 right-10 hidden w-px bg-white/10 md:block" aria-hidden="true" /><div><Image src="/sbtech-logo.png" alt="SBTech" width={116} height={76} priority className="h-[76px] w-[116px] bg-white object-contain p-1" /><p className="mt-8 text-xs font-bold uppercase tracking-[0.22em] text-orange-400">SBTech · BOQ Audit</p></div><div className="relative z-10 max-w-3xl py-10 md:py-0"><h1 className="display-face text-[clamp(3.6rem,7.5vw,7.5rem)] font-extrabold uppercase leading-[0.82] tracking-[-0.035em]">Đọc kỹ.<br />Đối chiếu.<br /><span className="text-orange-500">Kết luận.</span></h1><p className="mt-7 max-w-lg text-base leading-7 text-slate-300 sm:text-lg">Không gian kiểm soát hồ sơ xây dựng, từ bản vẽ và dự toán đến báo cáo đã được duyệt.</p></div><div className="flex items-center gap-4 text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500"><span>Drawing</span><span className="h-px w-8 bg-orange-500" /><span>BOQ</span><span className="h-px w-8 bg-orange-500" /><span>Evidence</span></div></section>
    <section className="flex items-center bg-[#edf0f1] px-6 py-12 text-slate-950 sm:px-10 md:px-12 lg:px-20"><div className="w-full max-w-md"><div className="technical-rule mb-8" /><p className="eyebrow">Đăng nhập hệ thống</p><h2 className="display-face mt-2 text-5xl font-extrabold uppercase leading-none tracking-tight">Tiếp tục<br />công việc</h2><p className="mt-4 max-w-sm text-base leading-6 text-slate-600">Truy cập hồ sơ, tiến độ rà soát và kết quả đã công bố.</p>
        {expired && <p className="mt-6 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.</p>}
        {replaced && <p className="mt-6 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">Tài khoản đã đăng nhập trên trình duyệt khác. Mỗi tài khoản khách hàng chỉ dùng một phiên đăng nhập.</p>}
        {error && <p className="error mt-6">{error}</p>}
        <form onSubmit={submit} className="mt-9 space-y-5">
          <div><label htmlFor="username">Tên đăng nhập</label><input id="username" name="username" type="text" minLength={3} maxLength={80} autoComplete="username" placeholder="Nhập tên đăng nhập" required /></div>
          <div><label htmlFor="password">Mật khẩu</label><input id="password" name="password" type="password" autoComplete="current-password" placeholder="Nhập mật khẩu" required /></div>
          <button disabled={loading} className="btn-primary mt-3 w-full justify-between px-5"><span>{loading ? "Đang đăng nhập…" : "Đăng nhập"}</span><span aria-hidden="true">→</span></button>
        </form>
      </div></section>
  </main>;
}
