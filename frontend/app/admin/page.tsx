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
  return <Shell admin><h1 className="text-2xl font-bold">Quản lý hồ sơ</h1><div className="mt-6 grid grid-cols-2 gap-3 lg:grid-cols-4">{[["Tổng hồ sơ", counts.total], ["Đang chờ", counts.waiting], ["Đang xử lý", counts.active], ["Hoàn thành", counts.done]].map(([label, value]) => <div className="card" key={label}><p className="text-sm text-slate-500">{label}</p><p className="mt-1 text-2xl font-bold">{value}</p></div>)}</div>
    <form onSubmit={submit} className="card mt-6 grid gap-3 sm:grid-cols-[1fr_240px_auto]"><div><label htmlFor="search">Tìm kiếm</label><input id="search" value={query} onChange={e => setQuery(e.target.value)} placeholder="Mã, công trình, khách hàng…" /></div><div><label htmlFor="status">Trạng thái</label><select id="status" value={status} onChange={e => setStatus(e.target.value)}><option value="">Tất cả</option>{["SUBMITTED","PROCESSING","WAITING_FOR_INFO","REVIEW","COMPLETED","FAILED"].map(s => <option key={s}>{s}</option>)}</select></div><button className="btn-primary self-end">LỌC</button></form>
    {error && <p className="error mt-5">{error}</p>}<div className="mt-5 overflow-hidden rounded-xl border border-line bg-white"><div className="hidden grid-cols-[140px_1fr_1fr_170px_150px] gap-4 border-b bg-slate-50 px-5 py-3 text-xs font-bold uppercase text-slate-500 md:grid"><span>Mã hồ sơ</span><span>Công trình</span><span>Khách hàng</span><span>Trạng thái</span><span>Ngày gửi</span></div>{jobs.map(job => <Link href={`/admin/jobs/${job.job_code}`} key={job.job_code} className="grid gap-2 border-b border-line px-5 py-4 last:border-0 hover:bg-slate-50 md:grid-cols-[140px_1fr_1fr_170px_150px] md:items-center"><span className="font-mono text-sm font-bold text-brand">{job.job_code}</span><span className="font-semibold">{job.project_name}</span><span className="text-sm text-slate-600">{job.user?.name}</span><StatusBadge status={job.status as JobStatus} /><span className="text-xs text-slate-500">{formatDate(job.created_at)}</span></Link>)}{jobs.length === 0 && <p className="p-8 text-center text-sm text-slate-500">Không có hồ sơ phù hợp.</p>}</div>
  </Shell>;
}

