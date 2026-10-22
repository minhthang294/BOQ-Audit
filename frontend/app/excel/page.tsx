"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { Shell } from "@/components/Shell";
import { api, fileSize } from "@/lib/api";
import type { ExcelExplainTurn, ExcelExplanation, ExcelWorkbook } from "@/types";

type ChatMessage = { role: "user" | "assistant"; content: string; meta?: string };
const MAX_FILE_BYTES = 25 * 1024 * 1024;

function inlineMarkdown(text: string, keyPrefix: string): ReactNode[] {
  return text.split(/(\[[^\]]+\]\(https?:\/\/[^\s)]+\)|`[^`]+`|\*\*[^*]+\*\*|__[^_]+__|\*[^*]+\*|_[^_]+_)/g).map((part, index) => {
    const key = `${keyPrefix}-${index}`;
    const link = part.match(/^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/i);
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) return <code key={key} className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[0.9em] text-slate-800">{part.slice(1, -1)}</code>;
    if (link) return <a key={key} href={link[2]} target="_blank" rel="noopener noreferrer" className="font-medium text-brand underline">{link[1]}</a>;
    if ((part.startsWith("**") && part.endsWith("**")) || (part.startsWith("__") && part.endsWith("__"))) return <strong key={key}>{part.slice(2, -2)}</strong>;
    if ((part.startsWith("*") && part.endsWith("*")) || (part.startsWith("_") && part.endsWith("_"))) return <em key={key}>{part.slice(1, -1)}</em>;
    return <span key={key}>{part}</span>;
  });
}

function markdownCells(line: string): string[] {
  return line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
}

function isTableDivider(line: string): boolean {
  return /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(line.trim());
}

function AnswerText({ text }: { text: string }) {
  const lines = text.replace(/\r/g, "").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i].trim();
    if (!line) { i++; continue; }
    const key = `md-${i}`;
    if (line.startsWith("```")) {
      const code: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith("```")) code.push(lines[i++]);
      i++;
      blocks.push(<pre key={key} className="overflow-x-auto rounded bg-slate-900 p-3 text-xs leading-5 text-slate-100"><code>{code.join("\n")}</code></pre>);
    } else if (/^#{1,6}\s+/.test(line)) {
      const level = line.match(/^#+/)?.[0].length ?? 2;
      const heading = inlineMarkdown(line.replace(/^#{1,6}\s+/, ""), key);
      blocks.push(level <= 2 ? <h3 key={key} className="pt-2 text-base font-bold text-slate-950">{heading}</h3> : <h4 key={key} className="pt-1 text-sm font-bold text-slate-950">{heading}</h4>);
      i++;
    } else if (i + 1 < lines.length && line.includes("|") && isTableDivider(lines[i + 1])) {
      const headers = markdownCells(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim().includes("|")) rows.push(markdownCells(lines[i++]));
      blocks.push(<div key={key} className="overflow-x-auto"><table className="min-w-full border-collapse text-left text-xs"><thead><tr>{headers.map((cell, col) => <th key={col} className="border border-slate-300 bg-slate-100 px-2 py-1.5 font-bold">{inlineMarkdown(cell, `${key}-h${col}`)}</th>)}</tr></thead><tbody>{rows.map((row, rowIndex) => <tr key={rowIndex}>{headers.map((_, col) => <td key={col} className="border border-slate-300 px-2 py-1.5 align-top">{inlineMarkdown(row[col] ?? "", `${key}-${rowIndex}-${col}`)}</td>)}</tr>)}</tbody></table></div>);
    } else if (/^>\s?/.test(line)) {
      const quote: string[] = [];
      while (i < lines.length && /^>\s?/.test(lines[i].trim())) quote.push(lines[i++].trim().replace(/^>\s?/, ""));
      blocks.push(<blockquote key={key} className="border-l-2 border-slate-400 pl-3 text-slate-600">{quote.map((part, n) => <p key={n}>{inlineMarkdown(part, `${key}-${n}`)}</p>)}</blockquote>);
    } else if (/^[-*+]\s+/.test(line) || /^\d+[.)]\s+/.test(line)) {
      const ordered = /^\d+[.)]\s+/.test(line);
      const items: string[] = [];
      const pattern = ordered ? /^\d+[.)]\s+/ : /^[-*+]\s+/;
      while (i < lines.length && pattern.test(lines[i].trim())) items.push(lines[i++].trim().replace(pattern, ""));
      const List = ordered ? "ol" : "ul";
      blocks.push(<List key={key} className={`${ordered ? "list-decimal" : "list-disc"} space-y-1 pl-5 marker:text-brand`}>{items.map((item, n) => <li key={n}>{inlineMarkdown(item, `${key}-${n}`)}</li>)}</List>);
    } else {
      const paragraph = [line];
      i++;
      while (i < lines.length && lines[i].trim() && !/^(```|#{1,6}\s|>|[-*+]\s|\d+[.)]\s)/.test(lines[i].trim()) && !(lines[i].includes("|") && i + 1 < lines.length && isTableDivider(lines[i + 1]))) paragraph.push(lines[i++].trim());
      blocks.push(<p key={key}>{inlineMarkdown(paragraph.join(" "), key)}</p>);
    }
  }
  return <div className="space-y-2.5">{blocks}</div>;
}

function uploadExcel(file: File, onProgress: (value: number) => void): Promise<ExcelWorkbook> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", "/api/excel/upload");
    request.withCredentials = true;
    request.upload.onprogress = (event) => { if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100)); };
    request.onload = () => {
      let body: { detail?: string } & Partial<ExcelWorkbook> = {};
      try { body = JSON.parse(request.responseText || "{}"); } catch {}
      if (request.status >= 200 && request.status < 300) { onProgress(100); resolve(body as ExcelWorkbook); }
      else if (request.status === 401) { location.href = "/login?expired=1"; reject(new Error("Phiên đăng nhập đã hết hạn.")); }
      else reject(new Error(body.detail || "Không tải được file Excel."));
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
  const [workbook, setWorkbook] = useState<ExcelWorkbook | null>(null);
  const [sheet, setSheet] = useState("");
  const [selection, setSelection] = useState("A1");
  const [scope, setScope] = useState<"cell" | "range" | "sheet">("cell");
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadError, setUploadError] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => { messagesEnd.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [messages, busy]);

  function pickFile(candidate: File | undefined) {
    if (!candidate) return;
    setUploadError("");
    const extension = candidate.name.toLowerCase().split(".").pop();
    if (!extension || !["xlsx", "xlsm"].includes(extension)) { setUploadError("Chỉ hỗ trợ Excel .xlsx và .xlsm."); return; }
    if (candidate.size > MAX_FILE_BYTES) { setUploadError("File vượt quá 25 MB."); return; }
    setFile(candidate);
  }

  async function startUpload() {
    if (!file || uploading) return;
    setUploading(true);
    setUploadProgress(0);
    setUploadError("");
    try {
      const result = await uploadExcel(file, setUploadProgress);
      setWorkbook(result);
      setSheet(result.sheets.find((item) => !item.hidden)?.name || result.sheets[0]?.name || "");
      setSelection("A1");
      setScope("cell");
      setMessages([]);
      setQuestion("");
    } catch (reason) { setUploadError(reason instanceof Error ? reason.message : "Không tải được file Excel."); }
    finally { setUploading(false); }
  }

  async function changeFile() {
    if (workbook) { try { await api(`/excel/${workbook.code}`, { method: "DELETE" }); } catch {} }
    setWorkbook(null);
    setSheet("");
    setSelection("A1");
    setMessages([]);
    setQuestion("");
    setError("");
    setUploadError("");
    setFile(null);
    setUploadProgress(0);
    if (fileInput.current) fileInput.current.value = "";
  }

  async function ask(suggestion?: string) {
    if (!workbook || !sheet || busy) return;
    const content = (suggestion ?? question).trim();
    if (!content) { setError("Nhập câu hỏi cần giải thích."); return; }
    const selected = selection.trim().replace(/\$/g, "");
    if (scope !== "sheet" && !/^[A-Za-z]{1,3}\d+(?::[A-Za-z]{1,3}\d+)?$/.test(selected)) {
      setError("Nhập địa chỉ ô như A1 hoặc vùng như A1:C10.");
      return;
    }
    const history: ExcelExplainTurn[] = messages.slice(-6).map((message) => ({ role: message.role, content: message.content.slice(0, 4000) }));
    const shownQuestion = content;
    const location = scope === "sheet" ? `Sheet: ${sheet} · toàn bộ sheet` : `Vị trí: ${sheet}!${selected}`;
    setMessages((current) => [...current, { role: "user", content: `${shownQuestion}\n\n${location}` }]);
    setQuestion("");
    setBusy(true);
    setError("");
    try {
      const result = await api<ExcelExplanation>(`/excel/${workbook.code}/explain`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sheet, scope, selection: scope !== "sheet" ? selected : "", question: content, history }),
      });
      setMessages((current) => [...current, { role: "assistant", content: result.explanation, meta: result.evidence_summary }]);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "Không tạo được giải thích.";
      setError(message);
      setMessages((current) => [...current, { role: "assistant", content: `Không tạo được giải thích: ${message}` }]);
    } finally { setBusy(false); }
  }

  const suggestions = scope === "sheet"
    ? ["Tóm tắt mục đích và cấu trúc sheet này", "Các công thức quan trọng cần kiểm tra là gì?", "Có dấu hiệu bất thường nào không?"]
    : ["Giải thích giá trị hoặc công thức tại vị trí này", "Số liệu này lấy từ đâu?", "Kiểm tra lỗi hoặc giả định tiềm ẩn"];

  return (
    <Shell wide>
      <section className="mb-8">
        <div className="technical-rule mb-6" />
        <p className="eyebrow">Công cụ 03 · Trợ lý Excel</p>
        <h1 className="page-title mt-2">Hỏi đáp<br />Excel</h1>
        <p className="mt-4 max-w-3xl text-base leading-6 text-slate-600">Tải bảng tính để hỏi về số liệu, công thức, ô tham chiếu, cách đối chiếu và các điểm cần kiểm tra.</p>
        <p className="mt-3 max-w-3xl border-l-4 border-brand bg-brand/5 px-4 py-3 text-sm leading-6 text-slate-700">File chỉ được lưu tạm 48 giờ. AI giải thích dựa trên các ô và tham chiếu đã đọc; hãy chọn đúng sheet và vùng cần phân tích.</p>
      </section>

      {!workbook ? (
        <section className="mx-auto max-w-3xl border-2 border-slate-950 bg-white p-6 sm:p-9">
          <p className="eyebrow">Bước 1</p>
          <h2 className="section-title mt-2">Tải file Excel cần hỏi</h2>
          <p className="mt-3 text-sm leading-6 text-slate-600">Hỗ trợ .xlsx và .xlsm, tối đa 25 MB.</p>
          <div className="mt-6 flex min-h-48 flex-col items-center justify-center border-2 border-dashed border-slate-300 bg-slate-50 p-6 text-center" onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); pickFile(event.dataTransfer.files?.[0]); }}>
            <input ref={fileInput} type="file" accept=".xlsx,.xlsm,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel.sheet.macroEnabled.12" className="sr-only" onChange={(event) => pickFile(event.target.files?.[0])} />
            <p className="font-mono text-sm text-slate-500">.xlsx / .xlsm</p>
            <button type="button" onClick={() => fileInput.current?.click()} className="btn mt-4 border-2 border-slate-950 bg-white px-5 py-3 font-semibold text-slate-950 hover:border-brand hover:text-brand">Chọn file từ máy</button>
            <p className="mt-3 text-xs text-slate-500">hoặc kéo thả file vào khung này</p>
          </div>
          {file && <div className="mt-4 flex items-center justify-between gap-4 border border-slate-300 bg-slate-50 px-4 py-3"><div className="min-w-0"><p className="truncate font-semibold text-slate-900">{file.name}</p><p className="text-xs text-slate-500">{fileSize(file.size)}</p></div><button type="button" onClick={() => { setFile(null); if (fileInput.current) fileInput.current.value = ""; }} className="text-sm font-semibold text-slate-500 hover:text-slate-950">Bỏ chọn</button></div>}
          {uploading && <div className="mt-4"><div className="h-2 w-full bg-slate-200"><div className="h-2 bg-brand transition-all" style={{ width: `${uploadProgress}%` }} /></div><p className="mt-2 text-xs text-slate-500">Đang tải và kiểm tra file... {uploadProgress}%</p></div>}
          {uploadError && <p className="mt-4 border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800">{uploadError}</p>}
          <button type="button" onClick={startUpload} disabled={!file || uploading} className="btn btn-primary mt-6 w-full px-5 py-4 text-base font-semibold disabled:cursor-not-allowed disabled:opacity-40">{uploading ? "Đang xử lý..." : "Tải file lên →"}</button>
        </section>
      ) : (
        <div className="mx-auto max-w-4xl space-y-4">
          <section className="flex flex-wrap items-center justify-between gap-4 border-2 border-slate-950 bg-white p-4 sm:p-5">
            <div className="min-w-0"><p className="eyebrow">File Excel đang mở</p><h2 className="mt-1 truncate text-lg font-bold text-slate-950">{workbook.filename}</h2><p className="mt-1 text-xs text-slate-500">{fileSize(workbook.file_size)} · lưu tạm 48 giờ</p></div>
            <button type="button" onClick={changeFile} className="border border-slate-400 bg-white px-4 py-2 text-sm font-semibold text-slate-700 hover:border-slate-950 hover:text-slate-950">Đổi file</button>
          </section>

          <section className="space-y-4 border-2 border-slate-950 bg-white p-5 sm:p-7">
            <div><p className="eyebrow">Bước 2</p><h2 className="section-title mt-2">Chỉ vị trí cần hỏi</h2></div>
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block text-sm font-semibold text-slate-700">Sheet
                <select value={sheet} onChange={(event) => setSheet(event.target.value)} className="mt-2 w-full border border-slate-400 bg-white px-3 py-3 text-sm font-normal text-slate-900 outline-none focus:border-brand">
                  {workbook.sheets.map((item) => <option key={item.name} value={item.name}>{item.name}{item.hidden ? " · ẩn" : ""}</option>)}
                </select>
              </label>
              <label className="block text-sm font-semibold text-slate-700">Địa chỉ ô hoặc vùng
                <input value={selection} onChange={(event) => setSelection(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); ask(); } }} disabled={scope === "sheet"} placeholder="Ví dụ: W170 hoặc W170:W201" className="mt-2 w-full border border-slate-400 bg-white px-3 py-3 font-mono text-sm text-slate-900 outline-none focus:border-brand disabled:bg-slate-100" />
              </label>
            </div>
            <label className="flex items-center gap-2 text-sm text-slate-700"><input type="checkbox" checked={scope === "sheet"} onChange={(event) => setScope(event.target.checked ? "sheet" : "cell")} className="accent-brand" /> Hỏi về toàn bộ sheet</label>
            <p className="text-xs leading-5 text-slate-500">Địa chỉ nhập theo dạng A1 hoặc A1:C10. Có thể chọn một ô, một vùng liên tục hoặc toàn sheet.</p>
          </section>

          <section className="border-2 border-slate-950 bg-white">
            <div className="border-b border-slate-300 bg-slate-50 p-4"><p className="eyebrow">Bước 3</p><h2 className="section-title mt-2">Đặt câu hỏi</h2><p className="mt-2 text-xs leading-5 text-slate-500">AI đọc dữ liệu, công thức và ô được tham chiếu từ vị trí bạn nhập.</p></div>
            <div className="max-h-[48vh] space-y-4 overflow-y-auto p-4">
              {messages.length === 0 && !busy ? <div><p className="text-sm leading-6 text-slate-600">Gợi ý câu hỏi cho {scope === "sheet" ? `sheet “${sheet}”` : `${sheet}!${selection || "A1"}`}:</p><div className="mt-3 space-y-2">{suggestions.map((suggestion) => <button key={suggestion} type="button" onClick={() => setQuestion(suggestion)} className="block w-full border border-slate-300 bg-white px-3 py-2 text-left text-xs font-semibold text-slate-700 hover:border-brand hover:text-brand">{suggestion}</button>)}</div></div> : messages.map((message, index) => <div key={index} className={message.role === "user" ? "ml-6 border border-slate-300 bg-slate-100 p-3" : "mr-1 border-l-4 border-brand bg-brand/5 p-3"}><p className="mb-1 text-[10px] font-bold uppercase tracking-widest text-slate-400">{message.role === "user" ? "Bạn hỏi" : "SBTech AI"}</p>{message.role === "assistant" ? <div className="text-sm leading-6 text-slate-800"><AnswerText text={message.content} /></div> : <p className="whitespace-pre-wrap break-words text-sm leading-6 text-slate-800">{message.content}</p>}{message.meta && <p className="mt-2 border-t border-slate-200 pt-2 text-[10px] text-slate-400">{message.meta}</p>}</div>)}
              {busy && <div className="border-l-4 border-brand bg-brand/5 p-3 text-sm text-slate-600">AI đang phân tích dữ liệu, công thức và ô tham chiếu...</div>}<div ref={messagesEnd} />
            </div>
            <div className="border-t border-slate-300 p-3">
              <textarea value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); ask(); } }} rows={4} maxLength={2000} placeholder="Hỏi về công thức, số liệu, ô tham chiếu hoặc cách kiểm tra..." className="w-full resize-y border border-slate-400 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-brand" />
              <div className="mt-3 flex flex-wrap items-end justify-between gap-3">
                <span className="text-xs text-slate-500">Enter để xuống dòng · Ctrl+Enter để gửi</span>
                <button type="button" onClick={() => ask()} disabled={busy} className="btn btn-primary shrink-0 px-4 py-2 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-40">Gửi câu hỏi →</button>
              </div>
            </div>
          </section>
          {error && <p className="border border-red-300 bg-red-50 px-4 py-3 text-sm leading-5 text-red-800">{error}</p>}
          <p className="text-[11px] leading-5 text-slate-500">Câu trả lời dựa trên vùng dữ liệu đã đọc, có thể bỏ sót nội dung ngoài vùng được chọn. Hãy đối chiếu lại với workbook trước khi sử dụng.</p>
        </div>
      )}
    </Shell>
  );
}
