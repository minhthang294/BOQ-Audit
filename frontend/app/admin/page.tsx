"use client";
import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { StatusBadge } from "@/components/StatusBadge";
import { api, formatDate } from "@/lib/api";
import { Job, JobStatus } from "@/types";

export default function AdminDashboard() {
  const [jobs, setJobs] = useState<Job[]>([]); const [error, setError] = useState(""); const [query, setQuery] = useState(""); const [status, setStatus] = useState("");
  function load(search = query, filter = status) { const params = new URLSearchParams(); if (search) params.set("search", search); if (filter) params.set("status", filter); api<{items: Job[]}>(`/admin/jobs?${params}`).then(r => setJobs(r.items)).catch(e => setError(e.message)); }
  useEffect(() => { load("", ""); }, []);
  function submit(e: FormEvent) { e.preventDefault(); load(); }
  const counts = { total: jobs.length, waiting: jobs.filter(j => j.status === "SUBMITTED").length, active: jobs.filter(j => ["PROCESSING", "REVIEW"].includes(j.status)).length, done: jobs.filter(j => j.status === "COMPLETED").length };
  const stats = [{label: "Tổng hồ sơ", value: counts.total, color: "bg-slate-800"}, {label: "Đang chờ", value: counts.waiting, color: "bg-sky-500"}, {label: "Đang xử lý", value: counts.active, color: "bg-amber-500"}, {label: "Hoàn thành", value: counts.done, color: "bg-emerald-500"}];
  return <Shell admin><p className="eyebrow">Tổng quan vận hành</p><h1 className="mt-1 text-3xl font-extrabold tracking-tight">Quản lý hồ sơ</h1><p className="mt-2 text-sm text-slate-500">Theo dõi và xử lý toàn bộ hồ sơ khách hàng tại một nơi.</p><div className="mt-6 grid grid-cols-2 gap-3 lg:grid-cols-4">{stats.map(({label, value, color}) => <div className="card relative overflow-hidden" key={label}><span className={`absolute inset-y-0 left-0 w-1 ${color}`} /><p className="text-sm font-medium text-slate-500">{label}</p><p className="mt-2 text-3xl font-extrabold tracking-tight text-slate-800">{value}</p></div>)}</div>
    <form onSubmit={submit} className="card mt-6 grid gap-3 sm:grid-cols-[1fr_240px_auto]"><div><label htmlFor="search">Tìm kiếm</label><input id="search" value={query} onChange={e => setQuery(e.target.value)} placeholder="Mã, công trình, khách hàng…" /></div><div><label htmlFor="status">Trạng thái</label><select id="status" value={status} onChange={e => setStatus(e.target.value)}><option value="">Tất cả</option>{["SUBMITTED","PROCESSING","WAITING_FOR_INFO","REVIEW","COMPLETED","FAILED"].map(s => <option key={s}>{s}</option>)}</select></div><button className="btn-primary self-end">LỌC</button></form>
    {error && <p className="error mt-5">{error}</p>}<div className="mt-5 overflow-hidden rounded-2xl bg-white/90 shadow-[0_8px_30px_rgba(15,23,42,0.06)] ring-1 ring-slate-200/70"><div className="hidden grid-cols-[140px_1fr_1fr_170px_150px] gap-4 border-b border-slate-100 bg-slate-50/80 px-5 py-3 text-xs font-bold uppercase tracking-wide text-slate-500 md:grid"><span>Mã hồ sơ</span><span>Công trình</span><span>Khách hàng</span><span>Trạng thái</span><span>Ngày gửi</span></div>{jobs.map(job => <Link href={`/admin/jobs/${job.job_code}`} key={job.job_code} className="grid gap-2 border-b border-slate-100 px-5 py-4 transition last:border-0 hover:bg-cyan-50/50 md:grid-cols-[140px_1fr_1fr_170px_150px] md:items-center"><span className="font-mono text-sm font-bold text-brand">{job.job_code}</span><span className="font-semibold">{job.project_name}</span><span className="text-sm text-slate-600">{job.user?.name}</span><StatusBadge status={job.status as JobStatus} /><span className="text-xs text-slate-500">{formatDate(job.created_at)}</span></Link>)}{jobs.length === 0 && <p className="p-8 text-center text-sm text-slate-500">Không có hồ sơ phù hợp.</p>}</div>
  </Shell>;
}
