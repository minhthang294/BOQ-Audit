"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { CompletionNotifications } from "@/components/CompletionNotifications";

export function Shell({ children, admin = false, wide = false, sidebarChat }: { children: React.ReactNode; admin?: boolean; wide?: boolean; sidebarChat?: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [mobileChatOpen, setMobileChatOpen] = useState(false);
  const chatTrigger = useRef<HTMLButtonElement>(null);
  const chatClose = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!mobileChatOpen) return;
    chatClose.current?.focus();
    const desktop = window.matchMedia("(min-width: 1024px)");
    const closeOnDesktop = () => { if (desktop.matches) { setMobileChatOpen(false); chatClose.current?.closest("aside")?.querySelector<HTMLAnchorElement>("a[href]")?.focus(); } };
    desktop.addEventListener("change", closeOnDesktop);
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setMobileChatOpen(false); chatTrigger.current?.focus(); }
      if (event.key === "Tab") {
        const items = chatClose.current?.closest("aside")?.querySelectorAll<HTMLElement>('a[href], button:not([disabled]), textarea:not([disabled])');
        if (!items?.length) return;
        if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items[items.length - 1].focus(); }
        else if (!event.shiftKey && document.activeElement === items[items.length - 1]) { event.preventDefault(); items[0].focus(); }
      }
    };
    window.addEventListener("keydown", onKeyDown);
    closeOnDesktop();
    return () => { window.removeEventListener("keydown", onKeyDown); desktop.removeEventListener("change", closeOnDesktop); };
  }, [mobileChatOpen]);

  async function logout() { await api("/auth/logout", { method: "POST" }); router.push("/login"); router.refresh(); }
  function closeMobileChat() { setMobileChatOpen(false); chatTrigger.current?.focus(); }
  const navClass = (active: boolean) => `focus-ring flex min-h-11 items-center rounded-md border-l-[3px] px-4 text-sm font-semibold transition-colors duration-200 ${active ? "border-signal bg-white/10 text-white" : "border-transparent text-slate-300 hover:border-brand hover:bg-white/5 hover:text-white"}`;
  const home = admin ? "/admin" : "/";

  return <div className="min-h-screen lg:grid lg:grid-cols-[296px_minmax(0,1fr)] 2xl:grid-cols-[320px_minmax(0,1fr)]">
    <aside id="sidebar-chat-menu" aria-label="Thanh bên" role={mobileChatOpen ? "dialog" : undefined} aria-modal={mobileChatOpen ? "true" : undefined} className={`blueprint-sidebar fixed inset-y-0 left-0 z-50 flex h-dvh w-[min(88vw,320px)] flex-col overflow-hidden bg-night text-white shadow-2xl transition-[transform,visibility] duration-300 lg:sticky lg:top-0 lg:bottom-auto lg:left-auto lg:z-auto lg:h-screen lg:w-auto lg:translate-x-0 lg:shadow-none ${mobileChatOpen ? "visible translate-x-0" : "invisible -translate-x-full lg:visible"}`}>
      <div className="mx-5 mt-5 flex items-center gap-2 border-b border-white/15 pb-5">
        <Link href={home} onClick={() => setMobileChatOpen(false)} className="focus-ring flex min-w-0 flex-1 items-center gap-3">
          <Image src="/sbtech-logo.png" alt="SBTech" width={60} height={40} priority className="h-10 w-[60px] shrink-0 rounded-md bg-white object-contain p-1" />
          <span className="min-w-0"><span className="display-face block text-xl font-bold uppercase leading-none tracking-wide">{admin ? "BOQ Admin" : "BOQ Audit"}</span><span className="mt-1 block text-[10px] uppercase tracking-[0.22em] text-slate-400">SBTech Portal</span></span>
        </Link>
        <button ref={chatClose} type="button" onClick={closeMobileChat} aria-label="Đóng thanh bên" className="focus-ring flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-white/20 text-xl text-white lg:hidden">×</button>
      </div>
      <nav aria-label="Điều hướng chính" className="mt-5 space-y-1 px-3">{admin ? <><Link href="/admin" className={navClass(pathname === "/admin" || pathname.startsWith("/admin/jobs"))}>Hồ sơ kiểm toán</Link><Link href="/admin/users" className={navClass(pathname.startsWith("/admin/users"))}>Tài khoản</Link></> : <Link href="/" className={navClass(pathname === "/" || pathname.startsWith("/jobs"))}>Hồ sơ của tôi</Link>}</nav>
      <div className="min-h-0 flex-1 px-3 pb-3 pt-5">{sidebarChat}</div>
      <div className="border-t border-white/10 p-4">{!admin && <CompletionNotifications />}<button onClick={logout} className="mt-1 flex min-h-11 w-full items-center px-4 text-sm font-semibold text-slate-300 transition-colors hover:text-white">Đăng xuất <span className="ml-auto" aria-hidden="true">↗</span></button></div>
    </aside>
    {mobileChatOpen && <button type="button" onClick={closeMobileChat} aria-label="Đóng thanh trò chuyện" className="fixed inset-0 z-[45] bg-night/60 lg:hidden" />}
    <div className="min-w-0">
      <header className="blueprint-sidebar sticky top-0 z-40 border-b border-slate-300 bg-night text-white lg:hidden"><div className="flex min-h-16 items-center justify-between gap-2 px-4"><Link href={home} className="focus-ring flex min-h-11 min-w-0 items-center gap-2"><Image src="/sbtech-logo.png" alt="SBTech" width={48} height={32} priority className="h-8 w-12 shrink-0 rounded bg-white object-contain p-0.5" /><span className="display-face truncate text-lg font-bold uppercase tracking-wide">{admin ? "BOQ Admin" : "BOQ Audit"}</span></Link><nav aria-label="Điều hướng chính" className="flex shrink-0 items-center gap-1">{sidebarChat && <button ref={chatTrigger} type="button" onClick={() => setMobileChatOpen(true)} aria-expanded={mobileChatOpen} aria-controls="sidebar-chat-menu" className="focus-ring flex min-h-11 items-center gap-1 rounded-md px-2 text-sm font-semibold text-white"><svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-4 w-4"><path d="M4 5h16v11H8l-4 3V5Z" strokeLinecap="round" strokeLinejoin="round" /></svg>Chat</button>}{admin && <Link href="/admin/users" className="min-h-11 px-2 py-3 text-sm font-semibold">Tài khoản</Link>}<button onClick={logout} className="min-h-11 px-2 text-sm font-semibold text-slate-300">Thoát</button></nav></div></header>
      <main className={`app-main mx-auto px-4 py-7 sm:px-8 sm:py-10 ${wide ? "max-w-[1560px]" : "max-w-7xl"}`}>{children}</main>
    </div>
  </div>;
}
