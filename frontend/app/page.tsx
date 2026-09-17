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
  return <Shell>
    <div className="mb-8 flex flex-col justify-between gap-4 sm:flex-row sm:items-center"><div><p className="text-sm text-slate-500">Xin chào,</p><h1 className="text-2xl font-bold">{user?.name || "…"}</h1></div><Link href="/jobs/new" className="btn-primary">TẠO HỒ SƠ MỚI</Link></div>
    <section><h2 className="mb-4 text-lg font-bold">Hồ sơ gần đây</h2>{error && <p className="error">{error}</p>}
      {!error && jobs.length === 0 && <div className="card text-center"><p className="font-semibold">Chưa có hồ sơ nào</p><p className="mt-1 text-sm text-slate-500">Tạo hồ sơ đầu tiên để bắt đầu tra soát.</p></div>}
      <div className="space-y-3">{jobs.map(job => <Link key={job.job_code} href={`/jobs/${job.job_code}`} className="card flex flex-col justify-between gap-3 hover:border-slate-400 sm:flex-row sm:items-center">
        <div><p className="font-mono text-sm font-bold text-brand">{job.job_code}</p><p className="mt-1 font-semibold">{job.project_name}</p><p className="mt-1 text-xs text-slate-500">Gửi lúc {formatDate(job.created_at)}</p></div><StatusBadge status={job.status} />
      </Link>)}</div>
    </section>
  </Shell>;
}

