"use client";
import Link from "next/link";
import { FormEvent, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Shell } from "@/components/Shell";
import { api } from "@/lib/api";
import { Job } from "@/types";

export default function NewJob() {
  const router = useRouter(); const fileRef = useRef<HTMLInputElement>(null); const estimateFileRef = useRef<HTMLInputElement>(null); const narrativeFileRef = useRef<HTMLInputElement>(null);
  const [filename, setFilename] = useState(""); const [estimateFilename, setEstimateFilename] = useState(""); const [narrativeFilename, setNarrativeFilename] = useState(""); const [progress, setProgress] = useState(0); const [error, setError] = useState(""); const [loading, setLoading] = useState(false);
  const [quotaChecking, setQuotaChecking] = useState(true); const [quotaReached, setQuotaReached] = useState(false);
  useEffect(() => { api<{items: Job[]}>("/jobs").then(({items}) => setQuotaReached(items.filter(job => ["SUBMITTED", "PROCESSING", "WAITING_FOR_INFO", "REVIEW"].includes(job.status)).length >= 2)).catch(e => setError(e.message)).finally(() => setQuotaChecking(false)); }, []);
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError("");
    if (quotaReached) { setError("Bạn đang có 2 hồ sơ được xử lý. Vui lòng chờ một hồ sơ hoàn thành."); return; }
    const file = fileRef.current?.files?.[0];
    if (!file) { setError("Vui lòng chọn hồ sơ PDF."); return; }
    if (!file.name.toLowerCase().endsWith(".pdf")) { setError("Chỉ chấp nhận tệp PDF."); return; }
    const estimateFile = estimateFileRef.current?.files?.[0];
    if (estimateFile && !/\.(xlsx|xls)$/i.test(estimateFile.name)) { setError("Dự toán chỉ chấp nhận tệp Excel .xlsx hoặc .xls."); return; }
    const narrativeFile = narrativeFileRef.current?.files?.[0];
    if (narrativeFile && !/\.(pdf|doc|docx)$/i.test(narrativeFile.name)) { setError("Thuyết minh chỉ chấp nhận PDF, DOC hoặc DOCX."); return; }
    const body = new FormData(event.currentTarget); setProgress(0); setLoading(true);
    const xhr = new XMLHttpRequest(); xhr.open("POST", "/api/jobs"); xhr.withCredentials = true;
    xhr.upload.onprogress = e => { if (e.lengthComputable) setProgress(Math.round(e.loaded / e.total * 100)); };
    xhr.onload = () => { setLoading(false); if (xhr.status === 201) { const job: Job = JSON.parse(xhr.responseText); router.push(`/jobs/${job.job_code}`); } else if (xhr.status === 401) router.push("/login?expired=1"); else { try { setError(JSON.parse(xhr.responseText).detail); } catch { setError("Không thể tải hồ sơ lên."); } } };
    xhr.onerror = () => { setLoading(false); setError("Mất kết nối. Vui lòng kiểm tra mạng và thử lại."); };
    xhr.send(body);
  }
  return <Shell><div className="mx-auto max-w-2xl"><Link href="/" className="mb-5 inline-flex items-center gap-2 text-sm font-semibold text-slate-500 transition hover:text-brand">← Quay lại danh sách</Link><div className="card overflow-hidden p-0"><div className="border-b border-slate-100 bg-gradient-to-r from-cyan-50 to-white p-6 sm:p-8"><p className="eyebrow">Hồ sơ mới</p><h1 className="mt-2 text-2xl font-extrabold tracking-tight">Tạo hồ sơ tra soát</h1><p className="mt-2 text-sm text-slate-500">Tải lên bản vẽ PDF; có thể đính kèm dự toán Excel và thuyết minh Word/PDF để rà soát.</p></div><div className="p-6 sm:p-8">
    {quotaReached && <p className="mt-5 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm font-medium text-amber-800">Bạn đã đạt giới hạn 2 hồ sơ đang xử lý. Khi một hồ sơ hoàn thành, bạn có thể tạo hồ sơ mới.</p>}
    {error && <p className="error mt-5">{error}</p>}<form onSubmit={submit} className="mt-6 space-y-5">
      <div><label htmlFor="project_name">Tên công trình *</label><input id="project_name" name="project_name" maxLength={240} required /></div>
      <div><label>Hồ sơ PDF *</label><label className="group flex min-h-44 cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed border-slate-200 bg-slate-50/70 p-5 text-center transition hover:border-cyan-600 hover:bg-cyan-50/60">
        <span className="mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-white text-sm font-extrabold text-red-600 shadow-sm ring-1 ring-slate-200 transition group-hover:-translate-y-1 group-hover:shadow-md">PDF</span><span className="font-semibold text-slate-700">{filename || "Kéo file vào đây hoặc chọn file"}</span><span className="mt-1 text-xs text-slate-500">Chỉ nhận PDF · tối đa theo cấu hình hệ thống</span><input ref={fileRef} name="file" type="file" accept="application/pdf,.pdf" required className="sr-only" onChange={e => setFilename(e.target.files?.[0]?.name || "")} />
      </label></div>
      <div><label>Dự toán Excel <span className="font-normal text-slate-400">(không bắt buộc)</span></label><label className="group flex min-h-36 cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed border-slate-200 bg-slate-50/70 p-5 text-center transition hover:border-emerald-600 hover:bg-emerald-50/60">
        <span className="mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-white text-xs font-extrabold text-emerald-700 shadow-sm ring-1 ring-slate-200 transition group-hover:-translate-y-1 group-hover:shadow-md">XLS</span><span className="font-semibold text-slate-700">{estimateFilename || "Chọn file dự toán nếu có"}</span><span className="mt-1 text-xs text-slate-500">Nhận XLSX hoặc XLS · có thể bỏ qua</span><input ref={estimateFileRef} name="estimate_file" type="file" accept=".xlsx,.xls,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel" className="sr-only" onChange={e => setEstimateFilename(e.target.files?.[0]?.name || "")} />
      </label></div>
      <div><label>Thuyết minh <span className="font-normal text-slate-400">(không bắt buộc)</span></label><label className="group flex min-h-36 cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed border-slate-200 bg-slate-50/70 p-5 text-center transition hover:border-cyan-600 hover:bg-cyan-50/60">
        <span className="mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-white text-xs font-extrabold text-cyan-700 shadow-sm ring-1 ring-slate-200">DOC</span><span className="font-semibold text-slate-700">{narrativeFilename || "Chọn văn bản thuyết minh nếu có"}</span><span className="mt-1 text-xs text-slate-500">Nhận PDF, DOC hoặc DOCX · có thể bỏ qua</span><input ref={narrativeFileRef} name="narrative_file" type="file" accept=".pdf,.doc,.docx,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document" className="sr-only" onChange={e => setNarrativeFilename(e.target.files?.[0]?.name || "")} />
      </label></div>
      {loading && <div className="rounded-lg border border-cyan-200 bg-cyan-50 p-4"><div className="mb-2 flex justify-between text-xs font-semibold text-cyan-900"><span>{progress < 100 ? "Đang tải hồ sơ lên…" : "Đang tiếp nhận hồ sơ…"}</span><span>{progress}%</span></div><div className="h-2 overflow-hidden rounded bg-cyan-100"><div className="h-full bg-brand transition-all" style={{width: `${progress}%`}} /></div><p className="mt-3 text-sm text-cyan-900">{progress < 100 ? "Hồ sơ lớn có thể cần thêm một chút thời gian. Vui lòng giữ nguyên trang này." : "Hệ thống đang chuẩn bị hồ sơ để bắt đầu tra soát. Bạn sẽ được chuyển sang trang theo dõi ngay sau đây."}</p></div>}
      <button disabled={loading || quotaChecking || quotaReached} className="btn-primary w-full">{loading ? "ĐANG TẢI HỒ SƠ…" : quotaChecking ? "ĐANG KIỂM TRA…" : quotaReached ? "ĐÃ ĐẠT GIỚI HẠN 2 HỒ SƠ" : "TẠO HỒ SƠ"}</button>
    </form></div></div></div></Shell>;
}
