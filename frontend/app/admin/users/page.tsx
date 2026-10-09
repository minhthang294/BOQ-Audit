"use client";
import { FormEvent, useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { api } from "@/lib/api";
import { AdminUser } from "@/types";

const emptyForm = { name: "", username: "", password: "", is_active: true };

export default function AdminUsers() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [editing, setEditing] = useState<number | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  async function load() { setUsers(await api<AdminUser[]>("/admin/users")); }
  useEffect(() => { load().catch(e => setError(e.message)).finally(() => setLoading(false)); }, []);
  function reset() { setEditing(null); setForm(emptyForm); }
  function edit(user: AdminUser) {
    setEditing(user.id); setForm({ name: user.name, username: user.username, password: "", is_active: user.is_active });
    setError(""); setMessage("");
    document.getElementById("account-form")?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  }
  async function save(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError(""); setMessage("");
    try {
      const payload = { name: form.name.trim(), username: form.username.trim().toLowerCase(), is_active: form.is_active,
        ...(form.password ? { password: form.password } : {}) };
      await api(`/admin/users${editing === null ? "" : `/${editing}`}`, {
        method: editing === null ? "POST" : "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      reset(); setMessage("Đã lưu tài khoản."); await load();
    } catch (e) { setError(e instanceof Error ? e.message : "Không thể lưu tài khoản."); }
    finally { setBusy(false); }
  }
  async function remove(user: AdminUser) {
    if (!window.confirm(`Xóa vĩnh viễn tài khoản “${user.username}”?`)) return;
    setBusy(true); setError(""); setMessage("");
    try {
      await api(`/admin/users/${user.id}`, { method: "DELETE" });
      if (editing === user.id) reset();
      setMessage("Đã xóa tài khoản."); await load();
    } catch (e) { setError(e instanceof Error ? e.message : "Không thể xóa tài khoản."); }
    finally { setBusy(false); }
  }
  const visible = users.filter(user => `${user.name} ${user.username}`.toLocaleLowerCase("vi").includes(query.toLocaleLowerCase("vi")));
  return <Shell admin>
    <div className="technical-rule mb-6" /><p className="eyebrow">Quản trị</p><h1 className="page-title mt-2">Tài khoản<br />người dùng</h1>
    <p className="mt-2 text-sm text-slate-500">Admin tạo và cấp mật khẩu cho khách hàng. Khóa tài khoản để ngừng truy cập và giữ hồ sơ.</p>
    {error && <p role="alert" className="error mt-4">{error}</p>}
    {message && <p role="status" className="mt-4 text-sm font-semibold text-emerald-700">{message}</p>}
    <form id="account-form" onSubmit={save} className="card mt-6 scroll-mt-28">
      <h2 className="text-lg font-bold">{editing === null ? "Thêm tài khoản" : "Sửa tài khoản"}</h2>
      <fieldset disabled={busy} className="mt-4 grid gap-4 sm:grid-cols-2">
        <div><label htmlFor="user-name">Tên người dùng</label><input id="user-name" required maxLength={160} value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></div>
        <div><label htmlFor="user-username">Tên đăng nhập</label><input id="user-username" required minLength={3} maxLength={80} pattern={"[^\\s]+"} autoCapitalize="none" autoComplete="off" value={form.username} onChange={e => setForm({ ...form, username: e.target.value })} /></div>
        <div><label htmlFor="user-password">{editing === null ? "Mật khẩu do admin đặt" : "Mật khẩu mới (để trống để giữ mật khẩu)"}</label><input id="user-password" type="password" autoComplete="new-password" required={editing === null} minLength={12} maxLength={200} value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} /><p className="mt-1 text-xs text-slate-500">Tối thiểu 12 ký tự. Mật khẩu đã lưu không thể xem lại.</p></div>
        <label className="flex items-center gap-2 self-center"><input type="checkbox" className="!w-auto" checked={form.is_active} onChange={e => setForm({ ...form, is_active: e.target.checked })} />Cho phép đăng nhập</label>
        <div className="flex gap-3 sm:col-span-2"><button className="btn-primary">{busy ? "Đang xử lý…" : editing === null ? "Tạo tài khoản" : "Lưu thay đổi"}</button>{editing !== null && <button type="button" onClick={reset} className="min-h-11 px-4 py-2 font-semibold text-slate-600">Hủy sửa</button>}</div>
      </fieldset>
    </form>
    <div className="mt-6"><label htmlFor="user-search">Tìm theo tên hoặc tên đăng nhập</label><input id="user-search" value={query} onChange={e => setQuery(e.target.value)} /></div>
    <div className="mt-4 overflow-x-auto border-t-2 border-slate-950 bg-white/50">
      <table className="w-full text-left text-sm"><caption className="sr-only">Danh sách tài khoản khách hàng</caption>
        <thead className="bg-slate-50 text-slate-500"><tr>{["Người dùng", "Tên đăng nhập", "Trạng thái", "Hồ sơ", "Thao tác"].map(label => <th key={label} scope="col" className="px-4 py-3">{label}</th>)}</tr></thead>
        <tbody>{visible.map(user => <tr key={user.id} className="border-t border-slate-100">
          <td className="px-4 py-4 font-semibold">{user.name}</td><td className="px-4 py-4">{user.username}</td>
          <td className="px-4 py-4">{user.is_active ? "Đang hoạt động" : "Đã khóa"}</td><td className="px-4 py-4">{user.job_count}</td>
          <td className="px-4 py-2"><div className="flex gap-2"><button disabled={busy} type="button" onClick={() => edit(user)} className="min-h-11 px-2 font-semibold text-brand disabled:opacity-50">Sửa<span className="sr-only"> {user.username}</span></button><button disabled={busy || user.job_count > 0} title={user.job_count > 0 ? "Đã có hồ sơ: sửa và bỏ chọn Cho phép đăng nhập để khóa." : "Xóa tài khoản"} type="button" onClick={() => remove(user)} className="min-h-11 px-2 font-semibold text-red-600 disabled:opacity-40">Xóa<span className="sr-only"> {user.username}</span></button></div></td>
        </tr>)}</tbody>
      </table>
      {visible.length === 0 && <p className="p-6 text-center text-slate-500">{loading ? "Đang tải…" : "Không có tài khoản phù hợp."}</p>}
    </div>
  </Shell>;
}
