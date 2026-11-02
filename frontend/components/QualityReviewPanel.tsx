"use client";
import { useEffect, useState } from "react";
import { AdminAnnotationDraft, AdminCheckpointIssues, AdminExcelReportReview, AdminInventoryApproval, AdminJobInventory, AdminJobQuality, AdminReleaseSignoff, AttemptArtifacts } from "@/types";
import { api } from "@/lib/api";
import { AnnotationMarkPlanner } from "@/components/AnnotationMarkPlanner";
import { AdminLessonReview } from "@/components/AdminLessonReview";

function statusLabel(status: AdminJobQuality["freshness_status"]) {
  if (status === "INVALIDATED") return "Đã vô hiệu hóa sau thay đổi";
  if (status === "NOT_VERIFIED") return "Chưa xác nhận còn hiệu lực";
  return "Chưa có quality review";
}

const repairStatusLabel: Record<AdminJobQuality["gate_repair_status"], string> = {
  NOT_APPLICABLE: "",
  GATE_INVALID: "Lịch sử gate lỗi toàn vẹn; cần kiểm tra dữ liệu lưu.",
  CHECKPOINT_UNAVAILABLE: "Checkpoint không còn hợp lệ với nguồn, skill hoặc bản duyệt hiện tại.",
  NOT_READY: "Gate không còn khớp checkpoint đang chờ; cần tải lại trạng thái.",
  CONTROLLER_CONTRACT: "Cần bổ sung bằng chứng closure do controller kiểm; chạy lại batch không giải quyết được.",
  VALIDATOR_ERROR: "Validator gặp lỗi; cần kiểm tra adapter và receipt.",
  UNMAPPED_BLOCKER: "Blocker chưa được gắn an toàn với batch cần sửa; kỹ sư phải xác định phạm vi.",
};
const excelReviewSections = [
  ["summary_counts", "Đối chiếu summary và các số đếm với ledger"],
  ["ledger_rows", "Kiểm sheet ledger, ID và liên kết nguồn"],
  ["source_formatting", "Mở và kiểm định dạng các sheet dự toán nguồn"],
  ["severity_colors", "Kiểm màu phân cấp và các trường reviewer"],
  ["pdf_alignment", "Đối chiếu trạng thái PDF/mark với cùng revision"],
] as const;

