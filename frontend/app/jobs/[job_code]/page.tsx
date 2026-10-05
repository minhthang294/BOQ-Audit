"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { StatusBadge } from "@/components/StatusBadge";
import { JobTiming } from "@/components/JobTiming";
import { ProjectChat } from "@/components/ProjectChat";
import { api, fileSize, formatDate } from "@/lib/api";
import { AICapacity, Job, OutputType } from "@/types";

export default function JobDetail({ params }: { params: Promise<{job_code: string}> }) {
  const { job_code } = use(params); const router = useRouter(); const [job, setJob] = useState<Job>(); const [error, setError] = useState(""); const [actionError, setActionError] = useState(""); const [busy, setBusy] = useState(false);
  const [usage, setUsage] = useState<AICapacity>();
  useEffect(() => {
    let active = true;
    const load = () => api<Job>(`/jobs/${job_code}`).then(value => { if (active) setJob(value); }).catch(e => { if (active) setError(e.message); });
    load();
    const timer = window.setInterval(() => { if (active) load(); }, 5000);
    return () => { active = false; window.clearInterval(timer); };
  }, [job_code]);
  useEffect(() => {
    let active = true;
    const loadUsage = () => api<AICapacity>("/jobs/ai-capacity").then(value => { if (active) setUsage(value); }).catch(() => undefined);
    loadUsage();
    const timer = window.setInterval(loadUsage, 60_000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);
  if (error) return <Shell><p className="error">{error}</p></Shell>;
  if (!job) return <Shell><p className="text-slate-500">Đang tải hồ sơ…</p></Shell>;
  const output = (type: OutputType) => job.outputs.find(item => item.file_type === type);
  const downloads = [
    { item: output("EXCEL_REPORT"), label: "TẢI BÁO CÁO RÀ SOÁT BOQ" },
    { item: output("ESTIMATE_REPORT"), label: "TẢI BÁO CÁO RÀ SOÁT DỰ TOÁN" },
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
  async function retryJob() {
    setBusy(true); setActionError("");
    try { setJob(await api<Job>(`/jobs/${job_code}/retry`, { method: "POST" })); }
    catch (err) { setActionError(err instanceof Error ? err.message : "Không thể thử lại hồ sơ."); }
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
    {usage?.available && <section className="card mb-5 text-sm"><span className="font-semibold">Năng lực AI: </span>{usage.ready === false ? "Đang tạm hết hạn mức xử lý" : usage.ready === true ? "Sẵn sàng xử lý" : "Đang kiểm tra khả năng xử lý"}</section>}
    <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_340px] 2xl:grid-cols-[minmax(0,1fr)_380px]">
      <div className="min-w-0 space-y-5">
      <section className="rounded-xl border border-line bg-white p-3 shadow-sm sm:p-4">
        <div className="mb-3 flex flex-col justify-between gap-3 sm:flex-row sm:items-center"><div><h2 className="font-bold">{viewerTitle}</h2><p className="mt-1 text-xs text-slate-500">{showAnnotatedPdf ? "Đang hiển thị kết quả PDF đã đánh dấu lỗi." : "Đang hiển thị PDF bạn đã gửi."}</p></div><a href={showAnnotatedPdf ? `/api/jobs/${job.job_code}/outputs/${annotatedPdf.id}/download` : `/api/jobs/${job.job_code}/input/download`} className="btn-secondary shrink-0">TẢI PDF</a></div>
        <iframe key={viewerSource} title={`${viewerTitle} ${job.job_code}`} src={viewerSource} className="h-[68vh] min-h-[500px] w-full rounded-lg border border-line bg-slate-100 xl:h-[calc(100vh-210px)] xl:min-h-[650px]" />
      </section>
        <section className="card"><h2 className="font-bold">Tệp đầu vào</h2><div className="mt-3 space-y-3"><div><p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Bản vẽ PDF</p><p className="mt-1 break-all text-sm font-medium">{job.original_filename}</p></div>{job.estimate_input && <div className="border-t border-slate-100 pt-3"><p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Dự toán Excel</p><p className="mt-1 break-all text-sm font-medium">{job.estimate_input.original_filename} <span className="font-normal text-slate-400">({fileSize(job.estimate_input.file_size)})</span></p><a href={`/api/jobs/${job.job_code}/estimate/download`} className="btn-secondary mt-3 w-full">TẢI DỰ TOÁN</a></div>}{job.narrative_input && <div className="border-t border-slate-100 pt-3"><p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Thuyết minh</p><p className="mt-1 break-all text-sm font-medium">{job.narrative_input.original_filename} <span className="font-normal text-slate-400">({fileSize(job.narrative_input.file_size)})</span></p><a href={`/api/jobs/${job.job_code}/narrative/download`} className="btn-secondary mt-3 w-full">TẢI THUYẾT MINH</a></div>}</div></section>
      </div>
      <aside className="space-y-5 xl:sticky xl:top-5">
        {job.status === "SUBMITTED" && <div className="flex gap-3 rounded-xl border border-slate-200 bg-slate-50 p-4"><span className="mt-0.5 h-5 w-5 shrink-0 animate-pulse rounded-full bg-slate-400" aria-hidden="true" /><div><p className="font-semibold text-slate-900">Đang xếp hàng cho AI</p><p className="mt-1 text-sm leading-5 text-slate-700">Hồ sơ đã nhận; AI sẽ tự động bắt đầu rà soát.</p></div></div>}
        {job.status === "PROCESSING" && <div className="flex gap-3 rounded-xl border border-cyan-200 bg-cyan-50 p-4"><span className="mt-0.5 h-5 w-5 shrink-0 animate-spin rounded-full border-2 border-cyan-200 border-t-brand" aria-hidden="true" /><div><p className="font-semibold text-cyan-950">Đang rà soát hồ sơ</p><p className="mt-1 text-sm leading-5 text-cyan-900">Hồ sơ lớn có thể cần thêm thời gian. Trạng thái tự cập nhật mỗi 5 giây.</p></div></div>}
        {job.status === "FAILED" && <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-900"><p>Audit tự động chưa hoàn tất.</p><button type="button" onClick={retryJob} disabled={busy} className="btn-primary mt-3 w-full">{busy ? "ĐANG THỬ LẠI…" : "THỬ LẠI AUDIT"}</button></div>}
        {job.status === "WAITING_FOR_INFO" && <p className="rounded-xl border border-orange-200 bg-orange-50 p-4 text-sm text-orange-900">Hồ sơ cần được bổ sung. Vui lòng liên hệ đơn vị tra soát.</p>}
        <JobTiming job={job} />
        <ProjectChat jobCode={job.job_code} version={job.updated_at} />
        {job.status === "COMPLETED" && <section className="card"><h2 className="font-bold">Kết quả tra soát</h2><div className="mt-4 grid grid-cols-2 gap-3"><div className="rounded-lg bg-red-50 p-3"><p className="text-xs text-red-700">Lỗi nghiêm trọng</p><p className="mt-1 text-2xl font-bold text-red-900">{job.critical_errors}</p></div><div className="rounded-lg bg-amber-50 p-3"><p className="text-xs text-amber-700">Cần lưu ý</p><p className="mt-1 text-2xl font-bold text-amber-900">{job.warnings}</p></div></div>{job.customer_notes && <div className="customer-message-scroll rich-text-content mt-4 rounded-xl border border-slate-100 bg-slate-50 p-4 text-sm leading-6" dangerouslySetInnerHTML={{ __html: job.customer_notes }} />}<div className="mt-4 space-y-2">{downloads.map(({item, label}) => item ? <a key={item.id} className="btn-primary w-full" href={`/api/jobs/${job.job_code}/outputs/${item.id}/download`}>{label}<span className="ml-2 text-xs opacity-70">({fileSize(item.file_size)})</span></a> : null)}</div></section>}
      </aside>
    </div>
  </Shell>;
}
