"use client";
import Link from "next/link";
import { FormEvent, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Shell } from "@/components/Shell";
import { Job } from "@/types";

export default function NewJob() {
  const router = useRouter(); const fileRef = useRef<HTMLInputElement>(null);
  const [filename, setFilename] = useState(""); const [progress, setProgress] = useState(0); const [error, setError] = useState(""); const [loading, setLoading] = useState(false);
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError("");
    const file = fileRef.current?.files?.[0];
    if (!file) { setError("Vui lòng chọn hồ sơ PDF."); return; }
    if (!file.name.toLowerCase().endsWith(".pdf")) { setError("Chỉ chấp nhận tệp PDF."); return; }
    const body = new FormData(event.currentTarget); setProgress(0); setLoading(true);
    const xhr = new XMLHttpRequest(); xhr.open("POST", "/api/jobs"); xhr.withCredentials = true;
    xhr.upload.onprogress = e => { if (e.lengthComputable) setProgress(Math.round(e.loaded / e.total * 100)); };
    xhr.onload = () => { setLoading(false); if (xhr.status === 201) { const job: Job = JSON.parse(xhr.responseText); router.push(`/jobs/${job.job_code}`); } else if (xhr.status === 401) router.push("/login?expired=1"); else { try { setError(JSON.parse(xhr.responseText).detail); } catch { setError("Không thể tải hồ sơ lên."); } } };
    xhr.onerror = () => { setLoading(false); setError("Mất kết nối. Vui lòng kiểm tra mạng và thử lại."); };
    xhr.send(body);
  }
  return <Shell><div className="mx-auto max-w-2xl"><Link href="/" className="mb-5 inline-block text-sm text-slate-600">← Quay lại</Link><div className="card p-6 sm:p-8"><h1 className="text-2xl font-bold">Tạo hồ sơ tra soát</h1><p className="mt-2 text-sm text-slate-500">Điền thông tin và tải lên một tệp PDF.</p>
    {error && <p className="error mt-5">{error}</p>}<form onSubmit={submit} className="mt-6 space-y-5">
      <div><label htmlFor="project_name">Tên công trình *</label><input id="project_name" name="project_name" maxLength={240} required /></div>
      <div><label>Hồ sơ PDF *</label><label className="flex min-h-36 cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed border-slate-300 bg-slate-50 p-5 text-center hover:border-brand">
        <span className="font-semibold text-slate-700">{filename || "Kéo file vào đây hoặc chọn file"}</span><span className="mt-1 text-xs text-slate-500">Tối đa theo cấu hình hệ thống</span><input ref={fileRef} name="file" type="file" accept="application/pdf,.pdf" required className="sr-only" onChange={e => setFilename(e.target.files?.[0]?.name || "")} />
      </label></div>
      {loading && <div className="rounded-lg border border-cyan-200 bg-cyan-50 p-4"><div className="mb-2 flex justify-between text-xs font-semibold text-cyan-900"><span>{progress < 100 ? "Đang tải hồ sơ lên…" : "Đang tiếp nhận hồ sơ…"}</span><span>{progress}%</span></div><div className="h-2 overflow-hidden rounded bg-cyan-100"><div className="h-full bg-brand transition-all" style={{width: `${progress}%`}} /></div><p className="mt-3 text-sm text-cyan-900">{progress < 100 ? "Hồ sơ lớn có thể cần thêm một chút thời gian. Vui lòng giữ nguyên trang này." : "Hệ thống đang chuẩn bị hồ sơ để bắt đầu tra soát. Bạn sẽ được chuyển sang trang theo dõi ngay sau đây."}</p></div>}
      <button disabled={loading} className="btn-primary w-full">{loading ? "ĐANG TẢI HỒ SƠ…" : "TẠO HỒ SƠ"}</button>
    </form></div></div></Shell>;
}
