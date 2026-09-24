"use client";

import { MouseEvent, useRef, useState } from "react";

type RichTextEditorProps = {
  initialHtml?: string | null;
  name: string;
};

const tools = [
  { command: "bold", label: "B", title: "In đậm", className: "font-bold" },
  { command: "italic", label: "I", title: "In nghiêng", className: "italic" },
  { command: "underline", label: "U", title: "Gạch chân", className: "underline" },
  { command: "insertUnorderedList", label: "• Danh sách", title: "Danh sách dấu chấm", className: "" },
  { command: "insertOrderedList", label: "1. Danh sách", title: "Danh sách đánh số", className: "" },
  { command: "removeFormat", label: "Xóa định dạng", title: "Xóa định dạng", className: "" },
] as const;

export function RichTextEditor({ initialHtml, name }: RichTextEditorProps) {
  const editorRef = useRef<HTMLDivElement>(null);
  const [html, setHtml] = useState(initialHtml || "");

  function applyFormat(event: MouseEvent<HTMLButtonElement>, command: string) {
    event.preventDefault();
    editorRef.current?.focus();
    document.execCommand(command, false);
    setHtml(editorRef.current?.innerHTML || "");
  }

  return <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm focus-within:border-cyan-700 focus-within:ring-4 focus-within:ring-cyan-100/70">
    <div className="flex flex-wrap gap-1 border-b border-slate-200 bg-slate-50 p-2" role="toolbar" aria-label="Định dạng ghi chú">
      {tools.map(tool => <button
        key={tool.command}
        type="button"
        title={tool.title}
        aria-label={tool.title}
        onMouseDown={event => event.preventDefault()}
        onClick={event => applyFormat(event, tool.command)}
        className={`rounded-lg border border-transparent px-2.5 py-1.5 text-xs text-slate-700 transition hover:border-slate-200 hover:bg-white ${tool.className}`}
      >{tool.label}</button>)}
    </div>
    <div
      ref={editorRef}
      id={name}
      contentEditable
      suppressContentEditableWarning
      role="textbox"
      aria-multiline="true"
      className="rich-text-content min-h-32 px-3.5 py-3 text-sm outline-none"
      dangerouslySetInnerHTML={{ __html: initialHtml || "" }}
      onInput={event => setHtml(event.currentTarget.innerHTML)}
    />
    <input type="hidden" name={name} value={html} readOnly />
  </div>;
}
