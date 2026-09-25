"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Job, User } from "@/types";

const POLL_MS = 30000;

export function CompletionNotifications() {
  const [supported, setSupported] = useState(false);
  const [permission, setPermission] = useState<NotificationPermission>("default");
  const [userId, setUserId] = useState<number | null>(null);

  useEffect(() => {
    if (!("Notification" in window) || !window.isSecureContext) return;
    setSupported(true);
    setPermission(Notification.permission);
    api<User>("/auth/me").then(user => {
      if (user.role === "CUSTOMER") {
        if (Notification.permission === "granted") {
          const key = `boq-notifications-enabled:${user.id}`;
          if (!localStorage.getItem(key)) localStorage.setItem(key, String(Date.now()));
        }
        setUserId(user.id);
      }
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (userId === null || permission !== "granted") return;
    let active = true;
    const enabledKey = `boq-notifications-enabled:${userId}`;
    const enabledAt = Number(localStorage.getItem(enabledKey));
    if (!enabledAt) return;
    const poll = async () => {
      try {
        const { items } = await api<{ items: Job[] }>("/jobs");
        if (!active) return;
        for (const job of items) {
          if (job.status !== "COMPLETED" || !job.completed_at) continue;
          const completedAt = new Date(/(?:Z|[+-]\d\d:\d\d)$/.test(job.completed_at) ? job.completed_at : `${job.completed_at}Z`).getTime();
          if (!Number.isFinite(completedAt) || completedAt < enabledAt) continue;
          const seenKey = `boq-notification-seen:${userId}:${job.job_code}`;
          if (localStorage.getItem(seenKey) === job.completed_at) continue;
          localStorage.setItem(seenKey, job.completed_at);
          const notice = new Notification("Hồ sơ đã hoàn thành", {
            body: `${job.project_name} (${job.job_code}) đã có kết quả.`,
            tag: `boq-completed-${userId}-${job.job_code}`,
          });
          notice.onclick = () => {
            window.focus();
            window.location.href = `/jobs/${encodeURIComponent(job.job_code)}`;
            notice.close();
          };
        }
      } catch {
        // Keep polling; temporary network failures should not consume notifications.
      }
    };
    void poll();
    const timer = window.setInterval(() => { void poll(); }, POLL_MS);
    return () => { active = false; window.clearInterval(timer); };
  }, [permission, userId]);

  if (!supported || userId === null || permission !== "default") return null;
  async function enable() {
    const result = await Notification.requestPermission();
    setPermission(result);
    if (result === "granted" && userId !== null) {
      localStorage.setItem(`boq-notifications-enabled:${userId}`, String(Date.now()));
    }
  }
  return <button type="button" onClick={enable} className="rounded-lg px-3 py-2 text-sm font-semibold text-brand transition hover:bg-cyan-50">Bật thông báo</button>;
}
