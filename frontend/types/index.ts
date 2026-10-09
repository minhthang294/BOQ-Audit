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
export interface AICapacity { available: boolean; ready: boolean | null; checked_at: string }
export interface ChatMessage { id: number; role: "user" | "assistant"; content: string; status: "PENDING" | "COMPLETED" | "FAILED"; created_at: string }
export interface ChatSnapshot { enabled: boolean; messages: ChatMessage[]; busy: boolean }
export interface CodexUsageWindow { used_percent: number; remaining_percent: number; resets_at?: number | null; window_duration_minutes?: number | null }
export interface CodexUsage {
  available: boolean; plan_type?: string | null; ordinary_usage_allowed?: boolean | null;
  primary?: CodexUsageWindow | null; secondary?: CodexUsageWindow | null; checked_at: string;
}
