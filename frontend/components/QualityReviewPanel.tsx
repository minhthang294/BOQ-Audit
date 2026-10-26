import { AdminJobQuality, AttemptArtifacts } from "@/types";

function statusLabel(status: AdminJobQuality["freshness_status"]) {
  if (status === "INVALIDATED") return "Đã vô hiệu hóa sau thay đổi";
  if (status === "NOT_VERIFIED") return "Chưa xác minh còn mới";
  return "Chưa có quality review";
}

export function QualityReviewPanel({
  jobCode,
  quality,
  history,
  loading,
  error,
}: {
  jobCode: string;
  quality?: AdminJobQuality;
  history?: AttemptArtifacts;
  loading: boolean;
  error: string;
}) {
  const run = quality?.attempt == null ? undefined : history?.attempts.find((item) => item.attempt === quality.attempt);

  return (
    <section className="card" aria-labelledby="quality-review-heading">
      <h2 id="quality-review-heading" className="font-bold">Quality review</h2>
      <p className="mt-1 text-xs text-slate-600">Chỉ mang tính advisory; không phải xác nhận kỹ thuật và không tự cho phép công bố.</p>
      {loading ? <p className="mt-4 text-sm text-slate-500">Đang tải trạng thái quality…</p> : error ? (
        <p role="alert" className="mt-4 text-sm text-red-700">Không tải được quality review: {error}</p>
      ) : !quality ? <p className="mt-4 text-sm text-slate-500">Chưa có dữ liệu quality.</p> : (
        <>
          <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
            <div><dt className="text-xs uppercase tracking-wide text-slate-500">Freshness</dt><dd className="mt-1 font-semibold">{statusLabel(quality.freshness_status)}</dd></div>
            <div><dt className="text-xs uppercase tracking-wide text-slate-500">Lượt / stage</dt><dd className="mt-1 font-semibold">{quality.attempt == null ? "—" : `Lượt ${quality.attempt}`} · {quality.stage ?? "—"}</dd></div>
            <div><dt className="text-xs uppercase tracking-wide text-slate-500">Validator</dt><dd className="mt-1 font-semibold">{quality.quality ? `${quality.quality.controller_status} · ${quality.quality.candidate_release_status ?? "chưa có candidate status"}` : "Chưa có review"}</dd></div>
            <div><dt className="text-xs uppercase tracking-wide text-slate-500">Bản ghi còn mở</dt><dd className="mt-1 font-semibold">{quality.quality?.open_record_count ?? "Chưa có số liệu"}</dd></div>
            <div><dt className="text-xs uppercase tracking-wide text-slate-500">Coverage</dt><dd className="mt-1 font-semibold">{quality.coverage_status === "UNAVAILABLE" ? "Chưa khả dụng" : quality.coverage_status}</dd></div>
            <div><dt className="text-xs uppercase tracking-wide text-slate-500">Unresolved riêng</dt><dd className="mt-1 font-semibold">Chưa có dữ liệu trong API</dd></div>
          </dl>
          {quality.coverage_status === "UNAVAILABLE" && <p className="mt-2 text-xs text-slate-600">{quality.coverage_limitation}</p>}
          <div className="mt-4 border-t border-slate-200 pt-3">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Blockers</h3>
            {quality.quality?.blocking_checks == null ? <p className="mt-1 text-sm text-slate-500">Chưa có dữ liệu blocker.</p> : quality.quality.blocking_checks.length ? (
              <ul className="mt-1 list-inside list-disc text-sm text-amber-800">{quality.quality.blocking_checks.map((check) => <li key={check}>{check}</li>)}</ul>
            ) : <p className="mt-1 text-sm text-slate-600">Không ghi nhận blocker trong review này.</p>}
          </div>
          {quality.freshness_status === "INVALIDATED" && <p className="mt-3 rounded border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900">Review cũ không còn hiện hành ({quality.invalidation_event_type}). Cần rà soát lại sau thay đổi.</p>}
          {run?.artifacts.length ? <div className="mt-4 border-t border-slate-200 pt-3"><h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Artifact của lượt này</h3>{run.artifacts.map((artifact) => <a key={artifact.name} className="mt-2 block break-all text-sm text-brand underline" href={`/api/admin/jobs/${encodeURIComponent(jobCode)}/attempts/${run.attempt}/artifacts/${artifact.name.split("/").map(encodeURIComponent).join("/")}`}>{artifact.name}</a>)}</div> : null}
        </>
      )}
    </section>
  );
}
