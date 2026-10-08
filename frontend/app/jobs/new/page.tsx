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
  return <Shell><div className="mx-auto max-w-3xl"><Link href="/" className="focus-ring mb-6 inline-flex min-h-11 items-center gap-2 text-sm font-semibold text-slate-600 transition-colors hover:text-brand">← Danh sách hồ sơ</Link><div className="technical-rule mb-6" /><div className="mb-9 grid gap-3 sm:grid-cols-[1fr_220px]"><div><p className="eyebrow">Hồ sơ mới</p><h1 className="page-title mt-2">Tạo lượt<br />tra soát</h1></div><p className="self-end text-sm leading-6 text-slate-600">Bản vẽ PDF là bắt buộc. Dự toán và thuyết minh bổ sung căn cứ đối chiếu.</p></div><div className="border-t-2 border-slate-950 py-7">
    {quotaReached && <p className="mt-5 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm font-medium text-amber-800">Bạn đã đạt giới hạn 2 hồ sơ đang xử lý. Khi một hồ sơ hoàn thành, bạn có thể tạo hồ sơ mới.</p>}
    {error && <p className="error mt-5">{error}</p>}<form onSubmit={submit} className="mt-6 space-y-5">
      <div><label htmlFor="project_name">Tên công trình *</label><input id="project_name" name="project_name" maxLength={240} required /></div>
      <div><label>Hồ sơ PDF *</label><label className="group grid min-h-28 cursor-pointer grid-cols-[64px_1fr_auto] items-center gap-4 border border-dashed border-slate-500 bg-white/40 p-4 transition-colors hover:border-orange-600 hover:bg-white focus-within:border-orange-600 focus-within:ring-2 focus-within:ring-orange-200">
        <span className="display-face flex h-14 w-14 items-center justify-center bg-red-700 text-lg font-bold text-white">PDF</span><span><span className="block font-semibold text-slate-800">{filename || "Chọn bản vẽ PDF"}</span><span className="mt-1 block text-xs text-slate-500">Tệp bắt buộc · giới hạn theo cấu hình</span></span><span className="text-xl text-slate-500" aria-hidden="true">＋</span><input ref={fileRef} name="file" type="file" accept="application/pdf,.pdf" required className="sr-only" onChange={e => setFilename(e.target.files?.[0]?.name || "")} />
      </label></div>
      <div><label>Dự toán Excel <span className="font-normal text-slate-500">(không bắt buộc)</span></label><label className="group grid min-h-24 cursor-pointer grid-cols-[64px_1fr_auto] items-center gap-4 border border-dashed border-slate-500 bg-white/40 p-4 transition-colors hover:border-orange-600 hover:bg-white focus-within:border-orange-600 focus-within:ring-2 focus-within:ring-orange-200">
        <span className="display-face flex h-14 w-14 items-center justify-center bg-emerald-800 text-base font-bold text-white">XLS</span><span><span className="block font-semibold text-slate-800">{estimateFilename || "Chọn file dự toán"}</span><span className="mt-1 block text-xs text-slate-500">XLSX hoặc XLS · có thể bỏ qua</span></span><span className="text-xl text-slate-500" aria-hidden="true">＋</span><input ref={estimateFileRef} name="estimate_file" type="file" accept=".xlsx,.xls,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel" className="sr-only" onChange={e => setEstimateFilename(e.target.files?.[0]?.name || "")} />
      </label></div>
      <div><label>Thuyết minh <span className="font-normal text-slate-500">(không bắt buộc)</span></label><label className="group grid min-h-24 cursor-pointer grid-cols-[64px_1fr_auto] items-center gap-4 border border-dashed border-slate-500 bg-white/40 p-4 transition-colors hover:border-orange-600 hover:bg-white focus-within:border-orange-600 focus-within:ring-2 focus-within:ring-orange-200">
        <span className="display-face flex h-14 w-14 items-center justify-center bg-slate-800 text-base font-bold text-white">DOC</span><span><span className="block font-semibold text-slate-800">{narrativeFilename || "Chọn văn bản thuyết minh"}</span><span className="mt-1 block text-xs text-slate-500">PDF, DOC hoặc DOCX · có thể bỏ qua</span></span><span className="text-xl text-slate-500" aria-hidden="true">＋</span><input ref={narrativeFileRef} name="narrative_file" type="file" accept=".pdf,.doc,.docx,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document" className="sr-only" onChange={e => setNarrativeFilename(e.target.files?.[0]?.name || "")} />
      </label></div>
      {loading && <div className="border border-orange-300 bg-orange-50 p-4"><div className="mb-2 flex justify-between text-xs font-semibold text-orange-950"><span>{progress < 100 ? "Đang tải hồ sơ lên…" : "Đang tiếp nhận hồ sơ…"}</span><span>{progress}%</span></div><div className="h-2 overflow-hidden bg-orange-100"><div className="h-full bg-brand transition-[width]" style={{width: `${progress}%`}} /></div><p className="mt-3 text-sm text-orange-950">{progress < 100 ? "Hồ sơ lớn có thể cần thêm một chút thời gian. Vui lòng giữ nguyên trang này." : "Hệ thống đang chuẩn bị hồ sơ để bắt đầu tra soát. Bạn sẽ được chuyển sang trang theo dõi ngay sau đây."}</p></div>}
      <button disabled={loading || quotaChecking || quotaReached} className="btn-primary w-full">{loading ? "ĐANG TẢI HỒ SƠ…" : quotaChecking ? "ĐANG KIỂM TRA…" : quotaReached ? "ĐÃ ĐẠT GIỚI HẠN 2 HỒ SƠ" : "TẠO HỒ SƠ"}</button>
    </form></div></div></Shell>;
}
