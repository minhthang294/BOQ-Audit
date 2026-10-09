"use client";
import { useEffect, useState } from "react";
import { formatDuration } from "@/lib/api";
import { Job } from "@/types";

export function JobTiming({ job }: { job: Job }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const started = performance.now();
    setElapsed(0);
    if (["COMPLETED", "FAILED"].includes(job.status) && job.current_attempt_seconds == null) return;
    const timer = window.setInterval(() => setElapsed((performance.now() - started) / 1000), 1000);
    return () => window.clearInterval(timer);
  }, [job]);
  const current = job.current_attempt_seconds == null ? 0 : job.current_attempt_seconds + elapsed;
  return <section className="card" aria-label="Thời gian xử lý">
    <h2 className="font-bold">Thời gian xử lý</h2>
    <dl className="mt-3 space-y-3 text-sm">
      <div><dt className="text-slate-500">Tổng thời gian từ khi nhận hồ sơ</dt><dd className="mt-1 font-semibold">{formatDuration(job.turnaround_seconds + (["COMPLETED", "FAILED"].includes(job.status) ? 0 : elapsed))}</dd></div>
      <div><dt className="text-slate-500">AI xử lý · {job.audit_runs.length} lần chạy</dt><dd className="mt-1 font-semibold">{formatDuration(job.ai_processing_seconds == null ? null : job.ai_processing_seconds + current)}</dd></div>
      {job.current_attempt_seconds != null && <div><dt className="text-slate-500">Lần chạy hiện tại</dt><dd className="mt-1 font-semibold">{formatDuration(current)}</dd></div>}
    </dl>
    {!job.ai_timing_complete && <p className="mt-3 text-xs text-amber-700">{job.audit_runs.length ? "Thời gian AI chưa đủ do có lần chạy bị gián đoạn; chỉ cộng các lần đo được." : "Chưa có dữ liệu thời gian AI cho hồ sơ này."}</p>}
    <p className="mt-3 text-xs text-slate-500">Tổng thời gian gồm chờ, thử lại và duyệt kết quả; không phải dự báo hoàn thành.</p>
  </section>;
}
