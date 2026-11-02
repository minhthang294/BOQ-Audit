export type Role = "CUSTOMER" | "ADMIN";
export type JobStatus = "SUBMITTED" | "PROCESSING" | "WAITING_FOR_INFO" | "REVIEW" | "COMPLETED" | "FAILED";
export type OutputType = "EXCEL_REPORT" | "ESTIMATE_REPORT" | "ANNOTATED_PDF" | "OTHER";

export interface User { id: number; name: string; username: string; role: Role }
export interface AdminUser extends User { is_active: boolean; created_at: string; job_count: number }
export interface JobOutput { id: number; file_type: OutputType; original_filename: string; file_size: number; created_at: string }
export interface NarrativeInput { original_filename: string; file_size: number; created_at: string }
export interface EstimateInput { original_filename: string; file_size: number; created_at: string }
export interface Job {
  job_code: string; project_name: string; description?: string; original_filename: string; status: JobStatus;
  critical_errors: number; warnings: number; customer_notes?: string; admin_notes?: string;
  created_at: string; started_at?: string; completed_at?: string; updated_at: string; outputs: JobOutput[];
  estimate_input?: EstimateInput | null; narrative_input?: NarrativeInput | null; user?: User;
  turnaround_seconds: number; ai_processing_seconds: number | null; ai_timing_complete: boolean;
  current_attempt_seconds: number | null; audit_runs: AuditRun[];
}
export interface AuditRun { attempt: number; started_at: string; ended_at: string | null; duration_seconds: number | null; status: string }
export interface AttemptArtifact { name: string; size: number }
export interface AttemptArtifacts { attempts: { attempt: number; status: string; artifacts: AttemptArtifact[] }[]; legacy: AttemptArtifact[] }
export interface AdminJobQuality {
  job_code: string; controlled_audit_history: boolean; attempt: number | null; stage: string | null;
  freshness_status: "NOT_REVIEWED" | "NOT_VERIFIED" | "INVALIDATED";
  invalidation_event_type: string | null;
  quality: {
    controller_status: string; candidate_release_status: string | null;
    advisory: boolean; enforcing: boolean; validator_exit_code: number | null;
    open_record_count: number | null; open_issue_count: number | null;
    blocking_checks: string[] | null; critical_errors: number | null; warnings: number | null;
    error_code: string | null; missing_outputs: string[] | null;
  } | null;
  coverage_status: "UNAVAILABLE" | string; coverage: Record<string, unknown> | null;
  coverage_limitation: string;
  sanity: { status: string; checked_rows: number; input_digest: string | null; anomalies: { row_id: string | number; anomaly: string; [key: string]: unknown }[]; advisory: boolean };
  stage_gates: { attempt: number; pass_no: number; checkpoint_version: number; snapshot_id: string; controller_status: string; candidate_release_status: string | null; blocking_checks: string[]; error_code: string | null; advanced: boolean; integrity_status: "VERIFIED" | "INVALID"; created_at: string }[];
  gate_repair_status: "NOT_APPLICABLE" | "GATE_INVALID" | "CHECKPOINT_UNAVAILABLE" | "NOT_READY" | "CONTROLLER_CONTRACT" | "VALIDATOR_ERROR" | "UNMAPPED_BLOCKER";
  gate_repair_attempts: number;
  gate_repair_request_status: string | null;
  report_source: { snapshot_digest: string; classification_digest: string | null; checkpoint_version: number; contract_version: number; ledger_record_counts: Record<string, number>; issue_final_status_counts: Record<string, number>; issue_candidate_count: number; pdf_mark_required_issue_count: number; classified_issue_count: number; unclassified_issue_count: number } | null;
}
export interface AdminCheckpointIssues {
  snapshot_digest: string; classification_digest: string; total: number; offset: number; limit: number;
  issues: { issue_id: string; claim: string; final_status: string; source_refs: { page_ids: string[]; sheet_ids: string[]; target_ids: string[] }; lineage_ids: string[]; severity: string | null; review_reason: string | null; classified: boolean }[];
}
export interface AdminAnnotationDraft {
  draft_id: number; snapshot_digest: string; classification_digest: string; output_pdf_sha256: string;
  annotation_qa_status: "PASS" | "FAIL" | "UNVERIFIED";
  missing_active_issue_ids: string[]; review_counts: Record<string, number>; is_current: boolean;
  marks: { mark_id: string; issue_id: string; lineage_ids: string[]; severity: string; pdf_page: number; note: string; review_status: "PASS" | "FAIL" | "UNVERIFIED" }[];
}
export interface AdminExcelReportReview {
  draft_id: number; snapshot_digest: string; classification_digest: string;
  annotation_pdf_sha256: string | null; workbook_sha256: string;
  structural_qa: "PASS" | "FAIL"; latest_result: "PASS" | "FAIL" | "UNREVIEWED";
  latest_reason: string | null; reviewer_user_id: number | null; revision_no: number | null;
  is_current: boolean; reviewed_sections: string[];
}
export interface AdminReleaseSignoff {
  signoff_id: number; revision_no: number; result: "APPROVE_FULL" | "APPROVE_PARTIAL" | "REJECT";
  snapshot_digest: string; classification_digest: string; checkpoint_version: number;
  pdf_draft_id: number; pdf_sha256: string; excel_draft_id: number; excel_sha256: string;
  reviewed_unmarked_pdf: boolean;
  unresolved_scope: string[]; is_current: boolean;
}
export interface AdminInventoryApproval {
  job_code: string; source_digest: string; reviewer_user_id: number; reviewer_email: string | null;
  approved_at: string; is_current: boolean; inventory: Record<string, unknown>;
}
export interface AdminJobInventory {
  job_code: string; source_digest: string; scaffold: Record<string, unknown>;
  approval: AdminInventoryApproval | null;
}
export interface AICapacity { available: boolean; ready: boolean | null; checked_at: string }
export interface ChatMessage { id: number; role: "user" | "assistant"; content: string; status: "PENDING" | "COMPLETED" | "FAILED"; created_at: string }
export interface ChatSnapshot { enabled: boolean; messages: ChatMessage[]; busy: boolean }
export interface CodexUsageWindow { used_percent: number; remaining_percent: number; resets_at?: number | null; window_duration_minutes?: number | null }
export interface CodexUsage {
  available: boolean; plan_type?: string | null; ordinary_usage_allowed?: boolean | null;
  primary?: CodexUsageWindow | null; secondary?: CodexUsageWindow | null; checked_at: string;
}

export interface ExcelSheetInfo { name: string; max_row: number; max_column: number; hidden: boolean }
export interface ExcelWorkbook {
  code: string; filename: string; file_size: number; file_type: string; sheets: ExcelSheetInfo[]; active_sheet: string; created_at: string;
}
export interface ExcelExplanation {
  explanation: string; cells_examined: number; references_examined: number; truncated: boolean; evidence_summary: string;
}
export interface ExcelExplainTurn { role: "user" | "assistant"; content: string }