export function QualityReviewPanel({
  jobCode,
  quality,
  history,
  loading,
  error,
  onReleaseStateChanged,
}: {
  jobCode: string;
  quality?: AdminJobQuality;
  history?: AttemptArtifacts;
  loading: boolean;
  error: string;
  onReleaseStateChanged?: (ready: boolean) => void;
}) {
  const run = quality?.attempt == null ? undefined : history?.attempts.find((item) => item.attempt === quality.attempt);
  const workItems = Array.isArray(quality?.coverage?.work_items)
    ? quality.coverage.work_items.filter((item): item is { check_id: string; pass_no: number; execution_status: string; result_status: string } =>
      typeof item === "object" && item !== null &&
      typeof item.check_id === "string" && typeof item.pass_no === "number" &&
      typeof item.execution_status === "string" && typeof item.result_status === "string")
    : [];
  const openWorkItems = workItems.filter((item) => item.execution_status !== "DONE" || item.result_status === "UNRESOLVED");
  const closure = quality?.coverage?.pass3_closure;
  const lessonSelection = quality?.coverage?.lesson_selection as Record<string, unknown> | undefined;
  const repairStatus = quality?.gate_repair_status ?? "NOT_APPLICABLE";
  const repairBatches = Array.isArray(quality?.coverage?.repair_batches)
    ? quality.coverage.repair_batches.filter((batch): batch is string[] =>
      Array.isArray(batch) && batch.length > 0 && batch.every((item) => typeof item === "string")) : [];
  const closureGaps = typeof closure === "object" && closure !== null && "gaps" in closure && Array.isArray(closure.gaps)
    ? closure.gaps.filter((gap): gap is string => typeof gap === "string") : [];
  const [inventory, setInventory] = useState<AdminJobInventory>();
  const [inventoryText, setInventoryText] = useState("");
  const [inventoryError, setInventoryError] = useState("");
  const [inventoryBusy, setInventoryBusy] = useState(false);
  const [resumeBusy, setResumeBusy] = useState(false);
  const [resumeError, setResumeError] = useState("");
  const [repairBusy, setRepairBusy] = useState(false);
  const [repairError, setRepairError] = useState("");
  const [issuePage, setIssuePage] = useState<AdminCheckpointIssues>();
  const [issueOffset, setIssueOffset] = useState(0);
  const [issueError, setIssueError] = useState("");
  const [issueBusy, setIssueBusy] = useState("");
  const [issueSeverities, setIssueSeverities] = useState<Record<string, string>>({});
  const [issueReasons, setIssueReasons] = useState<Record<string, string>>({});
  const [annotationDraft, setAnnotationDraft] = useState<AdminAnnotationDraft>();
  const [annotationError, setAnnotationError] = useState("");
  const [selectedMarkId, setSelectedMarkId] = useState("");
  const [annotationReason, setAnnotationReason] = useState("");
  const [annotationViewed, setAnnotationViewed] = useState(false);
  const [annotationBusy, setAnnotationBusy] = useState(false);
  const [excelReview, setExcelReview] = useState<AdminExcelReportReview>();
  const [excelError, setExcelError] = useState("");
  const [excelBusy, setExcelBusy] = useState(false);
  const [excelReason, setExcelReason] = useState("");
  const [excelSections, setExcelSections] = useState<Record<string, boolean>>({});
  const [releaseSignoff, setReleaseSignoff] = useState<AdminReleaseSignoff>();
  const [releaseReason, setReleaseReason] = useState("");
  const [partialAccepted, setPartialAccepted] = useState(false);
  const [unmarkedPdfReviewed, setUnmarkedPdfReviewed] = useState(false);
  const [releaseBusy, setReleaseBusy] = useState(false);
  const [releaseError, setReleaseError] = useState("");
  const selectedMark = annotationDraft?.marks.find((mark) => mark.mark_id === selectedMarkId) ?? annotationDraft?.marks[0];
  const noActivePdfIssues = quality?.report_source?.pdf_mark_required_issue_count === 0;
  const pdfReadyForSignoff = Boolean(annotationDraft?.is_current && (annotationDraft.annotation_qa_status === "PASS" || (annotationDraft.marks.length === 0 && annotationDraft.missing_active_issue_ids.length === 0)));
  const currentApprovalText = inventory?.approval?.is_current ? JSON.stringify(inventory.approval.inventory, null, 2) : "";
  const hasUnapprovedDraft = Boolean(currentApprovalText && inventoryText !== currentApprovalText);

  async function retryInterruptedBatch() {
    setResumeBusy(true);
    setResumeError("");
    try {
      await api(`/admin/jobs/${encodeURIComponent(jobCode)}/checkpoint/retry`, { method: "POST" });
      window.location.reload();
    } catch (error) {
      setResumeError(error instanceof Error ? error.message : "Không thể thử lại batch.");
      setResumeBusy(false);
    }
  }

  async function requestGateRepair(batchIds: string[]) {
    setRepairBusy(true);
    setRepairError("");
    try {
      await api(`/admin/jobs/${encodeURIComponent(jobCode)}/checkpoint/repair`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ batch_ids: batchIds }),
      });
      window.location.reload();
    } catch (error) {
      setRepairError(error instanceof Error ? error.message : "Không tạo được yêu cầu sửa gate.");
      setRepairBusy(false);
    }
  }

  useEffect(() => {
    let current = true;
    api<AdminJobInventory>(`/admin/jobs/${encodeURIComponent(jobCode)}/inventory`).then((value) => {
      if (!current) return;
      setInventory(value);
      setInventoryText(JSON.stringify(value.approval?.is_current ? value.approval.inventory : value.scaffold, null, 2));
      setInventoryError("");
    }).catch((error: Error) => { if (current) setInventoryError(error.message); });
    return () => { current = false; };
  }, [jobCode]);

  useEffect(() => {
    if (!quality?.report_source) return;
    let current = true;
    api<AdminCheckpointIssues>(`/admin/jobs/${encodeURIComponent(jobCode)}/checkpoint/issues?offset=${issueOffset}&limit=50`).then((value) => {
      if (current && value.snapshot_digest === quality.report_source?.snapshot_digest) { setIssuePage(value); setIssueError(""); }
    }).catch((error: Error) => { if (current) setIssueError(error.message); });
    return () => { current = false; };
  }, [jobCode, issueOffset, quality?.report_source?.snapshot_digest]);

  useEffect(() => {
    if (quality?.coverage?.pass_no !== 3 || !quality.report_source) return;
    let current = true;
    api<AdminAnnotationDraft>(`/admin/jobs/${encodeURIComponent(jobCode)}/checkpoint/annotation-drafts/latest`).then((value) => {
      if (current) { setAnnotationDraft(value); setSelectedMarkId(value.marks[0]?.mark_id ?? ""); setAnnotationError(""); }
    }).catch((error: Error) => {
      if (current && !error.message.includes("No annotation draft")) setAnnotationError(error.message);
    });
    return () => { current = false; };
  }, [jobCode, quality?.coverage?.pass_no, quality?.report_source?.snapshot_digest, quality?.report_source?.classification_digest]);

  useEffect(() => {
    setExcelReview(undefined);
    setExcelError("");
    setExcelSections({});
    setExcelReason("");
    setReleaseSignoff(undefined);
    setReleaseReason("");
    setPartialAccepted(false);
    setUnmarkedPdfReviewed(false);
  }, [jobCode, quality?.report_source?.snapshot_digest, quality?.report_source?.classification_digest]);

  useEffect(() => {
    if (quality?.coverage?.pass_no !== 3) return;
    let current = true;
    api<AdminReleaseSignoff>(`/admin/jobs/${encodeURIComponent(jobCode)}/checkpoint/release-signoff`).then((value) => {
      if (current) { setReleaseSignoff(value); onReleaseStateChanged?.(value.is_current && value.result !== "REJECT"); }
    }).catch(() => { if (current) { setReleaseSignoff(undefined); onReleaseStateChanged?.(false); } });
    return () => { current = false; };
  }, [jobCode, quality?.coverage?.pass_no, quality?.report_source?.snapshot_digest, quality?.report_source?.classification_digest]);

  async function reviewAnnotation(result: "PASS" | "FAIL") {
    if (!annotationDraft?.is_current || !selectedMark || !annotationViewed || annotationReason.trim().length < 12) return;
    setAnnotationBusy(true);
    setAnnotationError("");
    try {
      const root = `/api/admin/jobs/${encodeURIComponent(jobCode)}/checkpoint/annotation-drafts/${annotationDraft.draft_id}/marks/${encodeURIComponent(selectedMark.mark_id)}`;
      const images = await Promise.all((["target", "note", "page"] as const).map(async (kind) => {
        const response = await fetch(`${root}/preview/${kind}`, { credentials: "include", cache: "no-store" });
        if (!response.ok || response.headers.get("x-annotation-pdf-sha256") !== annotationDraft.output_pdf_sha256) throw new Error("Ảnh kiểm tra không còn khớp PDF draft.");
        return response.headers.get("x-review-image-sha256");
      }));
      if (images.some((digest) => !digest)) throw new Error("Thiếu SHA ảnh kiểm tra.");
      const updated = await api<AdminAnnotationDraft>(`${root}/review`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          output_pdf_sha256: annotationDraft.output_pdf_sha256,
          target_crop_sha256: images[0], note_crop_sha256: images[1], page_render_sha256: images[2],
          result, reason: annotationReason.trim(),
        }),
      });
      setAnnotationDraft(updated);
      setExcelReview(undefined);
      setReleaseSignoff(undefined);
      onReleaseStateChanged?.(false);
      setAnnotationViewed(false);
      setAnnotationReason("");
    } catch (error) {
      setAnnotationError(error instanceof Error ? error.message : "Không lưu được QA mark.");
    } finally {
      setAnnotationBusy(false);
    }
  }

  async function createUnmarkedPdfDraft() {
    setAnnotationBusy(true);
    setAnnotationError("");
    try {
      const draft = await api<AdminAnnotationDraft>(`/admin/jobs/${encodeURIComponent(jobCode)}/checkpoint/annotation-drafts`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ marks: [] }),
      });
      setAnnotationDraft(draft);
      setExcelReview(undefined);
      setReleaseSignoff(undefined);
      onReleaseStateChanged?.(false);
      setSelectedMarkId("");
      setUnmarkedPdfReviewed(false);
    } catch (error) {
      setAnnotationError(error instanceof Error ? error.message : "Không tạo được PDF draft không có mark.");
    } finally {
      setAnnotationBusy(false);
    }
  }

  async function downloadExcelForReview() {
    setExcelBusy(true);
    setExcelError("");
    try {
      const root = `/api/admin/jobs/${encodeURIComponent(jobCode)}/checkpoint/report-draft.xlsx`;
      const response = await fetch(root, { credentials: "include", cache: "no-store" });
      if (!response.ok || response.headers.get("x-audit-excel-qa") !== "PASS") throw new Error("Excel draft chưa qua structural QA.");
      const draftId = response.headers.get("x-audit-excel-draft-id");
      const workbookSha = response.headers.get("x-audit-excel-sha256");
      if (!draftId || !workbookSha) throw new Error("Thiếu ID hoặc SHA của Excel draft.");
      const objectUrl = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = "BOQ_Audit_Report_Draft.xlsx";
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
      const state = await api<AdminExcelReportReview>(`/admin/jobs/${encodeURIComponent(jobCode)}/checkpoint/report-drafts/${draftId}/review`);
      setExcelReview(state);
      setReleaseSignoff(undefined);
      onReleaseStateChanged?.(false);
      setExcelSections({});
      setExcelReason("");
    } catch (error) {
      setExcelError(error instanceof Error ? error.message : "Không tải được Excel draft.");
    } finally {
      setExcelBusy(false);
    }
  }

  async function reviewExcel(result: "PASS" | "FAIL") {
    if (!excelReview?.is_current || !excelReview.draft_id || excelReason.trim().length < 12 || Object.values(excelSections).filter(Boolean).length !== 5) return;
    setExcelBusy(true);
    setExcelError("");
    try {
      const updated = await api<AdminExcelReportReview>(`/admin/jobs/${encodeURIComponent(jobCode)}/checkpoint/report-drafts/${excelReview.draft_id}/review`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          workbook_sha256: excelReview.workbook_sha256, result, reason: excelReason.trim(),
          reviewed_sections: Object.keys(excelSections).filter((section) => excelSections[section]),
        }),
      });
      setExcelReview(updated);
      setReleaseSignoff(undefined);
      onReleaseStateChanged?.(false);
      setExcelReason("");
      setExcelSections({});
    } catch (error) {
      setExcelError(error instanceof Error ? error.message : "Không lưu được QA Excel.");
    } finally {
      setExcelBusy(false);
    }
  }

  async function signoffRelease(result: "APPROVE_FULL" | "APPROVE_PARTIAL") {
    const source = quality?.report_source;
    const pdfReady = annotationDraft?.is_current && (annotationDraft.annotation_qa_status === "PASS" || (annotationDraft.marks.length === 0 && annotationDraft.missing_active_issue_ids.length === 0));
    if (!source?.classification_digest || !pdfReady || (annotationDraft?.marks.length === 0 && !unmarkedPdfReviewed) || !excelReview?.is_current || excelReview.latest_result !== "PASS" || releaseReason.trim().length < 20) return;
    if (result === "APPROVE_PARTIAL" && !partialAccepted) return;
    setReleaseBusy(true);
    setReleaseError("");
    try {
      const response = await api<AdminReleaseSignoff>(`/admin/jobs/${encodeURIComponent(jobCode)}/checkpoint/release-signoff`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          snapshot_digest: source.snapshot_digest, classification_digest: source.classification_digest,
          pdf_draft_id: annotationDraft.draft_id, pdf_sha256: annotationDraft.output_pdf_sha256,
          excel_draft_id: excelReview.draft_id, excel_sha256: excelReview.workbook_sha256,
          result, accepted_partial: result === "APPROVE_PARTIAL" && partialAccepted,
          reviewed_unmarked_pdf: unmarkedPdfReviewed,
          reason: releaseReason.trim(),
        }),
      });
      setReleaseSignoff(response);
      onReleaseStateChanged?.(response.is_current && response.result !== "REJECT");
      setReleaseReason("");
      setUnmarkedPdfReviewed(false);
    } catch (error) {
      setReleaseError(error instanceof Error ? error.message : "Không lưu được engineer signoff.");
    } finally {
      setReleaseBusy(false);
    }
  }

  async function classifyIssue(issueId: string, finalStatus: string, currentSeverity: string | null, currentReason: string | null) {
    const digest = quality?.report_source?.snapshot_digest;
    if (!digest) return;
    setIssueBusy(issueId);
    setIssueError("");
    try {
      await api(`/admin/jobs/${encodeURIComponent(jobCode)}/checkpoint/issues/${encodeURIComponent(issueId)}/classification`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ snapshot_digest: digest, final_status: finalStatus, severity: ["FALSE_POSITIVE", "CLOSED_WITH_EXPLANATION"].includes(finalStatus) ? null : issueSeverities[issueId] ?? currentSeverity, reason: issueReasons[issueId] ?? currentReason ?? "" }),
      });
      window.location.reload();
    } catch (error) {
      setIssueError(error instanceof Error ? error.message : "Không lưu được phân cấp lỗi.");
      setIssueBusy("");
    }
  }

  async function approveInventory() {
    setInventoryBusy(true);
    setInventoryError("");
    try {
      const body = JSON.parse(inventoryText) as Record<string, unknown>;
      await api<AdminInventoryApproval>(`/admin/jobs/${encodeURIComponent(jobCode)}/inventory`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ inventory: body }),
      });
      const fresh = await api<AdminJobInventory>(`/admin/jobs/${encodeURIComponent(jobCode)}/inventory`);
      setInventory(fresh);
      setInventoryText(JSON.stringify(fresh.approval?.inventory ?? fresh.scaffold, null, 2));
    } catch (error) {
      setInventoryError(error instanceof Error ? error.message : "Không lưu được inventory.");
    } finally {
      setInventoryBusy(false);
    }
  }

  return (
    <section className="card" aria-labelledby="quality-review-heading">
      <h2 id="quality-review-heading" className="font-bold">Quality review</h2>
      <p className="mt-1 text-xs text-slate-600">Chỉ mang tính advisory; không phải xác nhận kỹ thuật và không tự cho phép công bố.</p>
      <AdminLessonReview jobCode={jobCode} />
      {loading ? <p className="mt-4 text-sm text-slate-500">Đang tải trạng thái quality…</p> : error ? (
        <p role="alert" className="mt-4 text-sm text-red-700">Không tải được quality review: {error}</p>
      ) : !quality ? <p className="mt-4 text-sm text-slate-500">Chưa có dữ liệu quality.</p> : (
        <>
          <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
            <div><dt className="text-xs uppercase tracking-wide text-slate-500">Freshness</dt><dd className="mt-1 font-semibold">{statusLabel(quality.freshness_status)}</dd></div>
            <div><dt className="text-xs uppercase tracking-wide text-slate-500">Lượt / stage</dt><dd className="mt-1 font-semibold">{quality.attempt == null ? "—" : `Lượt ${quality.attempt}`} · {quality.stage ?? "—"}</dd></div>
            <div><dt className="text-xs uppercase tracking-wide text-slate-500">Validator</dt><dd className="mt-1 font-semibold">{quality.quality ? `${quality.quality.controller_status} · ${quality.quality.candidate_release_status ?? "chưa có candidate status"}` : "Chưa có review"}</dd></div>
            <div><dt className="text-xs uppercase tracking-wide text-slate-500">Bản ghi còn mở</dt><dd className="mt-1 font-semibold">{quality.quality?.open_record_count ?? "Chưa có số liệu"}</dd></div>
            <div><dt className="text-xs uppercase tracking-wide text-slate-500">Coverage</dt><dd className="mt-1 font-semibold">{quality.coverage_status === "UNAVAILABLE" ? "Chưa khả dụng" : quality.coverage_status}</dd></div>
            <div><dt className="text-xs uppercase tracking-wide text-slate-500">Unresolved riêng</dt><dd className="mt-1 font-semibold">{typeof quality.coverage?.unresolved_count === "number" ? quality.coverage.unresolved_count : "Chưa có dữ liệu"}</dd></div>
          </dl>
          {quality.coverage && <p className="mt-2 text-xs text-slate-600">Pass {String(quality.coverage.pass_no ?? "—")} · checkpoint {String(quality.coverage.checkpoint_status ?? "—")} · {String(quality.coverage.checked_count ?? 0)}/{String(quality.coverage.scope_count ?? 0)} work item đã kiểm tra · {String(quality.coverage.not_checked_count ?? 0)} chưa kiểm tra</p>}
          {lessonSelection && <p className="mt-1 text-xs text-slate-600">Lesson selection: {String(lessonSelection.status)} · {String(lessonSelection.selected_count ?? 0)} đã áp dụng · {String(lessonSelection.pending_review_count ?? 0)} chờ reviewer · {String(lessonSelection.unresolved_count ?? 0)} chưa xác định{lessonSelection.selection_digest ? ` · SHA ${String(lessonSelection.selection_digest).slice(0, 12)}` : ""}</p>}
          {quality.report_source && <div className="mt-3 rounded border border-slate-200 p-3 text-xs">
            <h3 className="font-semibold">Nguồn báo cáo từ ledger · Excel nháp</h3>
            <p className="mt-1">Contract v{quality.report_source.contract_version} · checkpoint {quality.report_source.checkpoint_version} · ledger {quality.report_source.snapshot_digest.slice(0, 12)} · phân cấp {quality.report_source.classification_digest?.slice(0, 12) ?? "—"} · {quality.report_source.ledger_record_counts.issues ?? 0} bản ghi lỗi · {quality.report_source.issue_candidate_count} candidate · {quality.report_source.ledger_record_counts.boq_lineage ?? 0} liên kết BOQ · {quality.report_source.unclassified_issue_count} chưa phân cấp</p>
            <p className="mt-1 text-slate-600">Số bản ghi lỗi chưa phải số lỗi gốc. Phân cấp của reviewer chưa thay thế QA vị trí đánh dấu hoặc duyệt kỹ thuật để phát hành Excel/PDF.</p>
            {quality.coverage?.pass_no === 3 && <button type="button" className="btn-secondary mt-2" disabled={excelBusy} onClick={downloadExcelForReview}>{excelBusy ? "ĐANG XỬ LÝ…" : "TẢI EXCEL NHÁP ĐỂ QA"}</button>}
            {excelError && <p role="alert" className="mt-2 text-red-700">{excelError}</p>}
            {excelReview && <div className="mt-3 border-t border-slate-200 pt-3">
              <p>Excel draft #{excelReview.draft_id} · structural QA {excelReview.structural_qa} · {excelReview.is_current ? `review ${excelReview.latest_result}` : "BẢN CŨ / KHÔNG HỢP LỆ"}</p>
              <p className="mt-1 break-all text-slate-600">SHA-256: {excelReview.workbook_sha256}</p>
              {excelReview.latest_reason && <p className="mt-1">Nhận xét gần nhất: {excelReview.latest_reason}</p>}
              {excelReview.is_current && excelReview.structural_qa === "PASS" && <div className="mt-2 space-y-2">
                <p className="font-medium">Sau khi mở và kiểm file vừa tải, xác nhận từng mục:</p>
                {excelReviewSections.map(([key, label]) => <label key={key} className="flex items-start gap-2">
                  <input type="checkbox" checked={Boolean(excelSections[key])} onChange={(event) => setExcelSections((value) => ({ ...value, [key]: event.target.checked }))} />{label}
                </label>)}
                <label className="block">Nhận xét QA <input className="input mt-1 w-full" value={excelReason} onChange={(event) => setExcelReason(event.target.value)} placeholder="Ghi kết quả hoặc lỗi cần sửa; ít nhất 12 ký tự" /></label>
                <div className="flex gap-2"><button type="button" className="btn-secondary" disabled={excelBusy || Object.values(excelSections).filter(Boolean).length !== 5 || excelReason.trim().length < 12} onClick={() => reviewExcel("PASS")}>XÁC NHẬN EXCEL ĐẠT</button><button type="button" className="btn-secondary" disabled={excelBusy || Object.values(excelSections).filter(Boolean).length !== 5 || excelReason.trim().length < 12} onClick={() => reviewExcel("FAIL")}>BÁO EXCEL CÓ LỖI</button></div>
                <p className="text-slate-600">Xác nhận này gắn với SHA trên; đây là QA workbook, không phải ký duyệt kỹ thuật hoặc cho phép công bố.</p>
              </div>}
            </div>}
          </div>}
          {quality.coverage?.pass_no === 3 && quality.report_source?.classification_digest && <AnnotationMarkPlanner jobCode={jobCode} snapshotDigest={quality.report_source.snapshot_digest} classificationDigest={quality.report_source.classification_digest} onCreated={(draft) => { setAnnotationDraft(draft); setExcelReview(undefined); setReleaseSignoff(undefined); onReleaseStateChanged?.(false); setSelectedMarkId(draft.marks[0]?.mark_id ?? ""); setAnnotationViewed(false); setAnnotationReason(""); }} />}
          {quality.coverage?.pass_no === 3 && noActivePdfIssues && (!annotationDraft || !annotationDraft.is_current) && <button type="button" className="btn-secondary mt-3" disabled={annotationBusy} onClick={createUnmarkedPdfDraft}>{annotationBusy ? "ĐANG TẠO PDF…" : "TẠO PDF DRAFT KHÔNG CÓ MARK"}</button>}
          {quality.coverage?.pass_no === 3 && <div className="mt-3 rounded border border-slate-200 p-3 text-xs">
            <h3 className="font-semibold">QA đánh dấu PDF · từng mark</h3>
            {annotationError && <p role="alert" className="mt-1 text-red-700">{annotationError}</p>}
            {!annotationDraft ? <p className="mt-1 text-slate-600">Chưa có PDF draft gắn với run hiện tại.</p> : <>
              <p className="mt-1">PDF draft #{annotationDraft.draft_id} · {annotationDraft.is_current ? annotationDraft.annotation_qa_status : "BẢN CŨ"} · PASS {annotationDraft.review_counts.PASS ?? 0}/{annotationDraft.marks.length} · FAIL {annotationDraft.review_counts.FAIL ?? 0}</p>
              {annotationDraft.missing_active_issue_ids.length > 0 && <p className="mt-1 text-amber-900">Issue chưa có mark: {annotationDraft.missing_active_issue_ids.join(", ")}</p>}
              {annotationDraft.marks.length === 0 && annotationDraft.is_current && <p className="mt-1 text-amber-900">Không có issue gắn trang PDF cần đánh dấu. Issue chỉ ở Excel vẫn phải được kiểm trong report; kỹ sư phải mở và xem toàn bộ PDF khi ký.</p>}
              <a className="btn-secondary mt-2 inline-block" href={`/api/admin/jobs/${encodeURIComponent(jobCode)}/checkpoint/annotation-drafts/${annotationDraft.draft_id}/download`}>TẢI PDF DRAFT</a>
              {selectedMark && <div className="mt-3 border-t border-slate-100 pt-2">
                <label>Mark <select className="input ml-2" value={selectedMark.mark_id} onChange={(event) => { setSelectedMarkId(event.target.value); setAnnotationViewed(false); setAnnotationReason(""); }}>
                  {annotationDraft.marks.map((mark) => <option key={mark.mark_id} value={mark.mark_id}>{mark.mark_id} · {mark.issue_id} · trang {mark.pdf_page} · {mark.review_status}</option>)}
                </select></label>
                <p className="mt-2">{selectedMark.severity} · {selectedMark.note}</p>
                <div className="mt-2 grid gap-3 md:grid-cols-2">
                  {(["target", "note", "page"] as const).map((kind) => <figure key={`${annotationDraft.draft_id}-${selectedMark.mark_id}-${kind}`} className={kind === "page" ? "md:col-span-2" : ""}>
                    <figcaption className="mb-1 font-medium">{kind === "target" ? "Crop mục tiêu" : kind === "note" ? "Crop ghi chú" : "Toàn trang: kiểm đường nối và vùng che"}</figcaption>
                    <a href={`/api/admin/jobs/${encodeURIComponent(jobCode)}/checkpoint/annotation-drafts/${annotationDraft.draft_id}/marks/${encodeURIComponent(selectedMark.mark_id)}/preview/${kind}`} target="_blank" rel="noopener noreferrer" aria-label={`Mở ảnh ${kind} của mark ${selectedMark.mark_id} ở kích thước đầy đủ`}><img className="max-h-[600px] max-w-full border border-slate-300 object-contain" alt={`${kind} của mark ${selectedMark.mark_id}`} src={`/api/admin/jobs/${encodeURIComponent(jobCode)}/checkpoint/annotation-drafts/${annotationDraft.draft_id}/marks/${encodeURIComponent(selectedMark.mark_id)}/preview/${kind}`} /></a>
                  </figure>)}
                </div>
                {annotationDraft.is_current && <div className="mt-3 space-y-2">
                  <label className="flex items-start gap-2"><input type="checkbox" checked={annotationViewed} onChange={(event) => setAnnotationViewed(event.target.checked)} />Tôi đã xem rõ mục tiêu, ghi chú và toàn trang trên PDF draft này.</label>
                  <label className="block">Nhận xét QA <input className="input mt-1 w-full" value={annotationReason} onChange={(event) => setAnnotationReason(event.target.value)} placeholder="Nêu vị trí đúng/sai và việc cần sửa; ít nhất 12 ký tự" /></label>
                  <div className="flex gap-2"><button type="button" className="btn-secondary" disabled={annotationBusy || !annotationViewed || annotationReason.trim().length < 12} onClick={() => reviewAnnotation("PASS")}>XÁC NHẬN MARK ĐẠT</button><button type="button" className="btn-secondary" disabled={annotationBusy || !annotationViewed || annotationReason.trim().length < 12} onClick={() => reviewAnnotation("FAIL")}>BÁO MARK SAI</button></div>
                </div>}
              </div>}
            </>}
          </div>}
          {quality.coverage?.pass_no === 3 && <div className="mt-3 rounded border border-slate-200 p-3 text-xs">
            <h3 className="font-semibold">Kỹ sư duyệt phát hành</h3>
            <p className="mt-1 text-slate-600">Chỉ email được khai báo trong AUDIT_ENGINEER_SIGNOFF_EMAILS mới ký được. Signoff áp dụng cho đúng revision và SHA của PDF/Excel.</p>
            {releaseSignoff && <div className="mt-2">
              <p>Signoff #{releaseSignoff.signoff_id} · {releaseSignoff.is_current ? releaseSignoff.result : "BẢN CŨ / KHÔNG CÒN HIỆU LỰC"} · revision {releaseSignoff.revision_no}</p>
              {releaseSignoff.unresolved_scope.length > 0 && <p className="mt-1 text-amber-900">Phạm vi còn unresolved được ký nhận: {releaseSignoff.unresolved_scope.join(", ")}</p>}
            </div>}
            {releaseError && <p role="alert" className="mt-2 text-red-700">{releaseError}</p>}
            {pdfReadyForSignoff && excelReview?.is_current && excelReview.latest_result === "PASS" && <div className="mt-2 space-y-2">
              <label className="block">Nhận xét / phạm vi ký <textarea className="input mt-1 w-full" rows={3} value={releaseReason} onChange={(event) => setReleaseReason(event.target.value)} placeholder="Nêu cơ sở kỹ thuật và giới hạn đã chấp nhận; ít nhất 20 ký tự" /></label>
              {annotationDraft?.marks.length === 0 && <label className="flex items-start gap-2"><input type="checkbox" checked={unmarkedPdfReviewed} onChange={(event) => setUnmarkedPdfReviewed(event.target.checked)} />Tôi đã mở và xem toàn bộ PDF draft này; không có mark nên không tồn tại crop để reviewer QA riêng.</label>}
              <label className="flex items-start gap-2"><input type="checkbox" checked={partialAccepted} onChange={(event) => setPartialAccepted(event.target.checked)} />Tôi đã xem và chấp nhận rõ các mục unresolved/giới hạn nếu chọn duyệt partial.</label>
              <div className="flex flex-wrap gap-2">
                <button type="button" className="btn-secondary" disabled={releaseBusy || releaseReason.trim().length < 20 || (annotationDraft?.marks.length === 0 && !unmarkedPdfReviewed)} onClick={() => signoffRelease("APPROVE_FULL")}>KÝ DUYỆT ĐẦY ĐỦ</button>
                <button type="button" className="btn-secondary" disabled={releaseBusy || !partialAccepted || releaseReason.trim().length < 20 || (annotationDraft?.marks.length === 0 && !unmarkedPdfReviewed)} onClick={() => signoffRelease("APPROVE_PARTIAL")}>KÝ DUYỆT CÓ GIỚI HẠN</button>
              </div>
            </div>}
          </div>}
          {quality.report_source && <div className="mt-3 rounded border border-slate-200 p-3 text-xs">
            <h3 className="font-semibold">Issue ledger · phân cấp theo snapshot</h3>
            {issueError && <p role="alert" className="mt-1 text-red-700">{issueError}</p>}
            {issuePage && issuePage.snapshot_digest === quality.report_source.snapshot_digest && <>
              <p className="mt-1 text-slate-600">{issuePage.total} bản ghi · đang xem {issuePage.total ? issuePage.offset + 1 : 0}–{Math.min(issuePage.offset + issuePage.issues.length, issuePage.total)}</p>
              <ul className="mt-2 space-y-3">{issuePage.issues.map((issue) => <li key={issue.issue_id} className="border-t border-slate-100 pt-2">
                <p><strong>{issue.issue_id}</strong> · {issue.final_status} · {issue.classified ? `đã phân cấp ${issue.severity ?? "không áp dụng"}` : "chưa phân cấp"}</p>
                <p className="mt-1">{issue.claim}</p>
                <p className="mt-1 text-slate-600">Trang: {issue.source_refs.page_ids.join(", ") || "—"} · Sheet: {issue.source_refs.sheet_ids.join(", ") || "—"} · Target: {issue.source_refs.target_ids.join(", ") || "—"}</p>
                <div className="mt-2 flex flex-wrap items-end gap-2">
                  {!["FALSE_POSITIVE", "CLOSED_WITH_EXPLANATION"].includes(issue.final_status) && <label>Mức độ <select className="input ml-1" value={issueSeverities[issue.issue_id] ?? issue.severity ?? ""} onChange={(event) => setIssueSeverities((value) => ({ ...value, [issue.issue_id]: event.target.value }))}><option value="">Chọn</option>{["CRITICAL", "HIGH", "MEDIUM", "LOW", "NOTE"].map((level) => <option key={level} value={level}>{level}</option>)}</select></label>}
                  <label>Lý do kiểm nguồn <input className="input ml-1" value={issueReasons[issue.issue_id] ?? issue.review_reason ?? ""} onChange={(event) => setIssueReasons((value) => ({ ...value, [issue.issue_id]: event.target.value }))} /></label>
                  <button type="button" className="btn-secondary" disabled={issueBusy === issue.issue_id} onClick={() => classifyIssue(issue.issue_id, issue.final_status, issue.severity, issue.review_reason)}>{issueBusy === issue.issue_id ? "Đang lưu…" : issue.classified ? "CẬP NHẬT PHÂN CẤP" : "LƯU PHÂN CẤP"}</button>
                </div>
              </li>)}</ul>
              <div className="mt-2 flex gap-2"><button type="button" className="btn-secondary" disabled={issueOffset === 0} onClick={() => setIssueOffset(Math.max(0, issueOffset - 50))}>TRƯỚC</button><button type="button" className="btn-secondary" disabled={issueOffset + issuePage.limit >= issuePage.total} onClick={() => setIssueOffset(issueOffset + 50)}>SAU</button></div>
            </>}
          </div>}
          {repairStatus !== "NOT_APPLICABLE" && <p className="mt-2 text-xs font-medium text-amber-900">Hướng xử lý gate: {repairStatusLabel[repairStatus]}</p>}
          {quality.gate_repair_request_status && <p className="mt-1 text-xs text-slate-600">Yêu cầu sửa gần nhất: {quality.gate_repair_request_status} · {quality.gate_repair_attempts}/2 lượt đã dùng</p>}
          {repairStatus === "UNMAPPED_BLOCKER" && repairBatches.length === 1 && (quality.gate_repair_attempts ?? 0) < 2 && !["REQUESTED", "RUNNING"].includes(quality.gate_repair_request_status ?? "") && repairBatches.map((batch) => (
            <button key={batch.join("|")} type="button" className="btn-secondary mt-2 mr-2" disabled={repairBusy} onClick={() => requestGateRepair(batch)}>
              {repairBusy ? "Đang gửi…" : `YÊU CẦU SỬA BATCH ${batch.join(", ")}`}
            </button>
          ))}
          {repairError && <p role="alert" className="mt-2 text-sm text-red-700">{repairError}</p>}
          {closureGaps.length > 0 && <ul className="mt-2 list-inside list-disc text-xs text-amber-900">{closureGaps.map((gap, index) => <li key={`${index}-${gap}`}>{gap}</li>)}</ul>}
          {quality.coverage?.checkpoint_status === "INTERRUPTED" && <button type="button" className="btn-secondary mt-2" onClick={retryInterruptedBatch} disabled={resumeBusy}>{resumeBusy ? "Đang thử lại…" : "THỬ LẠI BATCH GIÁN ĐOẠN"}</button>}
          {resumeError && <p role="alert" className="mt-2 text-sm text-red-700">{resumeError}</p>}
          <p className="mt-2 text-xs text-slate-600">{quality.coverage_limitation}</p>
          {quality.coverage_status === "LEGACY_V3_UNVERIFIED" && <p role="status" className="mt-2 rounded border border-amber-300 bg-amber-50 p-2 text-sm text-amber-950">Checkpoint contract v3 chỉ được giữ để tra cứu lịch sử. Không thể xác minh hoặc tiếp tục checkpoint này theo contract v4; cần tạo attempt mới.</p>}
          <div className="mt-3 rounded border border-slate-200 p-3">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Lịch sử stage gate · tối đa 20 lần gần nhất</h3>
            {quality.stage_gates.length ? <ol className="mt-2 space-y-2 text-xs">
              {quality.stage_gates.map((gate) => <li key={gate.snapshot_id} className="border-t border-slate-100 pt-2">
                <p className="font-medium">Lượt {gate.attempt} · Pass {gate.pass_no} · checkpoint {gate.checkpoint_version} · {gate.integrity_status === "INVALID" ? "Bản ghi lỗi toàn vẹn" : gate.advanced ? "Đã chuyển pass" : gate.pass_no === 3 && gate.controller_status === "PASS" ? "Gate qua · cần kỹ sư duyệt" : gate.controller_status}</p>
                <p className="text-slate-600">{new Date(gate.created_at).toLocaleString("vi-VN")} · snapshot {gate.snapshot_id.slice(0, 12)}{gate.error_code ? ` · ${gate.error_code}` : ""}</p>
                {gate.blocking_checks.length > 0 && <ul className="mt-1 list-inside list-disc text-amber-900">{gate.blocking_checks.map((blocker, index) => <li key={`${index}-${blocker}`}>{blocker}</li>)}</ul>}
              </li>)}
            </ol> : <p className="mt-1 text-sm text-slate-500">Chưa chạy stage gate trong lượt audit mới nhất.</p>}
          </div>
          <div className="mt-3 rounded border border-slate-200 p-3">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Sanity Pass 1 · advisory</h3>
            <p className="mt-1 text-sm">{quality.sanity.status} · {quality.sanity.checked_rows} dòng · digest {quality.sanity.input_digest?.slice(0, 12) ?? "—"}</p>
            <p className="mt-1 text-xs text-slate-600">Tự kiểm tra số lượng đơn vị rời rạc và STT bị thiếu/trùng/không nguyên. Không có cờ bất thường không đồng nghĩa hồ sơ đã đúng kỹ thuật.</p>
            {quality.sanity.anomalies.length > 0 && <ul className="mt-2 space-y-1 text-xs text-amber-900">
              {quality.sanity.anomalies.map((item, index) => <li key={`${item.anomaly}-${item.row_id}-${index}`}><code>{item.row_id}</code> · {item.anomaly} · <code>{JSON.stringify(item)}</code></li>)}
            </ul>}
          </div>
          {openWorkItems.length > 0 && <div className="mt-3 overflow-x-auto">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Work item cần chú ý</h3>
            <table className="mt-1 min-w-full text-left text-xs">
              <thead><tr><th className="pr-4">Pass</th><th className="pr-4">Check ID</th><th className="pr-4">Thực thi</th><th>Kết quả</th></tr></thead>
              <tbody>{openWorkItems.map((item) => <tr key={item.check_id}>
                <td className="pr-4">{item.pass_no}</td><td className="pr-4 font-mono">{item.check_id}</td>
                <td className="pr-4">{item.execution_status}</td><td>{item.result_status}</td>
              </tr>)}</tbody>
            </table>
          </div>}
          <div className="mt-4 border-t border-slate-200 pt-3">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Blockers</h3>
            {quality.quality?.blocking_checks == null ? <p className="mt-1 text-sm text-slate-500">Chưa có dữ liệu blocker.</p> : quality.quality.blocking_checks.length ? (
              <ul className="mt-1 list-inside list-disc text-sm text-amber-800">{quality.quality.blocking_checks.map((check) => <li key={check}>{check}</li>)}</ul>
            ) : <p className="mt-1 text-sm text-slate-600">Không ghi nhận blocker trong review này.</p>}
          </div>
          {quality.freshness_status === "INVALIDATED" && <p className="mt-3 rounded border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900">Review cũ không còn hiện hành ({quality.invalidation_event_type}). Cần rà soát lại sau thay đổi.</p>}
          {run?.artifacts.length ? <div className="mt-4 border-t border-slate-200 pt-3"><h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Artifact của lượt này</h3>{run.artifacts.map((artifact) => <a key={artifact.name} className="mt-2 block break-all text-sm text-brand underline" href={`/api/admin/jobs/${encodeURIComponent(jobCode)}/attempts/${run.attempt}/artifacts/${artifact.name.split("/").map(encodeURIComponent).join("/")}`}>{artifact.name}</a>)}</div> : null}
        </>
      )}
      <div className="mt-5 border-t border-slate-200 pt-4">
        <h3 className="font-semibold">Inventory nguồn và duyệt phạm vi</h3>
        <p className="mt-1 text-xs text-slate-600">Backend tự lấy trang PDF, sheet Excel và digest. Kỹ sư phải bổ sung target, estimate_lines, estimate_line_groups và work item còn thiếu rồi gửi duyệt. Mỗi nhóm dòng BOQ phải được gán cho cả ba pass. Scaffold không phải inventory đầy đủ; chỉ reviewer nằm trong AUDIT_QUALITY_REVIEWER_EMAILS mới được duyệt.</p>
        {inventory ? <>
          <p className="mt-2 text-xs text-slate-600">Nguồn hiện tại: <code>{inventory.source_digest.slice(0, 16)}…</code> · {inventory.approval ? (inventory.approval.is_current ? `Đã duyệt bởi ${inventory.approval.reviewer_email ?? `user ${inventory.approval.reviewer_user_id}`}` : "Bản duyệt cũ, nguồn đã đổi") : "Chưa duyệt"}</p>
          {hasUnapprovedDraft && <p className="mt-1 text-xs font-medium text-amber-800">Nội dung đã sửa nhưng chưa được reviewer duyệt.</p>}
          <label className="mt-3 block text-xs font-medium" htmlFor="audit-inventory-json">Inventory JSON</label>
          <textarea id="audit-inventory-json" className="mt-1 min-h-56 w-full font-mono text-xs" value={inventoryText} onChange={(event) => setInventoryText(event.target.value)} spellCheck={false} />
          <button type="button" onClick={approveInventory} disabled={inventoryBusy} className="btn-secondary mt-2">{inventoryBusy ? "Đang lưu…" : "LƯU DUYỆT PHẠM VI"}</button>
        </> : <p className="mt-2 text-sm text-slate-500">{inventoryError || "Đang tải inventory nguồn…"}</p>}
        {inventoryError && inventory && <p role="alert" className="mt-2 text-sm text-red-700">{inventoryError}</p>}
      </div>
    </section>
  );
}
