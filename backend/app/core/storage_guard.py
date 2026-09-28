import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, status

from app.core.config import get_settings

logger = logging.getLogger("boq-audit.storage")
MIB = 1024 * 1024


@dataclass(frozen=True)
class StorageSnapshot:
    used_bytes: int
    free_bytes: int
    quota_bytes: int
    minimum_free_bytes: int

    @property
    def available_bytes(self) -> int:
        return max(0, min(self.quota_bytes - self.used_bytes, self.free_bytes - self.minimum_free_bytes))


def _directory_size(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def storage_snapshot() -> StorageSnapshot:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    disk = shutil.disk_usage(settings.data_dir)
    return StorageSnapshot(
        used_bytes=_directory_size(settings.jobs_dir),
        free_bytes=disk.free,
        quota_bytes=settings.max_total_storage_mb * MIB,
        minimum_free_bytes=settings.min_free_disk_mb * MIB,
    )


def require_upload_capacity(incoming_bytes: int = 0) -> StorageSnapshot:
    snapshot = storage_snapshot()
    if snapshot.available_bytes <= 0 or incoming_bytes > snapshot.available_bytes:
        logger.warning(
            "upload_rejected_storage used_bytes=%s free_bytes=%s quota_bytes=%s incoming_bytes=%s",
            snapshot.used_bytes,
            snapshot.free_bytes,
            snapshot.quota_bytes,
            incoming_bytes,
        )
        raise HTTPException(
            status.HTTP_507_INSUFFICIENT_STORAGE,
            "Hệ thống sắp hết dung lượng. Vui lòng liên hệ quản trị viên.",
        )
    return snapshot
