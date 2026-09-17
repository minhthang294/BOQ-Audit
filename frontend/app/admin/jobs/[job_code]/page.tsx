"use client";
import Link from "next/link";
import { FormEvent, use, useEffect, useRef, useState } from "react";
import { Shell } from "@/components/Shell";
import { StatusBadge } from "@/components/StatusBadge";
import { api, fileSize, formatDate } from "@/lib/api";
import { Job, JobOutput, JobStatus, OutputType } from "@/types";

const statuses: JobStatus[] = ["SUBMITTED", "PROCESSING", "WAITING_FOR_INFO", "REVIEW", "FAILED"];
function UploadBox({ jobCode, type, label, existing, onDone }: {jobCode: string; type: OutputType; label: string; existing?: JobOutput; onDone: () => void}) {
  const inputRef = useRef<HTMLInputElement>(null); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  async function upload() {
    const file = inputRef.current?.files?.[0];
    if (!file) { setError("Vui lòng chọn tệp trước khi tải lên."); return; }
    setBusy(true); setError(""); const form = new FormData(); form.set("file_type", type); form.set("file", file);
    try { await api(`/admin/jobs/${jobCode}/outputs`, { method: "POST", body: form }); if (inputRef.current) inputRef.current.value = ""; onDone(); }
    catch (err) { setError(err instanceof Error ? err.message : "Không thể upload"); }
    finally { setBusy(false); }
  }
  return <div className="rounded-lg border border-line p-4"><p className="font-semibold">{label}</p>{existing && <p className="mt-1 text-sm text-emerald-700">✓ {existing.original_filename} ({fileSize(existing.file_size)})</p>}{error && <p className="mt-2 text-xs text-red-700">{error}</p>}<div className="mt-3 flex flex-col gap-2 sm:flex-row"><input ref={inputRef} type="file" accept={type === "ANNOTATED_PDF" ? ".pdf,application/pdf" : ".xlsx,.xls"} className="text-sm" /><button type="button" onClick={upload} disabled={busy} className="btn-secondary shrink-0">{busy ? "Đang tải…" : existing ? "THAY TỆP" : "TẢI LÊN"}</button></div></div>;
}

export default function AdminJobDetail({ params }: {params: Promise<{job_code: string}>}) {
  const { job_code } = use(params); const [job, setJob] = useState<Job>(); const [error, setError] = useState(""); const [saved, setSaved] = useState(""); const [busy, setBusy] = useState(false);
  const load = () => api<Job>(`/admin/jobs/${job_code}`).then(setJob).catch(e => setError(e.message)); useEffect(() => { load(); }, [job_code]);
  async function save(e: FormEvent<HTMLFormElement>) { e.preventDefault(); setBusy(true); setError(""); setSaved(""); const form = new FormData(e.currentTarget); try { const updated = await api<Job>(`/admin/jobs/${job_code}`, { method: "PATCH", headers: {"Content-Type":"application/json"}, body: JSON.stringify({ status: form.get("status"), critical_errors: Number(form.get("critical_errors")), warnings: Number(form.get("warnings")), admin_notes: form.get("admin_notes"), customer_notes: form.get("customer_notes") }) }); setJob(updated); setSaved("Đã lưu thay đổi."); } catch (err) { setError(err instanceof Error ? err.message : "Không thể lưu"); } finally { setBusy(false); } }
  async function complete() { if (!confirm("Xác nhận hoàn thành hồ sơ? Khách hàng sẽ thấy và tải được kết quả.")) return; setBusy(true); try { setJob(await api<Job>(`/admin/jobs/${job_code}/complete`, {method:"POST"})); setSaved("Hồ sơ đã hoàn thành."); } catch (err) { setError(err instanceof Error ? err.message : "Không thể hoàn thành"); } finally { setBusy(false); } }
  if (!job) return <Shell admin>{error ? <p className="error">{error}</p> : <p>Đang tải…</p>}</Shell>;
  const getOutput = (type: OutputType) => job.outputs.find(o => o.file_type === type); const ready = !!getOutput("EXCEL_REPORT") && !!getOutput("ANNOTATED_PDF");
  return <Shell admin><Link href="/admin" className="mb-5 inline-block text-sm text-slate-600">← Danh sách hồ sơ</Link><div className="mb-6 flex flex-col justify-between gap-3 sm:flex-row"><div><p className="font-mono text-sm font-bold text-brand">{job.job_code}</p><h1 className="mt-1 text-2xl font-bold">{job.project_name}</h1></div><StatusBadge status={job.status} /></div>
    {error && <p className="error mb-5">{error}</p>}{saved && <p className="mb-5 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">{saved}</p>}
    <div className="grid gap-6 lg:grid-cols-[1fr_1.4fr]"><div className="space-y-6"><section className="card"><h2 className="font-bold">Khách hàng</h2><p className="mt-3 font-semibold">{job.user?.name}</p><p className="text-sm text-slate-500">@{job.user?.username}</p><p className="mt-3 text-xs text-slate-500">Gửi lúc {formatDate(job.created_at)}</p></section><section className="card"><h2 className="font-bold">Hồ sơ gốc</h2><p className="mt-3 break-all text-sm">{job.original_filename}</p><a href={`/api/admin/jobs/${job.job_code}/input/download`} className="btn-secondary mt-4 w-full">TẢI PDF GỐC</a></section></div>
      <form onSubmit={save} className="card space-y-5"><h2 className="text-lg font-bold">Kết quả tra soát</h2><div><label htmlFor="status">Trạng thái</label><select id="status" name="status" defaultValue={job.status} disabled={job.status === "COMPLETED"}>{job.status === "COMPLETED" && <option>COMPLETED</option>}{statuses.map(s => <option key={s}>{s}</option>)}</select></div><div className="grid grid-cols-2 gap-4"><div><label htmlFor="critical">Lỗi nghiêm trọng</label><input id="critical" name="critical_errors" type="number" min="0" defaultValue={job.critical_errors} required /></div><div><label htmlFor="warnings">Cảnh báo / lưu ý</label><input id="warnings" name="warnings" type="number" min="0" defaultValue={job.warnings} required /></div></div><div><label htmlFor="admin_notes">Ghi chú nội bộ</label><textarea id="admin_notes" name="admin_notes" rows={3} defaultValue={job.admin_notes} /></div><div><label htmlFor="customer_notes">Ghi chú cho khách hàng</label><textarea id="customer_notes" name="customer_notes" rows={3} defaultValue={job.customer_notes} /></div><UploadBox jobCode={job.job_code} type="EXCEL_REPORT" label="Báo cáo Excel" existing={getOutput("EXCEL_REPORT")} onDone={load} /><UploadBox jobCode={job.job_code} type="ANNOTATED_PDF" label="PDF đánh dấu" existing={getOutput("ANNOTATED_PDF")} onDone={load} /><div className="flex flex-col gap-3 border-t pt-5 sm:flex-row"><button disabled={busy || job.status === "COMPLETED"} className="btn-secondary flex-1">LƯU THAY ĐỔI</button><button type="button" onClick={complete} disabled={busy || !ready || job.status === "COMPLETED"} className="btn-primary flex-1">HOÀN THÀNH HỒ SƠ</button></div>{!ready && job.status !== "COMPLETED" && <p className="text-xs text-amber-700">Cần đủ báo cáo Excel và PDF đánh dấu để hoàn thành.</p>}</form></div>
  </Shell>;
}
