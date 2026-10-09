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
  const running = job.status === "PROCESSING";
  const percent = complete ? 100 : Math.max(4, Math.round(current / stages.length * 100));
  const currentLabel = complete ? "Đã công bố kết quả" : failed ? "Quy trình tạm dừng" : job.status === "WAITING_FOR_INFO" ? "Chờ bổ sung hồ sơ" : stages[Math.min(current, stages.length - 1)];

  if (compact) return <div className="mt-3" aria-label={`Tiến độ: ${currentLabel}`}>
    <div className="mb-1.5 flex items-center justify-between gap-3 text-xs"><span className="truncate font-semibold text-slate-700">{currentLabel}</span><span className="shrink-0 font-mono tabular-nums text-slate-500">{percent}%</span></div>
    <div className="progress-track h-1.5" role="progressbar" aria-label="Tiến độ ước tính" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}><span className={`progress-fill ${failed ? "bg-signal" : "bg-brand"} ${running ? "progress-fill-active" : ""}`} style={{ width: `${percent}%` }} /></div>
  </div>;

  return <section className="audit-progress overflow-hidden" aria-labelledby="audit-progress-title">
    <div className="flex flex-wrap items-center gap-3 px-4 pb-3 pt-4 sm:px-5">
      <span className={`audit-progress-icon ${running ? "audit-progress-icon-active" : ""} ${failed ? "audit-progress-icon-failed" : ""}`} aria-hidden="true">{complete ? "✓" : failed ? "!" : String(Math.min(current + 1, stages.length)).padStart(2, "0")}</span>
      <div className="min-w-0 flex-1"><p className="text-[10px] font-bold uppercase tracking-[0.2em] text-brand">Tiến trình tra soát · ước tính</p><h2 id="audit-progress-title" aria-live="polite" className="mt-0.5 text-base font-semibold leading-snug text-ink sm:text-lg">{currentLabel}</h2></div>
      <div className="flex items-center gap-2"><span className="hidden text-xs font-medium text-muted sm:inline">{complete ? "Đủ 8 bước" : `Bước ${Math.min(current + 1, stages.length)}/${stages.length}`}</span><span className={`font-mono text-sm font-bold tabular-nums ${failed ? "text-signal-strong" : "text-brand-strong"}`}>{percent}%</span></div>
    </div>
    <div className="px-4 pb-3 sm:px-5"><div className="progress-track h-2" role="progressbar" aria-label="Tiến độ ước tính" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}><span className={`progress-fill ${failed ? "bg-signal" : "bg-brand"} ${running ? "progress-fill-active" : ""}`} style={{ width: `${percent}%` }} /></div></div>
    <details className="audit-progress-details border-t border-line/70">
      <summary className="focus-ring flex min-h-10 cursor-pointer items-center justify-between gap-3 px-4 py-2 text-xs font-semibold text-slate-600 transition-colors hover:bg-brand-soft hover:text-brand-strong sm:px-5"><span>Xem sơ đồ 8 bước</span><span className="audit-progress-chevron text-base" aria-hidden="true">⌄</span></summary>
      <ol className="grid border-t border-line/70 sm:grid-cols-2 xl:grid-cols-4">
        {stages.map((stage, index) => {
          const done = complete || index < current;
          const active = !complete && !failed && job.status !== "WAITING_FOR_INFO" && index === current;
          const state = done ? "Đã xong" : active ? "Đang thực hiện" : "Chưa bắt đầu";
          return <li key={stage} className={`audit-progress-step flex items-start gap-2.5 border-b border-line/60 px-4 py-3 text-xs sm:px-5 ${active ? "audit-progress-step-active font-semibold text-brand-strong" : done ? "text-ink" : "text-slate-500"}`}>
            <span className={`font-mono text-[11px] font-bold tabular-nums ${done ? "text-brand" : active ? "text-signal" : "text-slate-400"}`} aria-hidden="true">{done ? "✓" : String(index + 1).padStart(2, "0")}</span>
            <span>{stage}<span className="sr-only"> — {state}</span></span>
          </li>;
        })}
      </ol>
      {!complete && !failed && <p className="px-4 py-2 text-xs leading-5 text-slate-500 sm:px-5">Các bước được ước tính từ trạng thái và thời gian xử lý; AI có thể quay lại để kiểm tra chéo.</p>}
    </details>
  </section>;
}
