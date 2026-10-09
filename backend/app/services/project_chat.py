import hashlib
import hmac
import json
from html.parser import HTMLParser
from urllib.request import Request, urlopen

from app.core.config import get_settings
from app.models.entities import Job, JobStatus
from app.storage.files import stored_job_file


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str):
        self.parts.append(data)


def public_context(job: Job) -> dict:
    context = {"project": job.project_name, "job_code": job.job_code, "status": job.status.value}
    if job.status == JobStatus.COMPLETED:
        parser = PlainText()
        parser.feed(job.customer_notes or "")
        context.update(summary=" ".join(parser.parts)[:16000], critical_errors=job.critical_errors, warnings=job.warnings,
                       reports=[{"id": o.id, "name": o.original_filename, "type": o.file_type.value, "bytes": o.file_size, "created_at": o.created_at.isoformat()} for o in sorted(job.outputs, key=lambda o: o.id)])
    return context


def context_version(job: Job) -> str:
    return hashlib.sha256(json.dumps(public_context(job), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def report_context(job: Job) -> dict:
    context = public_context(job)
    context["evidence_scope"] = "Metadata only; no drawing, spreadsheet, or report contents have been read."
    if job.status != JobStatus.COMPLETED:
        return context
    context["evidence_scope"] = "Approved customer summary; spreadsheet cell contents are not available."
    # Bounded excerpts from approved reports only. Never send raw uploads or worker logs.
    import fitz
    for output in job.outputs:
        if output.file_type.value != "ANNOTATED_PDF":
            continue
        try:
            path = stored_job_file(job.job_code, output.file_path)
            with fitz.open(path) as document:
                remaining = 16000
                excerpts = []
                for page_number in range(min(8, len(document))):
                    snippet = document[page_number].get_text()[:remaining]
                    if snippet.strip():
                        excerpts.append({"report": output.original_filename, "page": page_number + 1, "text": snippet})
                        remaining -= len(snippet)
                    if remaining <= 0:
                        break
                context["pdf_excerpts"] = excerpts
                context["evidence_scope"] += f" PDF text excerpts only: first {min(8, len(document))} of {len(document)} pages, at most 16000 characters; visual details and remaining pages not read."
        except Exception:
            context["evidence_scope"] += " PDF text extraction unavailable."
        break
    return context


def gateway_reply(job: Job, thread_id: str | None, version: str, history: list[dict], message: str) -> dict:
    settings = get_settings()
    session_key = hmac.new(settings.chat_gateway_token.encode(), f"{job.id}:{job.user_id}:{version}".encode(), hashlib.sha256).hexdigest()
    payload = {"session_key": session_key, "thread_id": thread_id, "context": report_context(job) if not thread_id else {}, "history": history, "message": message}
    request = Request(settings.chat_gateway_url.rstrip("/") + "/reply", data=json.dumps(payload, ensure_ascii=False).encode(), headers={"Content-Type": "application/json", "Authorization": "Bearer " + settings.chat_gateway_token}, method="POST")
    with urlopen(request, timeout=settings.chat_timeout_seconds + 10) as response:
        raw = response.read(100001)
    if len(raw) > 100000:
        raise ValueError("Oversized AI response")
    reply = json.loads(raw)
    if not isinstance(reply.get("text"), str) or not reply["text"].strip() or len(reply["text"]) > 12000:
        raise ValueError("Invalid AI response")
    if not isinstance(reply.get("thread_id"), str) or len(reply["thread_id"]) > 100:
        raise ValueError("Invalid conversation")
    return reply
