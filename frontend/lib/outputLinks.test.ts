import test from "node:test";
import assert from "node:assert/strict";
import { buildDownloadLinks } from "./outputLinks.ts";
import type { JobOutput, OutputType } from "../types";

function output(id: number, file_type: OutputType, original_filename: string, file_size: number): JobOutput {
  return { id, file_type, original_filename, file_size, created_at: "2026-10-31T00:00:00+00:00" };
}

test("keeps both Excel downloads when the reports are distinct artifacts", () => {
  const links = buildDownloadLinks([
    output(1, "EXCEL_REPORT", "boq.xlsx", 100),
    output(2, "ESTIMATE_REPORT", "estimate.xlsx", 200),
  ]);
  assert.deepEqual(links.map(link => link.label), ["TẢI BÁO CÁO RÀ SOÁT BOQ", "TẢI BÁO CÁO RÀ SOÁT DỰ TOÁN"]);
});

test("keeps both Excel downloads when filenames and sizes match", () => {
  const links = buildDownloadLinks([
    output(1, "EXCEL_REPORT", "BOQ_Audit_Report.xlsx", 512),
    output(2, "ESTIMATE_REPORT", "BOQ_Audit_Report.xlsx", 512),
  ]);
  assert.deepEqual(links.map(link => link.label), ["TẢI BÁO CÁO RÀ SOÁT BOQ", "TẢI BÁO CÁO RÀ SOÁT DỰ TOÁN"]);
  assert.deepEqual(links.map(link => link.item.id), [1, 2]);
});

test("shows the estimate report alone when no BOQ workbook was published", () => {
  const links = buildDownloadLinks([output(7, "ESTIMATE_REPORT", "estimate.xlsx", 200)]);
  assert.deepEqual(links.map(link => link.label), ["TẢI BÁO CÁO RÀ SOÁT DỰ TOÁN"]);
});

test("always keeps the annotated PDF separate from workbooks", () => {
  const links = buildDownloadLinks([
    output(1, "EXCEL_REPORT", "BOQ_Audit_Report.xlsx", 512),
    output(2, "ESTIMATE_REPORT", "BOQ_Audit_Report.xlsx", 512),
    output(3, "ANNOTATED_PDF", "BOQ_Audit_Annotated.pdf", 512),
  ]);
  assert.deepEqual(links.map(link => link.label), ["TẢI BÁO CÁO RÀ SOÁT BOQ", "TẢI BÁO CÁO RÀ SOÁT DỰ TOÁN", "TẢI PDF ĐÁNH DẤU"]);
});

test("ignores unrelated output types and missing reports", () => {
  assert.deepEqual(buildDownloadLinks([]), []);
  assert.deepEqual(buildDownloadLinks([output(9, "OTHER", "notes.txt", 10)]), []);
});
