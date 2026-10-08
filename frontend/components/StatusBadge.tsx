import { JobStatus } from "@/types";

export const statusText: Record<JobStatus, string> = {
  SUBMITTED: "Đã nhận hồ sơ", PROCESSING: "Đang rà soát", WAITING_FOR_INFO: "Cần bổ sung hồ sơ",
  REVIEW: "Đang kiểm tra kết quả", COMPLETED: "Đã có kết quả", FAILED: "Có lỗi xử lý",
};

const colors: Record<JobStatus, string> = {
  SUBMITTED: "bg-sky-50 text-sky-900 border-sky-300", PROCESSING: "bg-amber-50 text-amber-900 border-amber-300",
  WAITING_FOR_INFO: "bg-amber-50 text-amber-900 border-amber-300", REVIEW: "bg-brand-soft text-brand-strong border-brand",
  COMPLETED: "bg-emerald-50 text-emerald-800 border-emerald-200", FAILED: "bg-red-50 text-red-800 border-red-200",
};

export function StatusBadge({ status }: { status: JobStatus }) {
  return <span className={`inline-flex items-center gap-2 border px-2.5 py-1.5 text-xs font-bold uppercase tracking-wide ${colors[status]}`}><span className="h-1.5 w-1.5 bg-current opacity-70" />{statusText[status]}</span>;
}
