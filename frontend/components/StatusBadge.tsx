import { JobStatus } from "@/types";

export const statusText: Record<JobStatus, string> = {
  SUBMITTED: "Đã nhận hồ sơ", PROCESSING: "Đang tra soát", WAITING_FOR_INFO: "Cần bổ sung hồ sơ",
  REVIEW: "Đang kiểm tra kết quả", COMPLETED: "Hoàn thành", FAILED: "Có lỗi xử lý",
};

const colors: Record<JobStatus, string> = {
  SUBMITTED: "bg-sky-50 text-sky-800 border-sky-200", PROCESSING: "bg-amber-50 text-amber-800 border-amber-200",
  WAITING_FOR_INFO: "bg-orange-50 text-orange-800 border-orange-200", REVIEW: "bg-violet-50 text-violet-800 border-violet-200",
  COMPLETED: "bg-emerald-50 text-emerald-800 border-emerald-200", FAILED: "bg-red-50 text-red-800 border-red-200",
};

export function StatusBadge({ status }: { status: JobStatus }) {
  return <span className={`inline-flex rounded-full border px-3 py-1 text-xs font-semibold ${colors[status]}`}>{statusText[status]}</span>;
}

