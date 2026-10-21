"use client";

import { useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { Shell } from "@/components/Shell";
import { api, fileSize } from "@/lib/api";
import type {
  ExcelCell,
  ExcelExplanation,
  ExcelExplainTurn,
  ExcelSheetData,
  ExcelWorkbook,
} from "@/types";

type Point = { row: number; col: number };
type SelectionScope = "cell" | "range" | "sheet";
type ChatMessage = { role: "user" | "assistant"; content: string; meta?: string };

const MAX_FILE_BYTES = 25 * 1024 * 1024;
const MAX_SELECTION_CELLS = 400;

function columnLetter(value: number) {
  let result = "";
  let number = value;
  while (number > 0) {
    const remainder = (number - 1) % 26;
    result = String.fromCharCode(65 + remainder) + result;
    number = Math.floor((number - 1) / 26);
  }
  return result;
}

function rangeLabel(bounds: { minRow: number; minCol: number; maxRow: number; maxCol: number }) {
  const start = `${columnLetter(bounds.minCol)}${bounds.minRow}`;
  const end = `${columnLetter(bounds.maxCol)}${bounds.maxRow}`;
  return start === end ? start : `${start}:${end}`;
}

function parseRange(value: string, sheetName: string) {
  let cleaned = value.trim().replace(/\$/g, "");
  if (cleaned.includes("!")) {
    const [prefix, rest] = cleaned.split("!").slice(-2);
    const cleanPrefix = prefix.replace(/^'|'$/g, "").replace(/''/g, "'");
    if (cleanPrefix && cleanPrefix !== sheetName) return { error: "Vùng chọn đang trỏ sang sheet khác." };
    cleaned = rest;
  }
  const match = cleaned.match(/^([A-Za-z]{1,3})(\d+)(?::([A-Za-z]{1,3})(\d+))?$/);
  if (!match) return { error: "Vùng chọn không hợp lệ. Dùng dạng A1, W170 hoặc A1:C10." };
  const columnIndex = (letters: string) => {
    let number = 0;
    for (const character of letters.toUpperCase()) number = number * 26 + character.charCodeAt(0) - 64;
    return number;
  };
  const minCol = columnIndex(match[1]);
  const minRow = Number(match[2]);
  const maxCol = columnIndex(match[3] || match[1]);
  const maxRow = Number(match[4] || match[2]);
  const bounds = {
    minRow: Math.min(minRow, maxRow),
    maxRow: Math.max(minRow, maxRow),
    minCol: Math.min(minCol, maxCol),
    maxCol: Math.max(minCol, maxCol),
  };
  const area = (bounds.maxRow - bounds.minRow + 1) * (bounds.maxCol - bounds.minCol + 1);
  if (area > MAX_SELECTION_CELLS) {
    return { error: `Vùng chọn có ${area.toLocaleString("vi-VN")} ô, vượt quá giới hạn ${MAX_SELECTION_CELLS} ô. Hãy chọn vùng nhỏ hơn hoặc chọn cả sheet.` };
  }
  return { bounds };
}

function displayValue(cell: ExcelCell, showFormulas: boolean) {
  if (showFormulas && cell.formula) return cell.formula;
  if (cell.value === null || cell.value === undefined) return "";
  if (typeof cell.value === "number") {
    return cell.value.toLocaleString("vi-VN", { maximumFractionDigits: 8 });
  }
  if (typeof cell.value === "boolean") return cell.value ? "TRUE" : "FALSE";
  return String(cell.value);
}

function inlineText(text: string, keyPrefix: string) {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, index) => {
    const key = `${keyPrefix}-${index}`;
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return <strong key={key}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      return <code key={key} className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[0.9em] text-slate-800">{part.slice(1, -1)}</code>;
    }
    return <span key={key}>{part}</span>;
  });
}

