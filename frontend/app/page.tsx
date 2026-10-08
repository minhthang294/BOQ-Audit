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
    <section className="mb-10"><div className="technical-rule mb-6" /><div className="grid gap-6 md:grid-cols-[1fr_auto] md:items-end"><div><p className="eyebrow">Không gian làm việc</p><h1 className="page-title mt-2">Hồ sơ của<br />{user?.name || "…"}</h1><p className="mt-4 max-w-xl text-base leading-6 text-slate-600">Theo dõi các lượt đối chiếu bản vẽ, dự toán và bằng chứng trong một danh sách duy nhất.</p></div>{quotaReached ? <span aria-disabled="true" className="btn border-slate-400 bg-slate-200 text-slate-500">Đã đạt giới hạn</span> : <Link href="/jobs/new" className="btn-primary justify-between gap-8">Tạo hồ sơ <span aria-hidden="true">＋</span></Link>}</div><div className="mt-8 grid border-y border-slate-400 sm:grid-cols-[150px_1fr]"><div className="border-b border-slate-400 py-4 sm:border-b-0 sm:border-r"><span className="display-face text-4xl font-bold tabular-nums">{activeJobs}</span><span className="ml-2 text-sm font-semibold text-slate-600">/ 2 đang xử lý</span></div><div className="flex items-center px-0 py-4 sm:px-6"><div className="h-2 w-full bg-slate-300"><div className={`h-full ${quotaReached ? "bg-amber-600" : "bg-orange-600"}`} style={{width: `${activeJobs * 50}%`}} /></div></div></div></section>
    <section><div className="mb-4 grid gap-4 lg:grid-cols-[1fr_minmax(320px,440px)] lg:items-end"><div><h2 className="section-title">Danh sách hồ sơ <span className="font-normal text-slate-500">{normalizedQuery ? `${visibleJobs.length}/${jobs.length}` : jobs.length}</span></h2></div><div className="relative"><label htmlFor="job-search" className="sr-only">Tìm hồ sơ</label><svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" className="pointer-events-none absolute left-3.5 top-1/2 h-5 w-5 -translate-y-1/2 text-slate-500"><circle cx="11" cy="11" r="7" strokeWidth="2"/><path d="m20 20-3.5-3.5" strokeWidth="2" strokeLinecap="round"/></svg><input id="job-search" type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Tìm mã, công trình, tệp…" className="border-slate-500 bg-transparent pl-11" /></div></div>{error && <p className="error">{error}</p>}
      {!error && jobs.length === 0 && <div className="border border-dashed border-slate-500 p-10"><p className="display-face text-2xl font-bold uppercase">Chưa có hồ sơ</p><p className="mt-2 text-slate-600">Tạo hồ sơ đầu tiên để bắt đầu tra soát.</p></div>}
      {!error && jobs.length > 0 && visibleJobs.length === 0 && <div className="border border-dashed border-slate-500 p-10"><p className="display-face text-2xl font-bold uppercase">Không tìm thấy</p><p className="mt-2 text-slate-600">Thử mã hồ sơ, tên công trình hoặc tên tệp khác.</p></div>}
      <div className="border-t-2 border-slate-950">{visibleJobs.map(job => <Link key={job.job_code} href={`/jobs/${job.job_code}`} className="focus-ring group grid gap-4 border-b border-slate-400 py-5 transition-colors hover:bg-white/70 sm:px-4 lg:grid-cols-[130px_minmax(0,1fr)_220px_180px] lg:items-center">
        <div><p className="font-mono text-xs font-bold tracking-wide text-orange-700">{job.job_code}</p><p className="mt-1 text-xs text-slate-500">{formatDate(job.created_at)}</p></div><div className="min-w-0"><p className="truncate text-lg font-bold text-slate-950 transition-colors group-hover:text-orange-700">{job.project_name}</p><p className="mt-1 truncate text-sm text-slate-600">{job.original_filename}</p><AuditProgress job={job} compact /></div><div className="grid grid-cols-2 gap-4 text-xs text-slate-600"><p><span className="block uppercase tracking-wide text-slate-500">Tổng</span><span className="mt-1 block font-semibold tabular-nums text-slate-900">{formatDuration(job.turnaround_seconds)}</span></p><p><span className="block uppercase tracking-wide text-slate-500">AI</span><span className="mt-1 block font-semibold tabular-nums text-slate-900">{formatDuration(job.ai_processing_seconds == null ? null : job.ai_processing_seconds + (job.current_attempt_seconds || 0))}</span></p></div><div className="flex items-center justify-between gap-3 lg:justify-end"><StatusBadge status={job.status} /><span aria-hidden="true" className="display-face text-2xl text-slate-500 transition-transform group-hover:translate-x-1 group-hover:text-orange-600">→</span></div>
      </Link>)}</div>
    </section>
  </Shell>;
}
