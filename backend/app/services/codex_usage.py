import json
import logging
import select
import shlex
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger("boq-audit.codex-usage")
_cache_lock = threading.Lock()
_cached_at = 0.0
_cached_value: dict[str, Any] | None = None


def _read_response(process: subprocess.Popen[str], request_id: int, deadline: float) -> dict[str, Any]:
    if process.stdout is None:
        raise RuntimeError("Codex app-server không có stdout")
    while time.monotonic() < deadline:
        ready, _, _ = select.select([process.stdout], [], [], max(0.0, deadline - time.monotonic()))
        if not ready:
            break
        line = process.stdout.readline()
        if not line:
            break
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("id") != request_id:
            continue
        if "error" in message:
            detail = message["error"].get("message", "Codex từ chối yêu cầu")
            raise RuntimeError(detail)
        return message.get("result") or {}
    raise TimeoutError("Codex app-server không phản hồi kịp thời")


def _query_rate_limits() -> dict[str, Any]:
    settings = get_settings()
    executable = shlex.split(settings.codex_command)
    if not executable:
        raise RuntimeError("CODEX_COMMAND chưa được cấu hình")
    command = [*executable, "app-server", "--stdio"]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    try:
        if process.stdin is None:
            raise RuntimeError("Codex app-server không có stdin")
        deadline = time.monotonic() + settings.codex_usage_query_timeout_seconds
        initialize = {"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "boq-audit-portal", "version": "1.0.0"}, "capabilities": None}}
        process.stdin.write(json.dumps(initialize) + "\n")
        process.stdin.flush()
        _read_response(process, 1, deadline)
        process.stdin.write(json.dumps({"method": "initialized"}) + "\n")
        request = {"id": 2, "method": "account/rateLimits/read", "params": {"excludeResetCreditDetails": True, "supportsLunaReserve": False}}
        process.stdin.write(json.dumps(request) + "\n")
        process.stdin.flush()
        return _read_response(process, 2, deadline)
    finally:
        if process.stdin:
            process.stdin.close()
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


def _window(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not isinstance(value.get("usedPercent"), (int, float)):
        return None
    used = min(100, max(0, round(value["usedPercent"])))
    return {"used_percent": used, "remaining_percent": 100 - used, "resets_at": value.get("resetsAt"), "window_duration_minutes": value.get("windowDurationMins")}


def _public_snapshot(raw: dict[str, Any]) -> dict[str, Any]:
    snapshot = raw.get("rateLimits") if isinstance(raw.get("rateLimits"), dict) else {}
    return {"available": True, "plan_type": snapshot.get("planType"), "ordinary_usage_allowed": raw.get("ordinaryUsageAllowed"), "primary": _window(snapshot.get("primary")), "secondary": _window(snapshot.get("secondary")), "checked_at": datetime.now(timezone.utc).isoformat()}


def get_codex_usage() -> dict[str, Any]:
    global _cached_at, _cached_value
    settings = get_settings()
    with _cache_lock:
        now = time.monotonic()
        if _cached_value is not None and now - _cached_at < settings.codex_usage_cache_seconds:
            return _cached_value
        try:
            value = _public_snapshot(_query_rate_limits())
        except Exception as exc:
            logger.warning("codex_usage_unavailable: %s", exc)
            value = {"available": False, "plan_type": None, "ordinary_usage_allowed": None, "primary": None, "secondary": None, "checked_at": datetime.now(timezone.utc).isoformat()}
        _cached_at = now
        _cached_value = value
        return value
