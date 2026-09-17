export type Role = "CUSTOMER" | "ADMIN";
export type JobStatus = "SUBMITTED" | "PROCESSING" | "WAITING_FOR_INFO" | "REVIEW" | "COMPLETED" | "FAILED";
export type OutputType = "EXCEL_REPORT" | "ANNOTATED_PDF" | "OTHER";

export interface User { id: number; name: string; username: string; role: Role }
export interface JobOutput { id: number; file_type: OutputType; original_filename: string; file_size: number; created_at: string }
export interface Job {
  job_code: string; project_name: string; description?: string; original_filename: string; status: JobStatus;
  critical_errors: number; warnings: number; customer_notes?: string; admin_notes?: string;
  created_at: string; started_at?: string; completed_at?: string; updated_at: string; outputs: JobOutput[]; user?: User;
}
