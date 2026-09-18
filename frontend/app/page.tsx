"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { StatusBadge } from "@/components/StatusBadge";
import { api, formatDate } from "@/lib/api";
import { Job, User } from "@/types";

export default function Dashboard() {
  const [user, setUser] = useState<User>(); const [jobs, setJobs] = useState<Job[]>([]); const [error, setError] = useState("");
  useEffect(() => { Promise.all([api<User>("/auth/me"), api<{items: Job[]}>("/jobs")]).then(([u, j]) => { setUser(u); setJobs(j.items); }).catch(e => setError(e.message)); }, []);
  const activeJobs = jobs.filter(job => ["SUBMITTED", "PROCESSING", "WAITING_FOR_INFO", "REVIEW"].includes(job.status)).length;
  const quotaReached = activeJobs >= 2;
  return <Shell>
    <section className="relative mb-8 overflow-hidden rounded-3xl bg-gradient-to-br from-slate-900 via-cyan-950 to-cyan-800 p-6 text-white shadow-xl shadow-cyan-950/15 sm:p-8"><div className="absolute -right-16 -top-20 h-64 w-64 rounded-full border-[40px] border-white/5" /><div className="relative flex flex-col justify-between gap-6 sm:flex-row sm:items-center"><div><p className="text-sm font-medium text-cyan-100">Xin chào,</p><h1 className="mt-1 text-3xl font-extrabold tracking-tight">{user?.name || "…"}</h1><div className="mt-4 flex items-center gap-3"><div className="h-2 w-28 overflow-hidden rounded-full bg-white/15"><div className={`h-full rounded-full ${quotaReached ? "bg-amber-400" : "bg-cyan-300"}`} style={{width: `${activeJobs * 50}%`}} /></div><p className={`text-sm ${quotaReached ? "font-semibold text-amber-200" : "text-cyan-100"}`}>{activeJobs}/2 hồ sơ đang xử lý</p></div>{quotaReached && <p className="mt-2 text-xs text-amber-200">Bạn có thể tạo tiếp khi một hồ sơ hoàn thành.</p>}</div>{quotaReached ? <span aria-disabled="true" className="btn cursor-not-allowed border border-white/10 bg-white/10 text-white/50">ĐÃ ĐẠT GIỚI HẠN</span> : <Link href="/jobs/new" className="btn bg-white text-cyan-950 shadow-lg hover:-translate-y-0.5 hover:shadow-xl">＋ TẠO HỒ SƠ MỚI</Link>}</div></section>
    <section><div className="mb-4 flex items-end justify-between"><div><p className="eyebrow">Quản lý hồ sơ</p><h2 className="mt-1 text-xl font-extrabold tracking-tight">Hồ sơ gần đây</h2></div><span className="text-sm text-slate-500">{jobs.length} hồ sơ</span></div>{error && <p className="error">{error}</p>}
      {!error && jobs.length === 0 && <div className="card text-center"><p className="font-semibold">Chưa có hồ sơ nào</p><p className="mt-1 text-sm text-slate-500">Tạo hồ sơ đầu tiên để bắt đầu tra soát.</p></div>}
      <div className="space-y-3">{jobs.map(job => <Link key={job.job_code} href={`/jobs/${job.job_code}`} className="card group flex flex-col justify-between gap-3 transition duration-200 hover:-translate-y-0.5 hover:ring-cyan-200 sm:flex-row sm:items-center">
        <div className="flex items-center gap-4"><span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-cyan-50 text-lg font-bold text-cyan-800 ring-1 ring-cyan-100">PDF</span><div><p className="font-mono text-xs font-bold tracking-wide text-cyan-800">{job.job_code}</p><p className="mt-1 font-bold text-slate-800 transition group-hover:text-brand">{job.project_name}</p><p className="mt-1 text-xs text-slate-500">Gửi lúc {formatDate(job.created_at)}</p></div></div><div className="flex items-center justify-between gap-4"><StatusBadge status={job.status} /><span className="text-xl text-slate-300 transition group-hover:translate-x-1 group-hover:text-brand">›</span></div>
      </Link>)}</div>
    </section>
  </Shell>;
}