function AnswerText({ text }: { text: string }) {
  return (
    <div className="space-y-1.5">
      {text.split(/\r?\n/).map((line, index) => {
        const trimmed = line.trim();
        if (!trimmed) return <div key={index} className="h-1" />;
        if (/^#{1,4}\s+/.test(trimmed)) {
          return <h4 key={index} className="pt-1 text-sm font-bold text-slate-950">{inlineText(trimmed.replace(/^#{1,4}\s+/, ""), `h-${index}`)}</h4>;
        }
        if (/^[-*•]\s+/.test(trimmed)) {
          return <div key={index} className="flex gap-2"><span className="text-brand">•</span><p>{inlineText(trimmed.replace(/^[-*•]\s+/, ""), `b-${index}`)}</p></div>;
        }
        if (/^\d+[.)]\s+/.test(trimmed)) {
          return <p key={index} className="pl-1">{inlineText(trimmed, `n-${index}`)}</p>;
        }
        return <p key={index}>{inlineText(trimmed, `p-${index}`)}</p>;
      })}
    </div>
  );
}

function uploadExcel(file: File, onProgress: (value: number) => void): Promise<ExcelWorkbook> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", "/api/excel/upload");
    request.withCredentials = true;
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
    };
    request.onload = () => {
      let body: { detail?: string } & Partial<ExcelWorkbook> = {};
      try { body = JSON.parse(request.responseText || "{}"); } catch {}
      if (request.status >= 200 && request.status < 300) {
        onProgress(100);
        resolve(body as ExcelWorkbook);
      } else if (request.status === 401) {
        location.href = "/login?expired=1";
        reject(new Error("Phiên đăng nhập đã hết hạn."));
      } else {
        reject(new Error(body.detail || "Không tải được file Excel."));
      }
    };
    request.onerror = () => reject(new Error("Không kết nối được máy chủ."));
    const form = new FormData();
    form.append("file", file);
    request.send(form);
  });
}

