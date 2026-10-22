"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { api } from "@/lib/api";
import { User } from "@/types";

export default function ToolPicker() {
  const [user, setUser] = useState<User>();
  useEffect(() => { let active = true; api<User>("/auth/me").then(u => { if (active) setUser(u); }).catch(() => {}); return () => { active = false; }; }, []);
  const boqHref = user?.role === "ADMIN" ? "/admin" : "/";
  const cardClass = "focus-ring group flex flex-col justify-between border-2 border-slate-950 bg-white p-7 transition-colors hover:border-brand sm:p-9";
  const ctaClass = "mt-8 inline-flex items-center gap-3 text-base font-semibold text-brand";
  return <Shell>
    <section className="mb-10"><div className="technical-rule mb-6" /><p className="eyebrow">Cổng công cụ</p><h1 className="page-title mt-2">Chọn<br />công cụ</h1><p className="mt-4 max-w-xl text-base leading-6 text-slate-600">Ba công cụ dùng chung một phiên đăng nhập. Mọi kết quả đều là bản nháp kèm bằng chứng và cần người có chuyên môn rà soát trước khi sử dụng.</p></section>
    <section aria-label="Danh sách công cụ" className="grid gap-6 lg:grid-cols-3">
      <Link href={boqHref} className={cardClass}>
        <div>
          <p className="eyebrow">Công cụ 01</p>
          <h2 className="section-title mt-3">Rà soát BOQ</h2>
          <p className="mt-4 max-w-md text-base leading-6 text-slate-600">Gửi bản vẽ PDF, dự toán và thuyết minh; theo dõi tiến độ và nhận báo cáo đối chiếu có đánh dấu vị trí cùng bằng chứng.</p>
        </div>
        <span className={ctaClass}>Mở không gian rà soát <span aria-hidden="true" className="transition-transform group-hover:translate-x-1">→</span></span>
      </Link>
      <a href="/cad/" className={cardClass}>
        <div>
          <p className="eyebrow">Công cụ 02 · Alpha nội bộ</p>
          <h2 className="section-title mt-3">Trợ giúp vẽ CAD</h2>
          <p className="mt-4 max-w-md text-base leading-6 text-slate-600">Chuyển mô tả, bản phác thảo hoặc trang PDF thành bản vẽ DXF nháp; hệ thống dừng lại hỏi khi thiếu kích thước thay vì tự suy đoán.</p>
          <p className="mt-4 border border-amber-300 bg-amber-50 p-3 text-sm leading-5 text-amber-900">Bản vẽ là bản nháp kỹ thuật: phải mở và kiểm tra bằng phần mềm CAD, đối chiếu nguồn trước khi dùng cho công việc thực tế.</p>
        </div>
        <span className={ctaClass}>Mở CAD Studio <span aria-hidden="true" className="transition-transform group-hover:translate-x-1">→</span></span>
      </a>
      <Link href="/excel" className={cardClass}>
        <div>
          <p className="eyebrow">Công cụ 03 · Beta</p>
          <h2 className="section-title mt-3">Hỏi đáp và phân tích Excel</h2>
          <p className="mt-4 max-w-md text-base leading-6 text-slate-600">Tải bảng tính và hỏi về công thức, số liệu, ô tham chiếu, cách đối chiếu và các điểm cần kiểm tra.</p>
          <p className="mt-4 border border-slate-300 bg-slate-50 p-3 text-sm leading-5 text-slate-700">Chọn sheet và vùng ô để AI tập trung giải thích đúng phần bạn cần.</p>
        </div>
        <span className={ctaClass}>Mở chat tài liệu <span aria-hidden="true" className="transition-transform group-hover:translate-x-1">→</span></span>
      </Link>
    </section>
  </Shell>;
}
