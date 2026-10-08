"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { AuditProgress } from "@/components/AuditProgress";
import { Shell } from "@/components/Shell";
import { StatusBadge } from "@/components/StatusBadge";
import { api, formatDate, formatDuration } from "@/lib/api";
import { Job, User } from "@/types";

export default function Dashboard() {
  const [user, setUser] = useState<User>(); const [jobs, setJobs] = useState<Job[]>([]); const [error, setError] = useState(""); const [query, setQuery] = useState("");
  useEffect(() => {
    let active = true;
    const load = () => Promise.all([api<User>("/auth/me"), api<{items: Job[]}>("/jobs")])
      .then(([u, j]) => { if (active) { setUser(u); setJobs(j.items); setError(""); } })
      .catch(e => { if (active) setError(e.message); });
    void load();
    const timer = window.setInterval(() => { void load(); }, 30000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);
  const activeJobs = jobs.filter(job => ["SUBMITTED", "PROCESSING", "WAITING_FOR_INFO", "REVIEW"].includes(job.status)).length;
  const quotaReached = activeJobs >= 2;
  const normalizedQuery = query.trim().toLocaleLowerCase("vi");
  const visibleJobs = jobs.filter(job => `${job.job_code} ${job.project_name} ${job.original_filename}`.toLocaleLowerCase("vi").includes(normalizedQuery));
  return <Shell>
    <section className="mb-8 border-b border-slate-200 pb-8"><div className="flex flex-col justify-between gap-6 sm:flex-row sm:items-end"><div><p className="eyebrow">Không gian làm việc</p><h1 className="page-title mt-2">Xin chào, {user?.name || "…"}</h1><p className="mt-2 max-w-xl text-sm leading-6 text-slate-600">Theo dõi hồ sơ, xem Codex đang rà soát đến đâu và mở kết quả đã được duyệt.</p></div>{quotaReached ? <span aria-disabled="true" className="btn border border-slate-200 bg-slate-100 text-slate-400">ĐÃ ĐẠT GIỚI HẠN</span> : <Link href="/jobs/new" className="btn-primary">＋ TẠO HỒ SƠ MỚI</Link>}</div><div className="mt-6 flex max-w-md items-center gap-3"><div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-200"><div className={`h-full rounded-full ${quotaReached ? "bg-amber-500" : "bg-cyan-700"}`} style={{width: `${activeJobs * 50}%`}} /></div><p className="shrink-0 text-sm font-semibold text-slate-600">{activeJobs}/2 đang xử lý</p></div></section>
    <section><div className="mb-5 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between"><div><p className="eyebrow">Hồ sơ của bạn</p><h2 className="section-title mt-1">Tất cả hồ sơ <span className="font-normal text-slate-400">({jobs.length})</span></h2></div><div className="relative w-full sm:max-w-sm"><label htmlFor="job-search" className="sr-only">Tìm hồ sơ</label><svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" className="pointer-events-none absolute left-3.5 top-1/2 h-5 w-5 -translate-y-1/2 text-slate-400"><circle cx="11" cy="11" r="7" strokeWidth="2"/><path d="m20 20-3.5-3.5" strokeWidth="2" strokeLinecap="round"/></svg><input id="job-search" type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Tìm theo mã, công trình hoặc tên tệp…" className="pl-11" /></div></div>{error && <p className="error">{error}</p>}
      {!error && jobs.length === 0 && <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center"><p className="font-semibold">Chưa có hồ sơ nào</p><p className="mt-1 text-sm text-slate-500">Tạo hồ sơ đầu tiên để bắt đầu tra soát.</p></div>}
      {!error && jobs.length > 0 && visibleJobs.length === 0 && <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center"><p className="font-semibold">Không tìm thấy hồ sơ</p><p className="mt-1 text-sm text-slate-500">Thử mã hồ sơ, tên công trình hoặc tên tệp khác.</p></div>}
      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">{visibleJobs.map(job => <Link key={job.job_code} href={`/jobs/${job.job_code}`} className="focus-ring group grid gap-4 border-b border-slate-100 px-4 py-5 transition last:border-0 hover:bg-slate-50 sm:grid-cols-[minmax(0,1fr)_220px_auto] sm:items-center sm:px-5">
        <div className="min-w-0"><div className="flex items-center gap-2"><p className="font-mono text-xs font-bold tracking-wide text-cyan-800">{job.job_code}</p><span className="text-slate-300">•</span><p className="truncate text-xs text-slate-500">{formatDate(job.created_at)}</p></div><p className="mt-1 truncate font-bold text-slate-900 transition group-hover:text-cyan-900">{job.project_name}</p><p className="mt-1 truncate text-xs text-slate-500">{job.original_filename}</p><AuditProgress job={job} compact /></div><div className="text-xs leading-5 text-slate-500"><p>Tổng: <span className="font-medium tabular-nums text-slate-700">{formatDuration(job.turnaround_seconds)}</span></p><p>AI: <span className="font-medium tabular-nums text-slate-700">{formatDuration(job.ai_processing_seconds == null ? null : job.ai_processing_seconds + (job.current_attempt_seconds || 0))}</span></p></div><div className="flex items-center justify-between gap-3 sm:justify-end"><StatusBadge status={job.status} /><span aria-hidden="true" className="text-xl text-slate-300 transition group-hover:translate-x-0.5 group-hover:text-cyan-800">›</span></div>
      </Link>)}</div>
    </section>
  </Shell>;
}
