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
    <div className="mb-1.5 flex items-center justify-between gap-3 text-xs"><span className="truncate font-semibold text-slate-700">{currentLabel}</span><span className="shrink-0 font-mono tabular-nums text-slate-500">{percent}%</span></div>
    <div className="h-1 overflow-hidden bg-slate-300"><div className={`h-full ${failed ? "bg-red-600" : "bg-orange-600"}`} style={{ width: `${percent}%` }} /></div>
  </div>;

  return <section className="overflow-hidden bg-[#11181e] text-white" aria-labelledby="audit-progress-title">
    <div className="flex flex-wrap items-start justify-between gap-4 border-b border-white/15 px-5 py-5 sm:px-7"><div><p className="text-xs font-bold uppercase tracking-[0.18em] text-orange-400">Quy trình BOQ Audit</p><h2 id="audit-progress-title" className="display-face mt-1 text-3xl font-bold uppercase leading-none">{currentLabel}</h2></div><span className="border border-white/25 px-3 py-1 text-xs font-bold uppercase tracking-wider text-slate-200">{complete ? "Hoàn tất" : failed ? "Tạm dừng" : "Đang xử lý"}</span></div>
    <div className="h-1 bg-white/15" aria-hidden="true"><div className={`h-full transition-[width] duration-500 ${failed ? "bg-red-500" : "bg-orange-500"}`} style={{ width: `${percent}%` }} /></div>
    <ol className="grid sm:grid-cols-2 lg:grid-cols-4">
      {stages.map((stage, index) => {
        const done = complete || index < current;
        const active = !complete && !failed && job.status !== "WAITING_FOR_INFO" && index === current;
        const state = done ? "Đã xong" : active ? "Đang thực hiện" : "Chưa bắt đầu";
        return <li key={stage} className={`flex min-h-20 items-start gap-3 border-b border-r border-white/10 px-4 py-4 text-sm ${active ? "bg-orange-500 text-slate-950" : done ? "text-white" : "text-slate-500"}`}>
          <span className={`display-face flex h-7 w-7 shrink-0 items-center justify-center border text-base font-bold ${done ? "border-orange-500 bg-orange-500 text-slate-950" : active ? "border-slate-950 text-slate-950" : "border-slate-600"}`}>{done ? "✓" : index + 1}</span>
          <span className={active ? "font-bold" : "font-medium"}>{stage}<span className="sr-only"> — {state}</span></span>
        </li>;
      })}
    </ol>
    {!complete && !failed && <p className="px-5 py-4 text-xs leading-5 text-slate-400 sm:px-7">Tiến độ ước tính từ trạng thái và thời gian xử lý. Codex có thể quay lại bước trước để kiểm tra chéo.</p>}
  </section>;
}
