"use client";
import Link from "next/link";
import { use, useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { StatusBadge } from "@/components/StatusBadge";
import { api, fileSize, formatDate } from "@/lib/api";
import { Job, JobStatus, OutputType } from "@/types";

const steps = ["Đã nhận hồ sơ", "Đang tra soát", "Kiểm tra kết quả", "Hoàn thành"];
function stepIndex(status: JobStatus) { if (status === "COMPLETED") return 3; if (status === "REVIEW") return 2; if (["PROCESSING", "WAITING_FOR_INFO"].includes(status)) return 1; return 0; }
export default function JobDetail({ params }: { params: Promise<{job_code: string}> }) {
  const { job_code } = use(params); const [job, setJob] = useState<Job>(); const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    const load = () => api<Job>(`/jobs/${job_code}`).then(value => { if (active) setJob(value); }).catch(e => { if (active) setError(e.message); });
    load();
    const timer = window.setInterval(() => { if (active) load(); }, 15000);
    return () => { active = false; window.clearInterval(timer); };
  }, [job_code]);
  if (error) return <Shell><p className="error">{error}</p></Shell>;
  if (!job) return <Shell><p className="text-slate-500">Đang tải hồ sơ…</p></Shell>;
  const current = stepIndex(job.status);
  const output = (type: OutputType) => job.outputs.find(item => item.file_type === type);
  const downloads = [
    { item: output("EXCEL_REPORT"), label: "TẢI BÁO CÁO EXCEL" },
    { item: output("ANNOTATED_PDF"), label: "TẢI PDF ĐÁNH DẤU" },
  ];
  return <Shell><Link href="/" className="mb-5 inline-block text-sm text-slate-600">← Danh sách hồ sơ</Link>
    <div className="mb-6 flex flex-col justify-between gap-3 sm:flex-row sm:items-start"><div><p className="font-mono text-sm font-bold text-brand">{job.job_code}</p><h1 className="mt-1 text-2xl font-bold">{job.project_name}</h1><p className="mt-2 text-sm text-slate-500">Ngày gửi: {formatDate(job.created_at)}</p></div><StatusBadge status={job.status} /></div>
    {job.status === "PROCESSING" && <div className="mb-6 flex gap-4 rounded-xl border border-cyan-200 bg-cyan-50 p-5"><span className="mt-0.5 h-6 w-6 shrink-0 animate-spin rounded-full border-2 border-cyan-200 border-t-brand" aria-hidden="true" /><div><p className="font-semibold text-cyan-950">Hồ sơ đang được tra soát</p><p className="mt-1 text-sm leading-6 text-cyan-900">Hồ sơ có nhiều trang hoặc dung lượng lớn có thể cần thêm thời gian xử lý. Bạn có thể rời trang này và quay lại sau; trạng thái sẽ tự cập nhật.</p></div></div>}
    {job.status === "WAITING_FOR_INFO" && <p className="mb-6 rounded-lg border border-orange-200 bg-orange-50 p-4 text-sm text-orange-900">Hồ sơ cần được bổ sung. Vui lòng liên hệ đơn vị tra soát.</p>}
    <div className="grid gap-6 lg:grid-cols-2"><section className="card"><h2 className="font-bold">Tiến độ</h2><div className="mt-5 space-y-4">{steps.map((label, i) => <div key={label} className="flex items-center gap-3"><span className={`flex h-7 w-7 items-center justify-center rounded-full border text-xs font-bold ${i < current ? "border-emerald-600 bg-emerald-600 text-white" : i === current ? "border-brand bg-brand text-white" : "border-slate-300 text-slate-400"}`}>{i < current ? "✓" : i + 1}</span><span className={i <= current ? "font-semibold" : "text-slate-400"}>{label}</span></div>)}</div></section>
      <section className="card"><h2 className="font-bold">Thông tin hồ sơ</h2><dl className="mt-4 space-y-3 text-sm"><div><dt className="text-slate-500">Tệp đã gửi</dt><dd className="font-medium">{job.original_filename}</dd></div></dl></section>
    </div>
    <section className="card mt-6"><div className="mb-4 flex flex-col justify-between gap-3 sm:flex-row sm:items-center"><div><h2 className="font-bold">Xem hồ sơ PDF</h2><p className="mt-1 text-xs text-slate-500">Nội dung chỉ hiển thị cho tài khoản sở hữu hồ sơ.</p></div><a href={`/api/jobs/${job.job_code}/input/download`} className="btn-secondary">TẢI PDF</a></div><iframe title={`Hồ sơ ${job.job_code}`} src={`/api/jobs/${job.job_code}/input/view#toolbar=1&navpanes=0`} className="h-[70vh] min-h-[480px] w-full rounded-lg border border-line bg-slate-100" /></section>
    {job.status === "COMPLETED" && <section className="card mt-6"><h2 className="text-lg font-bold">Kết quả tra soát</h2><div className="mt-5 grid grid-cols-2 gap-4"><div className="rounded-lg bg-red-50 p-4"><p className="text-sm text-red-700">Lỗi nghiêm trọng</p><p className="mt-1 text-3xl font-bold text-red-900">{job.critical_errors}</p></div><div className="rounded-lg bg-amber-50 p-4"><p className="text-sm text-amber-700">Nội dung cần lưu ý</p><p className="mt-1 text-3xl font-bold text-amber-900">{job.warnings}</p></div></div>
      {job.customer_notes && <div className="mt-5 rounded-lg bg-slate-50 p-4 text-sm whitespace-pre-wrap">{job.customer_notes}</div>}
      <div className="mt-5 flex flex-col gap-3 sm:flex-row">{downloads.map(({item, label}) => item ? <a key={item.id} className="btn-primary" href={`/api/jobs/${job.job_code}/outputs/${item.id}/download`}>{label}<span className="ml-2 opacity-70">({fileSize(item.file_size)})</span></a> : null)}</div>
    </section>}
  </Shell>;
}