export default function ExcelExplainer() {
  const fileInput = useRef<HTMLInputElement>(null);
  const messagesEnd = useRef<HTMLDivElement>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(560);
  const ROW_HEIGHT = 22;
  const COL_WIDTH = 96;
  const ROW_HEADER_WIDTH = 52;
  const [workbook, setWorkbook] = useState<ExcelWorkbook | null>(null);
  const [sheetData, setSheetData] = useState<ExcelSheetData | null>(null);
  const [activeSheet, setActiveSheet] = useState("");
  const [anchor, setAnchor] = useState<Point | null>(null);
  const [focus, setFocus] = useState<Point | null>(null);
  const [scope, setScope] = useState<SelectionScope>("cell");
  const [dragging, setDragging] = useState(false);
  const [showFormulas, setShowFormulas] = useState(false);
  const [rangeInput, setRangeInput] = useState("");
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadError, setUploadError] = useState("");
  const [sheetLoading, setSheetLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const bounds = useMemo(() => {
    if (!anchor || !focus) return null;
    return {
      minRow: Math.min(anchor.row, focus.row),
      maxRow: Math.max(anchor.row, focus.row),
      minCol: Math.min(anchor.col, focus.col),
      maxCol: Math.max(anchor.col, focus.col),
    };
  }, [anchor, focus]);

  const selectionRef = bounds ? rangeLabel(bounds) : "";
  const selectionTitle = scope === "sheet" ? `${activeSheet} (toàn bộ sheet)` : `${activeSheet}!${selectionRef}`;
  const selectedCount = scope === "sheet" ? null : bounds ? (bounds.maxRow - bounds.minRow + 1) * (bounds.maxCol - bounds.minCol + 1) : 0;

  const totalRows = sheetData?.rows.length || 0;
  const totalCols = sheetData?.columns.length || 0;
  const visibleRowCount = Math.max(8, Math.ceil(viewportHeight / ROW_HEIGHT) + 16);
  const startIndex = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - 8);
  const endIndex = Math.min(totalRows, startIndex + visibleRowCount);
  const topSpacerHeight = startIndex * ROW_HEIGHT;
  const bottomSpacerHeight = Math.max(0, (totalRows - endIndex) * ROW_HEIGHT);
  const visibleRows = sheetData?.rows.slice(startIndex, endIndex) || [];

  const selectedFormulaCount = useMemo(() => {
    if (!sheetData) return 0;
    if (scope === "sheet") return sheetData.rows.flat().filter((cell) => cell.formula).length;
    if (!bounds) return 0;
    return sheetData.rows.flat().filter((cell) => cell.formula && cell.row >= bounds.minRow && cell.row <= bounds.maxRow && cell.col >= bounds.minCol && cell.col <= bounds.maxCol).length;
  }, [sheetData, bounds, scope]);

  const focusCell = useMemo(() => {
    if (!sheetData || !bounds) return null;
    return sheetData.rows.flat().find((cell) => cell.row === bounds.minRow && cell.col === bounds.minCol) || null;
  }, [sheetData, bounds]);

  useEffect(() => {
    const stopDragging = () => setDragging(false);
    window.addEventListener("mouseup", stopDragging);
    return () => window.removeEventListener("mouseup", stopDragging);
  }, []);

  useEffect(() => {
    if (!gridRef.current) return;
    const element = gridRef.current;
    const update = () => setViewportHeight(element.clientHeight || 560);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, [sheetData]);

  useEffect(() => {
    if (scope !== "sheet" && selectionRef && selectionRef !== rangeInput) setRangeInput(selectionRef);
  }, [scope, selectionRef]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    messagesEnd.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, busy]);

  function pickFile(candidate: File | undefined) {
    if (!candidate) return;
    setUploadError("");
    const extension = candidate.name.toLowerCase().split(".").pop();
    if (extension !== "xlsx" && extension !== "xlsm") {
      setUploadError("Chỉ nhận file .xlsx hoặc .xlsm.");
      return;
    }
    if (candidate.size > MAX_FILE_BYTES) {
      setUploadError("File vượt quá 25 MB. Hãy giảm dung lượng hoặc tách bớt sheet.");
      return;
    }
    setFile(candidate);
  }

  async function loadSheetFor(book: ExcelWorkbook, sheetName: string) {
    setSheetLoading(true);
    setError("");
    try {
      const data = await api<ExcelSheetData>(
        `/excel/${book.code}/sheet?name=${encodeURIComponent(sheetName)}&max_rows=2000&max_cols=100`,
      );
      setSheetData(data);
      setActiveSheet(sheetName);
      const first = data.rows[0]?.[0];
      const point = first ? { row: first.row, col: first.col } : { row: data.row_start, col: data.col_start };
      setAnchor(point);
      setFocus(point);
      setScope("cell");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không tải được sheet.");
    } finally {
      setSheetLoading(false);
    }
  }

  async function startUpload() {
    if (!file || uploading) return;
    setUploading(true);
    setUploadProgress(0);
    setUploadError("");
    try {
      const result = await uploadExcel(file, setUploadProgress);
      setWorkbook(result);
      setMessages([]);
      setQuestion("");
      const firstSheet = result.sheets.find((sheet) => !sheet.hidden) || result.sheets[0];
      if (firstSheet) await loadSheetFor(result, firstSheet.name);
    } catch (reason) {
      setUploadError(reason instanceof Error ? reason.message : "Không tải được file Excel.");
    } finally {
      setUploading(false);
    }
  }

  async function changeFile() {
    if (workbook) {
      try { await api(`/excel/${workbook.code}`, { method: "DELETE" }); } catch {}
    }
    setWorkbook(null);
    setSheetData(null);
    setActiveSheet("");
    setMessages([]);
    setQuestion("");
    setError("");
    setUploadError("");
    setFile(null);
    setUploadProgress(0);
    if (fileInput.current) fileInput.current.value = "";
  }

  function beginSelection(cell: ExcelCell, event: ReactMouseEvent<HTMLTableCellElement>) {
    event.preventDefault();
    setAnchor({ row: cell.row, col: cell.col });
    setFocus({ row: cell.row, col: cell.col });
    setScope("cell");
    setDragging(true);
  }

  function extendSelection(cell: ExcelCell) {
    if (!dragging || !anchor) return;
    setFocus({ row: cell.row, col: cell.col });
    setScope(anchor.row === cell.row && anchor.col === cell.col ? "cell" : "range");
  }

  function applyRangeInput() {
    if (!activeSheet) return;
    const parsed = parseRange(rangeInput, activeSheet);
    if ("error" in parsed) {
      setError(parsed.error || "Vùng chọn không hợp lệ.");
      return;
    }
    const next = parsed.bounds!;
    setAnchor({ row: next.minRow, col: next.minCol });
    setFocus({ row: next.maxRow, col: next.maxCol });
    setScope(next.minRow === next.maxRow && next.minCol === next.maxCol ? "cell" : "range");
    setError("");
  }

  async function ask(suggestion?: string) {
    if (!workbook || !sheetData || busy) return;
    const content = (suggestion ?? question).trim();
    const currentScope = scope;
    const currentBounds = bounds;
    if (currentScope !== "sheet" && !currentBounds) {
      setError("Hãy chọn một ô hoặc vùng trước khi hỏi.");
      return;
    }
    const requestedScope = currentScope === "sheet" ? "sheet" : currentBounds!.minRow === currentBounds!.maxRow && currentBounds!.minCol === currentBounds!.maxCol ? "cell" : "range";
    const selectedRange = requestedScope === "sheet" ? "" : rangeLabel(currentBounds!);
    const history: ExcelExplainTurn[] = messages.slice(-6).map((message) => ({ role: message.role, content: message.content.slice(0, 4000) }));
    const shownQuestion = content || (requestedScope === "sheet" ? "Hãy tóm tắt và giải thích sheet này" : `Hãy giải thích vùng ${selectedRange}`);
    setMessages((current) => [...current, { role: "user", content: shownQuestion }]);
    setQuestion("");
    setBusy(true);
    setError("");
    try {
      const result = await api<ExcelExplanation>(`/excel/${workbook.code}/explain`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sheet: activeSheet,
          scope: requestedScope,
          selection: selectedRange,
          question: content,
          history,
        }),
      });
      setMessages((current) => [...current, {
        role: "assistant",
        content: result.explanation,
        meta: `Đã đọc ${result.cells_examined} ô và ${result.references_examined} ô tham chiếu${result.truncated ? " (có rút gọn)" : ""}.`,
      }]);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "Không tạo được giải thích.";
      setError(message);
      setMessages((current) => [...current, { role: "assistant", content: `Không tạo được giải thích: ${message}` }]);
    } finally {
      setBusy(false);
    }
  }

  const suggestions = scope === "sheet"
    ? ["Tóm tắt cấu trúc và mục đích của sheet này", "Sheet này có những công thức quan trọng nào?", "Những lỗi hoặc dấu hiệu bất thường cần kiểm tra?"]
    : ["Giải thích công thức trong vùng này", "Số liệu này lấy từ đâu?", "Kiểm tra lỗi hoặc giả định tiềm ẩn"];

  return (
    <Shell wide>
      <section className="mb-8">
        <div className="technical-rule mb-6" />
        <p className="eyebrow">Công cụ 03 · Beta</p>
        <h1 className="page-title mt-2">Giải thích<br />Excel</h1>
        <p className="mt-4 max-w-3xl text-base leading-6 text-slate-600">
          Tải file BOQ, chọn ô, vùng hoặc sheet chưa hiểu. AI đọc công thức, giá trị cache và các ô tham chiếu gần nhất để giải thích nguồn số liệu, cách kiểm tra và lỗi tiềm ẩn.
        </p>
        <p className="mt-3 max-w-3xl border-l-4 border-brand bg-brand/5 px-4 py-3 text-sm leading-6 text-slate-700">
          AI không sửa file gốc. File được lưu tạm 48 giờ và chỉ chủ tài khoản tải lên mới đọc được. Kết quả là bản nháp, cần đối chiếu lại với bảng tính trước khi dùng.
        </p>
      </section>

      {!workbook ? (
        <section className="mx-auto max-w-3xl border-2 border-slate-950 bg-white p-6 sm:p-9">
          <p className="eyebrow">Bước 1</p>
          <h2 className="section-title mt-2">Chọn file Excel cần giải thích</h2>
          <p className="mt-3 text-sm leading-6 text-slate-600">Hỗ trợ .xlsx và .xlsm, tối đa 25 MB. File có macro vẫn chỉ được đọc dữ liệu, không chạy macro.</p>
          <div
            className="mt-6 flex min-h-48 flex-col items-center justify-center border-2 border-dashed border-slate-300 bg-slate-50 p-6 text-center"
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => { event.preventDefault(); pickFile(event.dataTransfer.files?.[0]); }}
          >
            <input
              ref={fileInput}
              type="file"
              accept=".xlsx,.xlsm,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel"
              className="sr-only"
              onChange={(event) => pickFile(event.target.files?.[0])}
            />
            <p className="font-mono text-sm text-slate-500">.xlsx / .xlsm</p>
            <button type="button" onClick={() => fileInput.current?.click()} className="btn mt-4 border-2 border-slate-950 bg-white px-5 py-3 font-semibold text-slate-950 hover:border-brand hover:text-brand">
              Chọn file từ máy
            </button>
            <p className="mt-3 text-xs text-slate-500">hoặc kéo thả file vào khung này</p>
          </div>
          {file && (
            <div className="mt-4 flex items-center justify-between gap-4 border border-slate-300 bg-slate-50 px-4 py-3">
              <div className="min-w-0">
                <p className="truncate font-semibold text-slate-900">{file.name}</p>
                <p className="text-xs text-slate-500">{fileSize(file.size)}</p>
              </div>
              <button type="button" onClick={() => { setFile(null); if (fileInput.current) fileInput.current.value = ""; }} className="text-sm font-semibold text-slate-500 hover:text-slate-950">Bỏ chọn</button>
            </div>
          )}
          {uploading && (
            <div className="mt-4">
              <div className="h-2 w-full bg-slate-200"><div className="h-2 bg-brand transition-all" style={{ width: `${uploadProgress}%` }} /></div>
              <p className="mt-2 text-xs text-slate-500">Đang tải và kiểm tra file... {uploadProgress}%</p>
            </div>
          )}
          {uploadError && <p className="mt-4 border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800">{uploadError}</p>}
          <button type="button" onClick={startUpload} disabled={!file || uploading} className="btn btn-primary mt-6 w-full px-5 py-4 text-base font-semibold disabled:cursor-not-allowed disabled:opacity-40">
            {uploading ? "Đang xử lý..." : "Tải lên và mở bảng tính →"}
          </button>
        </section>
      ) : (
        <div className="space-y-4">
          <section className="flex flex-wrap items-center justify-between gap-4 border-2 border-slate-950 bg-white p-4 sm:p-5">
            <div className="min-w-0">
              <p className="eyebrow">Đang mở</p>
              <h2 className="mt-1 truncate text-lg font-bold text-slate-950">{workbook.filename}</h2>
              <p className="mt-1 text-xs text-slate-500">{fileSize(workbook.file_size)} · {workbook.sheets.length} sheet · lưu tạm 48 giờ</p>
            </div>
            <button type="button" onClick={changeFile} className="border border-slate-400 bg-white px-4 py-2 text-sm font-semibold text-slate-700 hover:border-slate-950 hover:text-slate-950">Đổi tệp</button>
          </section>

          <div className="flex gap-1 overflow-x-auto border-b-2 border-slate-950">
            {workbook.sheets.map((sheet) => (
              <button
                key={sheet.name}
                type="button"
                onClick={() => loadSheetFor(workbook, sheet.name)}
                className={`shrink-0 border-2 border-b-0 px-4 py-2 text-sm font-semibold ${activeSheet === sheet.name ? "border-slate-950 bg-slate-950 text-white" : "border-transparent bg-slate-100 text-slate-600 hover:bg-slate-200"}`}
                title={sheet.hidden ? "Sheet đang ẩn" : sheet.name}
              >
                {sheet.name}{sheet.hidden ? " · ẩn" : ""}
              </button>
            ))}
          </div>

          <div className="grid gap-4 lg:grid-cols-[minmax(0,1.35fr)_minmax(360px,0.65fr)]">
            <div className="min-w-0 space-y-4">
              <div className="flex flex-wrap items-center gap-3 border border-slate-300 bg-slate-50 p-3">
                <label className="inline-flex items-center gap-2 text-sm font-semibold text-slate-700">
                  <input type="checkbox" checked={showFormulas} onChange={(event) => setShowFormulas(event.target.checked)} className="accent-brand" />
                  Hiện công thức
                </label>
                <div className="flex min-w-0 flex-1 items-center gap-2 sm:flex-none">
                  <input
                    value={rangeInput}
                    onChange={(event) => setRangeInput(event.target.value)}
                    onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); applyRangeInput(); } }}
                    placeholder="W170 hoặc W170:W201"
                    className="min-w-0 flex-1 border border-slate-400 bg-white px-3 py-2 font-mono text-xs text-slate-900 outline-none focus:border-brand sm:w-48"
                    aria-label="Nhập vùng chọn"
                  />
                  <button type="button" onClick={applyRangeInput} className="border border-slate-950 bg-white px-3 py-2 text-xs font-bold text-slate-950 hover:bg-slate-950 hover:text-white">Chọn</button>
                </div>
                <button type="button" onClick={() => setScope("sheet")} className={`border px-3 py-2 text-xs font-bold ${scope === "sheet" ? "border-brand bg-brand text-white" : "border-slate-950 bg-white text-slate-950 hover:bg-slate-950 hover:text-white"}`}>Chọn cả sheet</button>
                <p className="ml-auto max-w-full truncate font-mono text-xs text-slate-500">{selectionTitle}{selectedCount !== null ? ` · ${selectedCount.toLocaleString("vi-VN")} ô` : ""}</p>
              </div>

              <div className="flex items-center gap-2 border border-[#c9ccd1] bg-white px-3 py-2">
                <span className="min-w-16 font-mono text-xs font-semibold text-[#5f6368]">{selectionTitle}</span>
                <span className="text-[#c9ccd1]">ƒx</span>
                <div className="min-w-0 flex-1 truncate font-mono text-xs text-[#202124]">{focusCell?.formula || (focusCell?.value === null ? "" : String(focusCell?.value ?? "")) || "\u00a0"}</div>
              </div>

              <div
                ref={gridRef}
                onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
                className="relative max-h-[72vh] overflow-auto border border-[#c9ccd1] bg-white shadow-inner"
              >
                {sheetLoading ? (
                  <div className="flex min-h-72 items-center justify-center p-8 text-sm text-slate-500">Đang tải toàn bộ sheet...</div>
                ) : sheetData ? (
                  <>
                    <div aria-hidden="true" style={{ height: topSpacerHeight }} />
                    <table className="border-separate border-spacing-0 text-xs" style={{ tableLayout: "fixed", width: ROW_HEADER_WIDTH + totalCols * COL_WIDTH }}>
                      <colgroup>
                        <col style={{ width: ROW_HEADER_WIDTH }} />
                        {sheetData.columns.map((column) => <col key={column} style={{ width: COL_WIDTH }} />)}
                      </colgroup>
                      <thead>
                        <tr>
                          <th className="sticky left-0 top-0 z-40 border border-[#d9d9d9] bg-[#f8f9fa] px-1 py-1 text-center font-mono text-[10px] font-medium text-[#5f6368]">#</th>
                          {sheetData.columns.map((column) => (
                            <th key={column} className="sticky top-0 z-30 border border-[#d9d9d9] bg-[#f8f9fa] px-2 py-1 text-center font-mono text-[10px] font-medium text-[#5f6368]">{column}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {visibleRows.map((row) => (
                          <tr key={row[0]?.row ?? "blank-row"} style={{ height: ROW_HEIGHT }}>
                            <th className="sticky left-0 z-20 border border-[#d9d9d9] bg-[#f8f9fa] px-1 py-0 text-right font-mono text-[10px] font-normal text-[#5f6368]" style={{ height: ROW_HEIGHT }}>{row[0]?.row}</th>
                            {row.map((cell) => {
                              const selected = scope !== "sheet" && bounds && cell.row >= bounds.minRow && cell.row <= bounds.maxRow && cell.col >= bounds.minCol && cell.col <= bounds.maxCol;
                              const value = displayValue(cell, showFormulas);
                              return (
                                <td
                                  key={cell.ref}
                                  onMouseDown={(event) => beginSelection(cell, event)}
                                  onMouseEnter={() => extendSelection(cell)}
                                  className={`cursor-cell border border-[#e2e2e2] px-2 py-0 align-middle ${selected ? "bg-[#d2e3fc] shadow-[inset_0_0_0_1px_#1a73e8]" : "bg-white"} ${cell.kind === "blank" ? "text-[#bdc1c6]" : "text-[#202124]"}`}
                                  style={{ height: ROW_HEIGHT, lineHeight: `${ROW_HEIGHT - 2}px` }}
                                  title={cell.formula || (cell.value === null ? "" : String(cell.value))}
                                >
                                  <div className="max-w-full truncate">{value || "\u00a0"}</div>
                                </td>
                              );
                            })}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <div aria-hidden="true" style={{ height: bottomSpacerHeight }} />
                  </>
                ) : (
                  <div className="flex min-h-72 items-center justify-center p-8 text-sm text-slate-500">Chưa có dữ liệu sheet.</div>
                )}
              </div>


              {sheetData?.truncated && (
                <p className="border border-amber-300 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">
                  Lưới chỉ hiển thị {sheetData.row_end - sheetData.row_start + 1} dòng × {sheetData.col_end - sheetData.col_start + 1} cột đầu. Nhập vùng như <span className="font-mono">W170:W201</span> để hỏi các ô ngoài lưới; AI vẫn đọc được tối đa 400 ô mỗi lần.
                </p>
              )}

              <section className="border border-slate-300 bg-white p-4">
                <p className="eyebrow">Vùng đang chọn</p>
                <div className="mt-2 flex flex-wrap items-baseline justify-between gap-2">
                  <p className="break-all font-mono text-sm font-semibold text-slate-950">{selectionTitle}</p>
                  <p className="text-xs text-slate-500">{selectedFormulaCount} công thức trong lưới xem trước</p>
                </div>
                {scope === "sheet" ? (
                  <p className="mt-3 text-sm leading-6 text-slate-600">AI sẽ đọc cấu trúc sheet, các dòng mẫu đầu/cuối và tối đa 40 công thức tiêu biểu. Với sheet rất lớn, hãy chọn một vùng cụ thể để có giải thích sâu hơn.</p>
                ) : focusCell ? (
                  <div className="mt-3 space-y-2 text-sm">
                    <p className="text-slate-600">Ô <span className="font-mono font-semibold text-slate-950">{focusCell.ref}</span> · kiểu <span className="font-mono">{focusCell.kind}</span> · định dạng <span className="font-mono">{focusCell.number_format || "General"}</span></p>
                    <p className="break-words text-slate-800">Giá trị: <span className="font-medium">{focusCell.value === null ? "(trống)" : String(focusCell.value)}</span></p>
                    {focusCell.formula && <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all border border-sky-200 bg-sky-50 p-3 font-mono text-xs leading-5 text-sky-950">{focusCell.formula}</pre>}
                  </div>
                ) : (
                  <p className="mt-3 text-sm leading-6 text-slate-600">Ô đầu của vùng nằm ngoài lưới xem trước. AI vẫn đọc vùng này khi bạn gửi câu hỏi.</p>
                )}
              </section>
            </div>

            <aside className="min-w-0 space-y-3 lg:sticky lg:top-4 lg:self-start">
              <section className="border-2 border-slate-950 bg-white">
                <div className="border-b border-slate-300 bg-slate-50 p-4">
                  <p className="eyebrow">Trợ lý giải thích</p>
                  <h2 className="section-title mt-2 text-xl">Hỏi về vùng đang chọn</h2>
                  <p className="mt-2 text-xs leading-5 text-slate-500">AI chỉ thấy vùng chọn, hàng/cột tiêu đề gần kề và các ô được tham chiếu (tối đa 2 tầng).</p>
                </div>
                <div className="max-h-[48vh] space-y-4 overflow-y-auto p-4">
                  {messages.length === 0 && !busy ? (
                    <div>
                      <p className="text-sm leading-6 text-slate-600">Chọn một ô như <span className="font-mono">W170</span>, bôi đen một vùng, hoặc chọn cả sheet. Sau đó hỏi:</p>
                      <div className="mt-3 space-y-2">
                        {suggestions.map((suggestion) => (
                          <button key={suggestion} type="button" onClick={() => setQuestion(suggestion)} className="block w-full border border-slate-300 bg-white px-3 py-2 text-left text-xs font-semibold text-slate-700 hover:border-brand hover:text-brand">{suggestion}</button>
                        ))}
                      </div>
                    </div>
                  ) : (
                    messages.map((message, index) => (
                      <div key={index} className={message.role === "user" ? "ml-6 border border-slate-300 bg-slate-100 p-3" : "mr-1 border-l-4 border-brand bg-brand/5 p-3"}>
                        <p className="mb-1 text-[10px] font-bold uppercase tracking-widest text-slate-400">{message.role === "user" ? "Bạn hỏi" : "SBTech AI"}</p>
                        {message.role === "assistant" ? <div className="text-sm leading-6 text-slate-800"><AnswerText text={message.content} /></div> : <p className="whitespace-pre-wrap break-words text-sm leading-6 text-slate-800">{message.content}</p>}
                        {message.meta && <p className="mt-2 border-t border-slate-200 pt-2 text-[10px] text-slate-400">{message.meta}</p>}
                      </div>
                    ))
                  )}
                  {busy && <div className="mr-1 border-l-4 border-brand bg-brand/5 p-3 text-sm text-slate-600">AI đang đọc vùng chọn và các ô tham chiếu...</div>}
                  <div ref={messagesEnd} />
                </div>
                <div className="border-t border-slate-300 p-3">
                  <textarea
                    value={question}
                    onChange={(event) => setQuestion(event.target.value)}
                    onKeyDown={(event) => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); ask(); } }}
                    rows={3}
                    maxLength={2000}
                    placeholder="Ví dụ: Vì sao ô này ra số này? Số liệu lấy từ đâu? Công thức có lỗi gì?"
                    className="w-full resize-y border border-slate-400 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-brand"
                  />
                  <div className="mt-2 flex items-center justify-between gap-2">
                    <p className="text-[10px] leading-4 text-slate-400">Ctrl/⌘ + Enter để gửi · tối đa 2.000 ký tự</p>
                    <button type="button" onClick={() => ask()} disabled={busy || !sheetData} className="btn btn-primary shrink-0 px-4 py-2 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-40">Gửi câu hỏi →</button>
                  </div>
                </div>
              </section>
              {error && <p className="border border-red-300 bg-red-50 px-4 py-3 text-sm leading-5 text-red-800">{error}</p>}
              <p className="text-[11px] leading-5 text-slate-500">Kết quả AI có thể sai do công thức động, dữ liệu ngoài workbook hoặc giá trị cache cũ. Luôn kiểm tra lại ô nguồn và công thức gốc.</p>
            </aside>
          </div>
        </div>
      )}
    </Shell>
  );
}
