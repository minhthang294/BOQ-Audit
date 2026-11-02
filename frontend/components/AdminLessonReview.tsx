"use client";

import { FormEvent, useEffect, useState } from "react";
import { api } from "@/lib/api";

type Lesson = {
  lesson_id: string;
  version: number;
  status: string;
  error_class: string;
  applicability_check_kinds: string[];
  applicability_target_ids: string[];
  trigger_conditions: string[];
  required_checks: string[];
  designated_reviewer_id?: string | null;
  project_scope: string;
};

const lines = (value: FormDataEntryValue | null) => String(value ?? "").split("\n").map((item) => item.trim()).filter(Boolean);

export function AdminLessonReview({ jobCode }: { jobCode: string }) {
  const [lessons, setLessons] = useState<Lesson[]>([]);
  const [reviewer, setReviewer] = useState("");
  const [evidence, setEvidence] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const load = () => api<{ items: Lesson[] }>("/admin/lessons?status=CANDIDATE&limit=500").then((result) => setLessons(result.items)).catch((err) => setError(err.message));
  useEffect(() => { void load(); }, []);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setBusy(true); setError(""); setMessage("");
    try {
      await api("/admin/lessons", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
        error_class: form.get("error_class"), applicability_check_kinds: lines(form.get("check_kinds")), applicability_target_ids: lines(form.get("target_ids")),
        trigger_conditions: lines(form.get("triggers")), required_checks: lines(form.get("required_checks")), counterexamples: lines(form.get("counterexamples")),
        evidence_refs: lines(form.get("evidence_refs")), project_scope: jobCode, designated_reviewer_email: form.get("reviewer"),
      }) });
      formElement.reset(); setMessage("Đã tạo lesson candidate; lesson chưa được dùng cho đến khi reviewer xác minh."); await load();
    } catch (err) { setError(err instanceof Error ? err.message : "Không tạo được lesson"); }
    finally { setBusy(false); }
  }

  async function decide(lesson: Lesson, result: "VERIFIED" | "REJECTED") {
    setBusy(true); setError(""); setMessage("");
    try {
      await api(`/admin/lessons/${encodeURIComponent(lesson.lesson_id)}/versions/${lesson.version}/review`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ result, reason, evidence_refs: lines(evidence) }) });
      setMessage(`Đã ghi quyết định ${result} cho ${lesson.lesson_id} v${lesson.version}.`); setReason(""); setEvidence(""); await load();
    } catch (err) { setError(err instanceof Error ? err.message : "Không lưu được quyết định"); }
    finally { setBusy(false); }
  }

  return <details className="mt-4 rounded border border-slate-200 p-3 text-xs">
    <summary className="cursor-pointer font-semibold">Bài học kiểm tra theo hồ sơ này</summary>
    <p className="mt-2 text-slate-600">Lesson chỉ áp dụng đúng project scope <code>{jobCode}</code> và check kind/target đã khai báo. Candidate chưa được đưa vào audit cho đến khi reviewer được chỉ định xác minh.</p>
    {error && <p role="alert" className="mt-2 text-red-700">{error}</p>}{message && <p role="status" className="mt-2 text-emerald-800">{message}</p>}
    <form onSubmit={create} className="mt-3 grid gap-2 border-t border-slate-100 pt-3 md:grid-cols-2">
      <label>Nhóm lỗi <input name="error_class" required maxLength={120} className="input mt-1 w-full" /></label>
      <label>Email reviewer được chỉ định <input name="reviewer" type="email" required className="input mt-1 w-full" /></label>
      <label>Check kind chính xác, mỗi dòng một giá trị <textarea name="check_kinds" className="input mt-1 w-full" rows={2} /></label>
      <label>Target ID chính xác, mỗi dòng một giá trị <textarea name="target_ids" className="input mt-1 w-full" rows={2} /></label>
      <label>Điều kiện kích hoạt, mỗi dòng một mục <textarea name="triggers" required className="input mt-1 w-full" rows={2} /></label>
      <label>Các bước cần kiểm, mỗi dòng một mục <textarea name="required_checks" required className="input mt-1 w-full" rows={2} /></label>
      <label>Phản ví dụ, mỗi dòng một mục <textarea name="counterexamples" className="input mt-1 w-full" rows={2} /></label>
      <label>Bằng chứng nguồn, mỗi dòng một tham chiếu <textarea name="evidence_refs" required className="input mt-1 w-full" rows={2} /></label>
      <button className="btn-secondary md:col-span-2" disabled={busy}>TẠO LESSON CANDIDATE</button>
    </form>
    {lessons.filter((lesson) => lesson.project_scope === jobCode).map((lesson) => <article key={`${lesson.lesson_id}-${lesson.version}`} className="mt-3 border-t border-slate-200 pt-3">
      <p className="font-semibold">{lesson.lesson_id} · v{lesson.version} · {lesson.error_class}</p>
      <p className="mt-1 text-slate-600">Reviewer: {lesson.designated_reviewer_id ?? "chưa chỉ định"} · check kind: {lesson.applicability_check_kinds.join(", ") || "—"} · target: {lesson.applicability_target_ids.join(", ") || "—"}</p>
      <p className="mt-1">{lesson.required_checks.join("; ")}</p>
      <div className="mt-2 grid gap-2 md:grid-cols-2"><label>Lý do quyết định <textarea className="input mt-1 w-full" rows={2} value={reason} onChange={(event) => setReason(event.target.value)} /></label><label>Tham chiếu bằng chứng, mỗi dòng một mục <textarea className="input mt-1 w-full" rows={2} value={evidence} onChange={(event) => setEvidence(event.target.value)} /></label></div>
      <div className="mt-2 flex gap-2"><button type="button" className="btn-secondary" disabled={busy || reason.trim().length < 12 || !lines(evidence).length} onClick={() => void decide(lesson, "VERIFIED")}>XÁC MINH</button><button type="button" className="btn-secondary" disabled={busy || reason.trim().length < 12 || !lines(evidence).length} onClick={() => void decide(lesson, "REJECTED")}>TỪ CHỐI</button></div>
    </article>)}
  </details>;
}
