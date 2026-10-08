"use client";

import { Job } from "@/types";

const stages = [
  "Tiếp nhận hồ sơ",
  "Kiểm kê tài liệu",
  "Lập bản đồ bằng chứng",
  "Đối chiếu BOQ và bản vẽ",
  "Rà soát độc lập lượt 2",
  "Tái kiểm tra và đóng cảnh báo",
  "Kiểm tra báo cáo đầu ra",
  "Duyệt và công bố",
];

function progressIndex(job: Job) {
  if (job.status === "COMPLETED") return stages.length;
  if (job.status === "REVIEW") return stages.length - 1;
  if (job.status === "SUBMITTED") return 0;
  if (job.status === "WAITING_FOR_INFO") return 2;
  const seconds = (job.ai_processing_seconds || 0) + (job.current_attempt_seconds || 0);
  if (seconds < 60) return 1;
  if (seconds < 180) return 2;
  if (seconds < 420) return 3;
  if (seconds < 720) return 4;
  if (seconds < 960) return 5;
  return 6;
}

export function AuditProgress({ job, compact = false }: { job: Job; compact?: boolean }) {
  const current = progressIndex(job);
  const complete = job.status === "COMPLETED";
  const failed = job.status === "FAILED";
  const percent = complete ? 100 : Math.max(4, Math.round(current / stages.length * 100));
  const currentLabel = complete ? "Đã công bố kết quả" : failed ? "Quy trình tạm dừng" : job.status === "WAITING_FOR_INFO" ? "Chờ bổ sung hồ sơ" : stages[Math.min(current, stages.length - 1)];

  if (compact) return <div className="mt-3" aria-label={`Tiến độ: ${currentLabel}`}>
    <div className="mb-1.5 flex items-center justify-between gap-3 text-xs"><span className="truncate font-medium text-slate-600">{currentLabel}</span><span className="shrink-0 tabular-nums text-slate-400">{percent}%</span></div>
    <div className="h-1.5 overflow-hidden rounded-full bg-slate-200"><div className={`h-full rounded-full ${failed ? "bg-red-500" : "bg-cyan-700"}`} style={{ width: `${percent}%` }} /></div>
  </div>;

  return <section className="card" aria-labelledby="audit-progress-title">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><p className="eyebrow">Quy trình BOQ Audit</p><h2 id="audit-progress-title" className="section-title mt-1">{currentLabel}</h2></div><span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-600">{complete ? "Hoàn tất" : failed ? "Tạm dừng" : "Đang xử lý"}</span></div>
    <div className="mt-5 h-2 overflow-hidden rounded-full bg-slate-200" aria-hidden="true"><div className={`h-full rounded-full transition-[width] duration-500 ${failed ? "bg-red-500" : "bg-cyan-700"}`} style={{ width: `${percent}%` }} /></div>
    <ol className="mt-5 grid gap-3 sm:grid-cols-2">
      {stages.map((stage, index) => {
        const done = complete || index < current;
        const active = !complete && index === current;
        return <li key={stage} className={`flex min-h-11 items-start gap-3 rounded-lg px-3 py-2 text-sm ${active ? failed ? "bg-red-50 text-red-900" : "bg-cyan-50 text-cyan-950" : "text-slate-600"}`}>
          <span className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-bold ${done ? "bg-cyan-800 text-white" : active ? failed ? "border-2 border-red-500 bg-white text-red-700" : "border-2 border-cyan-700 bg-white text-cyan-800" : "border border-slate-300 bg-white text-slate-400"}`}>{done ? "✓" : index + 1}</span>
          <span className={active ? "font-semibold" : ""}>{stage}</span>
        </li>;
      })}
    </ol>
    {!complete && !failed && <p className="mt-4 text-xs leading-5 text-slate-500">Bước hiện tại được ước tính từ trạng thái và thời gian xử lý; Codex có thể quay lại bước trước khi kiểm tra chéo.</p>}
  </section>;
}
