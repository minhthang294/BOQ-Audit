"""Observe-only quality invalidation and history guards.

This module never changes publication state or job lifecycle. It records
append-only evidence that a mutation occurred after a quality review, so the
same evidence can be consumed when enforcement is approved separately.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import AuditQualityReview, AuditQualityReviewEvent, AuditRun

SIGNOFF_ACCEPTED = "SIGNOFF_ACCEPTED"
REVOKED = "REVOKED"
QUALITY_INVALIDATED = "QUALITY_INVALIDATED"
TERMINAL_INVALIDATION_EVENTS = {REVOKED, QUALITY_INVALIDATED}


def latest_review(db: Session, job_id: int) -> AuditQualityReview | None:
    """Return the highest review id attached to the job's newest audit run."""
    newest_run_id = db.scalar(
        select(AuditRun.id)
        .where(AuditRun.job_id == job_id)
        .order_by(AuditRun.attempt.desc(), AuditRun.id.desc())
        .limit(1)
    )
    if newest_run_id is None:
        return None
    return db.scalar(
        select(AuditQualityReview)
        .where(AuditQualityReview.run_id == newest_run_id)
        .order_by(AuditQualityReview.id.desc())
        .limit(1)
    )


def latest_event(db: Session, review_id: int) -> AuditQualityReviewEvent | None:
    return db.scalar(
        select(AuditQualityReviewEvent)
        .where(AuditQualityReviewEvent.review_id == review_id)
        .order_by(AuditQualityReviewEvent.event_id.desc())
        .limit(1)
    )


def has_quality_review(db: Session, job_id: int) -> bool:
    return db.scalar(
        select(AuditQualityReview.id)
        .join(AuditRun, AuditRun.id == AuditQualityReview.run_id)
        .where(AuditRun.job_id == job_id)
        .limit(1)
    ) is not None


def record_mutation_invalidation(
    db: Session,
    job_id: int,
    actor_id: str | int,
    reason: str,
) -> AuditQualityReviewEvent | None:
    """Append one invalidation event inside the caller's transaction.

    If the latest event is an accepted signoff, append REVOKED. Otherwise
    ensure the current review has exactly one QUALITY_INVALIDATED event.
    Repeated mutations against the same review are idempotent until a newer
    review becomes current.
    """
    reason = reason.strip()
    if not reason:
        raise ValueError("mutation invalidation reason must not be empty")

    review = latest_review(db, job_id)
    if review is None:
        return None

    current = latest_event(db, review.id)
    if current is not None and current.event_type in TERMINAL_INVALIDATION_EVENTS:
        return None
    if current is not None and current.event_type == SIGNOFF_ACCEPTED:
        event_type = REVOKED
    else:
        already_recorded = db.scalar(
            select(AuditQualityReviewEvent.event_id)
            .where(
                AuditQualityReviewEvent.review_id == review.id,
                AuditQualityReviewEvent.event_type == QUALITY_INVALIDATED,
            )
            .limit(1)
        )
        if already_recorded is not None:
            return None
        event_type = QUALITY_INVALIDATED

    event = AuditQualityReviewEvent(
        review_id=review.id,
        event_type=event_type,
        actor_id=str(actor_id),
        reason=reason,
    )
    db.add(event)
    db.flush()
    return event
