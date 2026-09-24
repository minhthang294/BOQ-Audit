"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { StatusBadge } from "@/components/StatusBadge";
import { api, fileSize, formatDate } from "@/lib/api";
import { Job, JobStatus, OutputType } from "@/types";

const steps = [
  "Đọc đầy đủ BOQ Audit v6 và kiểm kê trang, tờ, bảng trong PDF",
  "Dựng mô hình cấu, bóc tách độc lập và rà từng dòng BOQ/bảng tổng hợp",
  "Đối chiếu hai chiều, kiểm chuỗi khối lượng và rà soát vòng hai",
  "Xuất Excel chi tiết và PDF đánh dấu; kiểm tra độ phủ và vị trí đánh dấu",
];
const FIRST_STEP_MS = 4 * 60 * 1000;
const SECOND_STEP_MS = 10 * 60 * 1000;
function progressState(status: JobStatus, elapsedMs: number) {
  if (status === "COMPLETED") return { completed: steps.length, active: -1 };
  if (status === "SUBMITTED") return { completed: 0, active: 0 };
  if (status === "REVIEW") return { completed: 2, active: 2 };

  // The API exposes job status, but not per-action progress. Estimate the first
  // two stages from started_at so the list can advance while a job is processing.
  const completed = elapsedMs < FIRST_STEP_MS ? 0 : elapsedMs < FIRST_STEP_MS + SECOND_STEP_MS ? 1 : 2;
  if (status === "FAILED") return { completed, active: -1 };
  return { completed, active: completed };
}
export default function JobDetail({ params }: { params: Promise<{job_code: string}> }) {
  const { job_code } = use(params); const router = useRouter(); const [job, setJob] = useState<Job>(); const [error, setError] = useState(""); const [actionError, setActionError] = useState(""); const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    let active = true;
    const load = () => api<Job>(`/jobs/${job_code}`).then(value => { if (active) setJob(value); }).catch(e => { if (active) setError(e.message); });
    load();
    const timer = window.setInterval(() => { if (active) load(); }, 15000);
    return () => { active = false; window.clearInterval(timer); };
  }, [job_code]);
  if (error) return <Shell><p className="error">{error}</p></Shell>;
  if (!job) return <Shell><p className="text-slate-500">Đang tải hồ sơ…</p></Shell>;
  const startedAt = job.started_at ? new Date(job.started_at).getTime() : new Date(job.created_at).getTime();
  const progress = progressState(job.status, Math.max(0, now - startedAt));
  const output = (type: OutputType) => job.outputs.find(item => item.file_type === type);
  const downloads = [
    { item: output("EXCEL_REPORT"), label: "TẢI BÁO CÁO EXCEL" },
    { item: output("ANNOTATED_PDF"), label: "TẢI PDF ĐÁNH DẤU" },
  ];
  const annotatedPdf = output("ANNOTATED_PDF");
  const showAnnotatedPdf = job.status === "COMPLETED" && !!annotatedPdf;
  const viewerTitle = showAnnotatedPdf ? "PDF đã đánh dấu lỗi" : "Hồ sơ PDF đã gửi";
  const viewerSource = showAnnotatedPdf
    ? `/api/jobs/${job.job_code}/outputs/${annotatedPdf.id}/view#toolbar=1&navpanes=0`
    : `/api/jobs/${job.job_code}/input/view#toolbar=1&navpanes=0`;
  async function renameJob() {
    const projectName = window.prompt("Tên hồ sơ mới", job?.project_name || "");
    if (projectName === null) return;
    if (!projectName.trim()) { setActionError("Tên hồ sơ không được để trống."); return; }
    setBusy(true); setActionError("");
    try { setJob(await api<Job>(`/jobs/${job_code}`, { method: "PATCH", headers: {"Content-Type":"application/json"}, body: JSON.stringify({project_name: projectName}) })); }
    catch (err) { setActionError(err instanceof Error ? err.message : "Không thể đổi tên hồ sơ."); }
    finally { setBusy(false); }
  }
  async function deleteJob() {
    if (!window.confirm(`Xóa hồ sơ ${job?.job_code} và toàn bộ tệp liên quan? Thao tác này không thể hoàn tác.`)) return;
    setBusy(true); setActionError("");
    try { await api(`/jobs/${job_code}`, {method: "DELETE"}); router.push("/"); router.refresh(); }
    catch (err) { setActionError(err instanceof Error ? err.message : "Không thể xóa hồ sơ."); setBusy(false); }
  }
  return <Shell wide><Link href="/" className="mb-4 inline-block text-sm text-slate-600">← Danh sách hồ sơ</Link>
    <div className="mb-5 flex flex-col justify-between gap-3 sm:flex-row sm:items-start"><div><p className="font-mono text-sm font-bold text-brand">{job.job_code}</p><h1 className="mt-1 text-2xl font-bold">{job.project_name}</h1><p className="mt-1 text-sm text-slate-500">Ngày gửi: {formatDate(job.created_at)}</p><div className="mt-3 flex flex-wrap gap-2"><button type="button" onClick={renameJob} disabled={busy} className="btn-secondary">ĐỔI TÊN</button><button type="button" onClick={deleteJob} disabled={busy} className="btn-danger">XÓA HỒ SƠ</button></div></div><StatusBadge status={job.status} /></div>
    {actionError && <p className="error mb-5">{actionError}</p>}
    <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_340px] 2xl:grid-cols-[minmax(0,1fr)_380px]">
      <section className="min-w-0 rounded-xl border border-line bg-white p-3 shadow-sm sm:p-4">
        <div className="mb-3 flex flex-col justify-between gap-3 sm:flex-row sm:items-center"><div><h2 className="font-bold">{viewerTitle}</h2><p className="mt-1 text-xs text-slate-500">{showAnnotatedPdf ? "Đang hiển thị kết quả PDF đã đánh dấu lỗi." : "Đang hiển thị PDF bạn đã gửi."}</p></div><a href={showAnnotatedPdf ? `/api/jobs/${job.job_code}/outputs/${annotatedPdf.id}/download` : `/api/jobs/${job.job_code}/input/download`} className="btn-secondary shrink-0">TẢI PDF</a></div>
        <iframe key={viewerSource} title={`${viewerTitle} ${job.job_code}`} src={viewerSource} className="h-[68vh] min-h-[500px] w-full rounded-lg border border-line bg-slate-100 xl:h-[calc(100vh-210px)] xl:min-h-[650px]" />
      </section>
      <aside className="space-y-5 xl:sticky xl:top-5">
        {job.status === "PROCESSING" && <div className="flex gap-3 rounded-xl border border-cyan-200 bg-cyan-50 p-4"><span className="mt-0.5 h-5 w-5 shrink-0 animate-spin rounded-full border-2 border-cyan-200 border-t-brand" aria-hidden="true" /><div><p className="font-semibold text-cyan-950">Đang rà soát hồ sơ</p><p className="mt-1 text-sm leading-5 text-cyan-900">Hồ sơ lớn có thể cần thêm thời gian. Trạng thái tự cập nhật mỗi 15 giây.</p></div></div>}
        {job.status === "WAITING_FOR_INFO" && <p className="rounded-xl border border-orange-200 bg-orange-50 p-4 text-sm text-orange-900">Hồ sơ cần được bổ sung. Vui lòng liên hệ đơn vị tra soát.</p>}
        <section className="card" aria-labelledby="progress-title"><h2 id="progress-title" className="font-bold">Tiến độ</h2><ol className="mt-4 space-y-4">{steps.map((label, i) => {
          const done = i < progress.completed;
          const active = i === progress.active;
          return <li key={label} className="flex items-start gap-3" aria-current={active ? "step" : undefined}>
            <span className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border ${done ? "border-slate-400 bg-slate-400 text-white" : active ? "border-brand" : "border-slate-300"}`} aria-hidden="true">
              {done ? <span className="text-xs leading-none">✓</span> : active ? <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-200 border-t-brand" /> : null}
            </span>
            <span className={`text-sm leading-6 ${active ? "font-medium text-ink" : done ? "text-slate-500" : "text-slate-400"}`}>{label}</span>
          </li>;
        })}</ol></section>
        {job.status === "COMPLETED" && <section className="card"><h2 className="font-bold">Kết quả tra soát</h2><div className="mt-4 grid grid-cols-2 gap-3"><div className="rounded-lg bg-red-50 p-3"><p className="text-xs text-red-700">Lỗi nghiêm trọng</p><p className="mt-1 text-2xl font-bold text-red-900">{job.critical_errors}</p></div><div className="rounded-lg bg-amber-50 p-3"><p className="text-xs text-amber-700">Cần lưu ý</p><p className="mt-1 text-2xl font-bold text-amber-900">{job.warnings}</p></div></div>{job.customer_notes && <div className="mt-4 whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-sm">{job.customer_notes}</div>}<div className="mt-4 space-y-2">{downloads.map(({item, label}) => item ? <a key={item.id} className="btn-primary w-full" href={`/api/jobs/${job.job_code}/outputs/${item.id}/download`}>{label}<span className="ml-2 text-xs opacity-70">({fileSize(item.file_size)})</span></a> : null)}</div></section>}
        <section className="card"><h2 className="font-bold">Tệp hồ sơ</h2><p className="mt-2 break-all text-sm font-medium">{job.original_filename}</p></section>
      </aside>
    </div>
  </Shell>;
}
