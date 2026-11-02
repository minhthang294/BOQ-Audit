import type { JobOutput, OutputType } from "../types";

export interface OutputDownload {
  item: JobOutput;
  label: string;
}

const EXCEL_LABEL = "TẢI BÁO CÁO RÀ SOÁT BOQ";
const ESTIMATE_LABEL = "TẢI BÁO CÁO RÀ SOÁT DỰ TOÁN";
const PDF_LABEL = "TẢI PDF ĐÁNH DẤU";

function findOutput(outputs: JobOutput[], type: OutputType): JobOutput | undefined {
  return outputs.find(output => output.file_type === type);
}

export function buildDownloadLinks(outputs: JobOutput[]): OutputDownload[] {
  const excel = findOutput(outputs, "EXCEL_REPORT");
  const estimate = findOutput(outputs, "ESTIMATE_REPORT");
  const pdf = findOutput(outputs, "ANNOTATED_PDF");
  const links: OutputDownload[] = [];
  if (excel) links.push({ item: excel, label: EXCEL_LABEL });
  if (estimate) links.push({ item: estimate, label: ESTIMATE_LABEL });
  if (pdf) {
    links.push({ item: pdf, label: PDF_LABEL });
  }
  return links;
}
