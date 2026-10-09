"use client";
import { FormEvent, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { ChatSnapshot } from "@/types";

export function ProjectChat({ jobCode, version }: { jobCode: string; version: string }) {
  const [open, setOpen] = useState(false);
  const [chat, setChat] = useState<ChatSnapshot>();
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const transcript = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    let active = true;
    const load = () => api<ChatSnapshot>(`/jobs/${jobCode}/chat`).then(value => { if (active) setChat(value); }).catch(() => { if (active) setError("Chưa tải được hội thoại. Vui lòng thử lại."); });
    void load();
    const timer = window.setInterval(load, 5000);
    return () => { active = false; window.clearInterval(timer); };
  }, [jobCode, version, open]);
  useEffect(() => { if (transcript.current) transcript.current.scrollTop = transcript.current.scrollHeight; }, [chat?.messages]);
  async function send(event: FormEvent) {
    event.preventDefault();
    if (!message.trim() || sending) return;
    setSending(true); setError("");
    const question = message;
    try {
      setChat(await api<ChatSnapshot>(`/jobs/${jobCode}/chat/messages`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: question }) }));
      setMessage("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "AI chưa thể trả lời.");
      try { setChat(await api<ChatSnapshot>(`/jobs/${jobCode}/chat`)); } catch {}
    } finally { setSending(false); }
  }
  return <section className="card">
    <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} aria-controls="project-chat-panel" className="focus-ring display-face flex min-h-11 w-full items-center justify-between text-left text-xl font-bold uppercase">SBTech AI <span aria-hidden="true">{open ? "−" : "+"}</span></button>
    {open && <div id="project-chat-panel">
      <p className="mt-1 text-xs leading-5 text-slate-500">Trợ lý AI được tùy chỉnh cho rà soát BOQ. Hỏi về hồ sơ, tiến độ hoặc kết quả đã phát hành.</p>
      <div ref={transcript} role="log" aria-label="Hội thoại với SBTech AI" aria-live="polite" aria-relevant="additions" className="mt-3 max-h-80 space-y-3 overflow-y-auto border border-slate-300 bg-slate-100 p-3">
        {!chat && <p className="text-sm text-slate-500">Đang tải hội thoại…</p>}
        {chat?.messages.length === 0 && <p className="text-sm text-slate-500">Bạn muốn hỏi gì về hồ sơ này?</p>}
        {chat?.messages.map(item => <div key={item.id} className={`border p-3 text-sm ${item.role === "user" ? "border-brand bg-brand-soft" : "border-slate-300 bg-white"}`}><p className="mb-1 text-xs font-bold uppercase tracking-wide text-slate-500">{item.role === "user" ? "Bạn" : "SBTech AI"}</p><p className="whitespace-pre-wrap break-words">{item.content}</p>{item.status !== "COMPLETED" && <p className="mt-2 text-xs text-slate-500">{item.status === "PENDING" ? "AI đang trả lời…" : "Chưa nhận được câu trả lời. Bạn có thể gửi lại."}</p>}</div>)}
      </div>
      {chat && !chat.enabled && <p className="mt-3 text-sm text-amber-700">SBTech AI chưa được cấu hình.</p>}
      {error && <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>}
      <form onSubmit={send} className="mt-3 space-y-2">
        <label htmlFor="chat-message">Câu hỏi của bạn</label>
        <textarea id="chat-message" value={message} onChange={e => setMessage(e.target.value)} maxLength={2000} rows={3} required placeholder="Giải thích kết quả rà soát…" disabled={sending || chat?.busy} />
        <button className="btn-primary w-full" disabled={!chat?.enabled || sending || chat.busy || !message.trim()}>{sending || chat?.busy ? "ĐANG TRẢ LỜI…" : "GỬI CÂU HỎI"}</button>
      </form>
    </div>}
  </section>;
}
