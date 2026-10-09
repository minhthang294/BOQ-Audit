"use client";

import Image from "next/image";
import { FormEvent, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { ChatSnapshot } from "@/types";

export function ProjectChat({ jobCode, version }: { jobCode: string; version: string }) {
  const [visible, setVisible] = useState(false);
  const [chat, setChat] = useState<ChatSnapshot>();
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const panel = useRef<HTMLElement>(null);
  const transcript = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const pollDelay = chat?.enabled === false ? 0 : chat?.busy ? 5000 : 30_000;

  useEffect(() => {
    if (!panel.current) return;
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting));
    observer.observe(panel.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!visible) return;
    let active = true;
    const load = () => api<ChatSnapshot>(`/jobs/${jobCode}/chat`)
      .then(value => { if (active) { setChat(value); setError(""); } })
      .catch(() => { if (active) setError("Chưa tải được hội thoại. Vui lòng thử lại."); });
    void load();
    const timer = pollDelay ? window.setInterval(load, pollDelay) : undefined;
    return () => { active = false; if (timer) window.clearInterval(timer); };
  }, [jobCode, version, visible, pollDelay]);

  useEffect(() => {
    if (stickToBottom.current && transcript.current) transcript.current.scrollTop = transcript.current.scrollHeight;
  }, [chat?.messages]);

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!chat?.enabled || chat.busy || !message.trim() || sending) return;
    setSending(true); setError("");
    const question = message;
    try {
      setChat(await api<ChatSnapshot>(`/jobs/${jobCode}/chat/messages`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: question }) }));
      setMessage("");
      stickToBottom.current = true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "AI chưa thể trả lời.");
      try { setChat(await api<ChatSnapshot>(`/jobs/${jobCode}/chat`)); } catch {}
    } finally { setSending(false); }
  }

  return <section ref={panel} className="flex h-full min-h-0 flex-col overflow-hidden rounded-xl border border-white/15 bg-white/5" aria-label="Trò chuyện với SBTech AI">
    <header className="flex items-center gap-2.5 border-b border-white/10 px-3 py-3">
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-white" aria-hidden="true"><Image src="/sbtech-logo.png" alt="" width={30} height={20} className="h-auto object-contain" /></span>
      <div className="min-w-0 flex-1"><h2 className="text-sm font-bold text-white">SBTech AI</h2><p className="truncate text-[11px] text-slate-300">Trợ lý hồ sơ {jobCode}</p></div>
      <span className="h-2 w-2 shrink-0 rounded-full bg-signal" aria-hidden="true" />
    </header>

    <div ref={transcript} role="log" aria-label="Hội thoại với SBTech AI" aria-live="polite" aria-relevant="additions" className="chat-transcript min-h-0 flex-1 space-y-3 overflow-y-auto p-3" onScroll={() => { const node = transcript.current; if (node) stickToBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight < 56; }}>
      {!chat && <p className="text-sm text-slate-600">Đang tải hội thoại…</p>}
      {chat?.messages.length === 0 && <div className="flex items-start gap-2">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-white shadow-sm" aria-hidden="true"><Image src="/sbtech-logo.png" alt="" width={24} height={16} className="object-contain" /></span>
        <p className="max-w-[85%] rounded-2xl rounded-tl-sm border border-line bg-white px-3 py-2.5 text-sm leading-5 text-ink shadow-sm">{chat.enabled ? "Chào bạn! Hỏi tôi về hồ sơ, tiến độ hoặc kết quả rà soát nhé." : "Trợ lý AI đang tạm ngừng. Lịch sử hội thoại vẫn có thể xem tại đây."}</p>
      </div>}
      {chat?.messages.map(item => <div key={item.id} className={`flex items-end gap-2 ${item.role === "user" ? "justify-end" : "justify-start"}`}>
        {item.role !== "user" && <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-white shadow-sm" aria-hidden="true"><Image src="/sbtech-logo.png" alt="" width={24} height={16} className="object-contain" /></span>}
        <div className={`max-w-[85%] min-w-0 rounded-2xl px-3 py-2.5 text-sm leading-5 shadow-sm ${item.role === "user" ? "rounded-tr-sm bg-brand text-white" : "rounded-tl-sm border border-line bg-white text-ink"}`}>
          <p className={`mb-1 text-[10px] font-bold uppercase tracking-wide ${item.role === "user" ? "text-white/75" : "text-brand-strong"}`}>{item.role === "user" ? "Bạn" : "SBTech AI"}</p>
          <p className="whitespace-pre-wrap break-words">{item.content}</p>
          {item.status !== "COMPLETED" && <p className={`mt-2 text-xs ${item.role === "user" ? "text-white/80" : "text-slate-500"}`}>{item.status === "PENDING" ? "AI đang trả lời…" : "Chưa nhận được câu trả lời. Bạn có thể gửi lại."}</p>}
        </div>
        {item.role === "user" && <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-signal text-xs font-bold text-white" aria-hidden="true">B</span>}
      </div>)}
    </div>

    {error && <p role="alert" className="border-t border-white/10 px-3 py-2 text-xs text-red-200">{error}</p>}
    <form onSubmit={send} className="border-t border-white/10 p-2.5">
      <label htmlFor="chat-message" className="sr-only">Câu hỏi của bạn</label>
      <div className="flex items-end gap-1.5 rounded-xl bg-white p-1.5 shadow-sm">
        <textarea id="chat-message" value={message} onChange={e => setMessage(e.target.value)} maxLength={2000} rows={2} required placeholder={chat?.enabled ? "Hỏi về hồ sơ này…" : "AI hiện chưa sẵn sàng"} disabled={!chat?.enabled || sending || chat.busy} className="!min-h-12 !resize-none !border-0 !bg-transparent !px-2 !py-1.5 !text-sm !shadow-none !ring-0" />
        <button type="submit" aria-label="Gửi câu hỏi" title="Gửi câu hỏi" disabled={!chat?.enabled || sending || chat.busy || !message.trim()} className="focus-ring flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-brand text-white transition-colors hover:bg-brand-strong disabled:cursor-not-allowed disabled:bg-slate-300"><svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-5 w-5"><path d="m5 12 7-7 7 7M12 5v14" strokeLinecap="round" strokeLinejoin="round" /></svg></button>
      </div>
      <p className="mt-2 text-[11px] leading-4 text-slate-300">{chat?.busy || sending ? "AI đang trả lời…" : "Nội dung trao đổi chỉ thuộc hồ sơ này."}</p>
    </form>
  </section>;
}
