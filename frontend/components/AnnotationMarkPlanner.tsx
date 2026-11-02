"use client";

import { useEffect, useState } from "react";
import type { PointerEvent } from "react";
import { api } from "@/lib/api";
import type { AdminAnnotationDraft, AdminCheckpointIssues } from "@/types";

type Point = [number, number];
type Box = [number, number, number, number];
type Mark = {
  mark_id: string; issue_id: string; page_id: string; dpi: number; render_size_px: [number, number];
  target_bbox_px: Box; note_bbox_px: Box; leader_points_px: Point[]; note: string;
  snapshot_digest: string; classification_digest: string;
};

function inside(point: Point, box: Box) {
  return box[0] <= point[0] && point[0] <= box[2] && box[1] <= point[1] && point[1] <= box[3];
}

export function AnnotationMarkPlanner({ jobCode, snapshotDigest, classificationDigest, onCreated }: {
  jobCode: string; snapshotDigest: string; classificationDigest: string; onCreated: (draft: AdminAnnotationDraft) => void;
}) {
  const [issues, setIssues] = useState<AdminCheckpointIssues["issues"]>([]);
  const [issueId, setIssueId] = useState("");
  const [pageId, setPageId] = useState("");
  const [dpi, setDpi] = useState(300);
  const [image, setImage] = useState<{ url: string; width: number; height: number }>();
  const [mode, setMode] = useState<"target" | "note" | "leader">("target");
  const [start, setStart] = useState<Point>();
  const [preview, setPreview] = useState<Box>();
  const [target, setTarget] = useState<Box>();
  const [noteBox, setNoteBox] = useState<Box>();
  const [leader, setLeader] = useState<Point[]>([]);
  const [noteText, setNoteText] = useState("");
  const [marks, setMarks] = useState<Mark[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const issue = issues.find((item) => item.issue_id === issueId);
  const pages = issue?.source_refs.page_ids ?? [];
  const fullNote = issue ? `${issue.severity} · ${issue.issue_id} — ${noteText.trim()}` : "";
  const severityColor = ({ CRITICAL: "#C00000", HIGH: "#E53935", MEDIUM: "#ED7D31", LOW: "#4472C4", NOTE: "#00A6A6" } as Record<string, string>)[issue?.severity ?? ""] ?? "#E53935";

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const rows: AdminCheckpointIssues["issues"] = [];
        for (let offset = 0; ; offset += 100) {
          const page = await api<AdminCheckpointIssues>(`/admin/jobs/${encodeURIComponent(jobCode)}/checkpoint/issues?offset=${offset}&limit=100`);
          if (page.snapshot_digest !== snapshotDigest || page.classification_digest !== classificationDigest) throw new Error("Issue ledger đã đổi; tải lại trang.");
          rows.push(...page.issues);
          if (rows.length >= page.total) break;
        }
        if (active) setIssues(rows.filter((row) => row.classified && row.severity && !["FALSE_POSITIVE", "CLOSED_WITH_EXPLANATION"].includes(row.final_status) && row.source_refs.page_ids.length > 0));
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "Không tải được issue ledger.");
      }
    }
    void load();
    return () => { active = false; };
  }, [jobCode, snapshotDigest, classificationDigest]);

  useEffect(() => {
    if (!pageId) { setImage(undefined); return; }
    setImage(undefined);
    const controller = new AbortController();
    let url = "";
    async function load() {
      try {
        const response = await fetch(`/api/admin/jobs/${encodeURIComponent(jobCode)}/checkpoint/annotation-source/${encodeURIComponent(pageId)}?dpi=${dpi}`, { credentials: "include", cache: "no-store", signal: controller.signal });
        if (!response.ok) {
          const detail = await response.json().catch(() => null) as { detail?: string } | null;
          throw new Error(detail?.detail ?? `Không render được trang nguồn (${response.status}).`);
        }
        if (response.headers.get("x-audit-snapshot-digest") !== snapshotDigest || response.headers.get("x-classification-digest") !== classificationDigest) throw new Error("Trang nguồn đã đổi; tải lại hồ sơ.");
        const width = Number(response.headers.get("x-render-width"));
        const height = Number(response.headers.get("x-render-height"));
        if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1) throw new Error("Kích thước ảnh nguồn không hợp lệ.");
        url = URL.createObjectURL(await response.blob());
        if (!controller.signal.aborted) { setImage({ url, width, height }); setError(""); }
      } catch (cause) {
        if (!controller.signal.aborted) { setImage(undefined); setError(cause instanceof Error ? cause.message : "Không tải được ảnh nguồn."); }
      }
    }
    void load();
    return () => { controller.abort(); if (url) URL.revokeObjectURL(url); };
  }, [jobCode, pageId, dpi, snapshotDigest, classificationDigest]);

  useEffect(() => { setMarks([]); setIssueId(""); setPageId(""); resetGeometry(); }, [snapshotDigest, classificationDigest]);

  function resetGeometry() {
    setTarget(undefined); setNoteBox(undefined); setLeader([]); setStart(undefined); setPreview(undefined);
  }

  function point(event: PointerEvent<SVGSVGElement>): Point {
    const rect = event.currentTarget.getBoundingClientRect();
    return [Math.max(0, Math.min(image!.width, Math.round((event.clientX - rect.left) * image!.width / rect.width))), Math.max(0, Math.min(image!.height, Math.round((event.clientY - rect.top) * image!.height / rect.height)))];
  }

  function pointerDown(event: PointerEvent<SVGSVGElement>) {
    if (!image) return;
    const current = point(event);
    if (mode === "leader") { if (leader.length < 5) setLeader([...leader, current]); return; }
    event.currentTarget.setPointerCapture(event.pointerId);
    setStart(current);
    setPreview([current[0], current[1], current[0], current[1]]);
  }

  function pointerMove(event: PointerEvent<SVGSVGElement>) {
    if (!start || !image) return;
    const current = point(event);
    setPreview([Math.min(start[0], current[0]), Math.min(start[1], current[1]), Math.max(start[0], current[0]), Math.max(start[1], current[1])]);
  }

  function pointerUp(event: PointerEvent<SVGSVGElement>) {
    if (!start || !image) return;
    const current = point(event);
    const box: Box = [Math.min(start[0], current[0]), Math.min(start[1], current[1]), Math.max(start[0], current[0]), Math.max(start[1], current[1])];
    if (box[2] - box[0] >= 3 && box[3] - box[1] >= 3) {
      if (mode === "target") setTarget(box); else setNoteBox(box);
      setLeader([]);
    }
    setStart(undefined); setPreview(undefined);
  }

  function addMark() {
    if (!issue || !pageId || !image || !target || !noteBox || leader.length < 2 || !inside(leader[0], noteBox) || !inside(leader[leader.length - 1], target)) {
      setError("Cần khung mục tiêu, khung ghi chú và leader bắt đầu trong ghi chú, kết thúc trong mục tiêu."); return;
    }
    if (!noteText.trim() || fullNote.split("\n").length > 2 || fullNote.length > 220) { setError("Ghi chú cần ngắn, rõ việc cần kiểm tra và tối đa hai dòng."); return; }
    setMarks((value) => [...value, {
      mark_id: `M-${crypto.randomUUID()}`, issue_id: issue.issue_id, page_id: pageId, dpi,
      render_size_px: [image.width, image.height], target_bbox_px: target, note_bbox_px: noteBox,
      leader_points_px: leader, note: fullNote,
      snapshot_digest: snapshotDigest, classification_digest: classificationDigest,
    }]);
    resetGeometry(); setNoteText(""); setMode("target"); setError("");
  }

  async function createDraft() {
    if (!marks.length) return;
    setBusy(true); setError("");
    try {
      const draft = await api<AdminAnnotationDraft>(`/admin/jobs/${encodeURIComponent(jobCode)}/checkpoint/annotation-drafts`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ marks }),
      });
      setMarks([]); onCreated(draft);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Không tạo được PDF draft.");
    } finally { setBusy(false); }
  }

  return <div className="mt-3 rounded border border-slate-200 p-3 text-xs">
    <h3 className="font-semibold">Đặt mark trên trang nguồn · PDF draft</h3>
    <p className="mt-1 text-slate-600">Kéo khung nhỏ sát số/dòng sai; kéo khung ghi chú vào khoảng trống; chọn leader rồi bấm từ ghi chú qua khoảng trắng đến mục tiêu. Sau khi xuất PDF, QA từng crop và toàn trang.</p>
    {error && <p role="alert" className="mt-2 text-red-700">{error}</p>}
    <div className="mt-2 flex flex-wrap items-end gap-2">
      <label>Issue <select className="input ml-1" value={issueId} onChange={(event) => { setIssueId(event.target.value); setPageId(""); setImage(undefined); resetGeometry(); }}><option value="">Chọn issue đã phân cấp</option>{issues.map((row) => <option key={row.issue_id} value={row.issue_id}>{row.issue_id} · {row.severity} · {row.claim.slice(0, 65)}</option>)}</select></label>
      <label>Trang <select className="input ml-1" value={pageId} onChange={(event) => { setPageId(event.target.value); setImage(undefined); resetGeometry(); }}><option value="">Chọn trang nguồn</option>{pages.map((id) => <option key={id} value={id}>{id}</option>)}</select></label>
      <label>DPI <select className="input ml-1" value={dpi} onChange={(event) => { setDpi(Number(event.target.value)); setImage(undefined); resetGeometry(); }}>{[150, 200, 300, 450].map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
    </div>
    {image && <>
      <div className="mt-2 flex flex-wrap gap-2">{(["target", "note", "leader"] as const).map((value) => <button key={value} type="button" className="btn-secondary" aria-pressed={mode === value} onClick={() => setMode(value)}>{value === "target" ? "1. KHUNG MỤC TIÊU" : value === "note" ? "2. KHUNG GHI CHÚ" : "3. ĐIỂM LEADER"}</button>)}<button type="button" className="btn-secondary" onClick={() => setLeader([])}>XÓA LEADER</button></div>
      <p className="mt-1 text-slate-600">Ảnh {image.width} × {image.height} px · mục tiêu {target?.join(", ") ?? "chưa đặt"} · ghi chú {noteBox?.join(", ") ?? "chưa đặt"} · leader {leader.length}/5 điểm</p>
      <div className="relative mt-2 w-full overflow-hidden border border-slate-300"><img src={image.url} alt={`Trang nguồn ${pageId} để đặt mark`} className="block h-auto w-full select-none" draggable={false} /><svg className="absolute inset-0 h-full w-full touch-none cursor-crosshair" viewBox={`0 0 ${image.width} ${image.height}`} preserveAspectRatio="none" onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={() => { setStart(undefined); setPreview(undefined); }} aria-label="Kéo để chọn bbox hoặc bấm để đặt leader">
        {target && <rect x={target[0]} y={target[1]} width={target[2] - target[0]} height={target[3] - target[1]} fill="none" stroke={severityColor} strokeWidth="3" />}
        {noteBox && <rect x={noteBox[0]} y={noteBox[1]} width={noteBox[2] - noteBox[0]} height={noteBox[3] - noteBox[1]} fill="none" stroke={severityColor} strokeWidth="3" />}
        {preview && <rect x={preview[0]} y={preview[1]} width={preview[2] - preview[0]} height={preview[3] - preview[1]} fill="none" stroke="#00A6A6" strokeWidth="2" />}
        {leader.length > 1 && <polyline points={leader.map((value) => value.join(",")).join(" ")} fill="none" stroke={severityColor} strokeWidth="3" />}
        {leader.map((value, index) => <circle key={index} cx={value[0]} cy={value[1]} r="5" fill={severityColor} />)}
      </svg></div>
      <label className="mt-2 block">Ghi chú sau mã lỗi (tối đa 2 dòng) <textarea className="input mt-1 w-full" rows={2} value={noteText} onChange={(event) => setNoteText(event.target.value)} placeholder="Sai: … Kiểm tra: …" /></label>
      <p className="mt-1 text-slate-600">Trên PDF: {fullNote || "—"}</p>
      <button type="button" className="btn-secondary mt-2" onClick={addMark}>THÊM MARK VÀO DRAFT</button>
    </>}
    {marks.length > 0 && <div className="mt-3"><p className="font-medium">{marks.length} mark chờ xuất PDF</p><ul className="mt-1 space-y-1">{marks.map((mark) => <li key={mark.mark_id}>{mark.issue_id} · {mark.page_id} · {mark.note}<button type="button" className="ml-2 underline" onClick={() => setMarks((value) => value.filter((item) => item.mark_id !== mark.mark_id))}>Bỏ</button></li>)}</ul><button type="button" className="btn-secondary mt-2" disabled={busy} onClick={createDraft}>{busy ? "Đang tạo…" : "TẠO PDF DRAFT ĐỂ QA"}</button></div>}
  </div>;
}
