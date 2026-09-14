from __future__ import annotations

from enum import StrEnum


class TrackState(StrEnum):
    DISCOVERED = "DISCOVERED"
    RESOLVING = "RESOLVING"
    RESOLVED = "RESOLVED"
    SEARCHING = "SEARCHING"
    MATCHED = "MATCHED"
    QUEUED = "QUEUED"
    DOWNLOADING = "DOWNLOADING"
    TRANSCODING = "TRANSCODING"
    TAGGING = "TAGGING"
    COMPLETE = "COMPLETE"
    COMPLETE_WITH_WARNINGS = "COMPLETE_WITH_WARNINGS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


ALLOWED_TRANSITIONS: dict[TrackState, set[TrackState]] = {
    TrackState.DISCOVERED: {TrackState.RESOLVING, TrackState.RESOLVED, TrackState.SKIPPED},
    TrackState.RESOLVING: {TrackState.RESOLVED, TrackState.FAILED, TrackState.CANCELLED},
    TrackState.RESOLVED: {TrackState.QUEUED, TrackState.SKIPPED},
    TrackState.QUEUED: {TrackState.SEARCHING, TrackState.CANCELLED, TrackState.SKIPPED},
    TrackState.SEARCHING: {TrackState.MATCHED, TrackState.QUEUED, TrackState.FAILED, TrackState.CANCELLED},
    TrackState.MATCHED: {TrackState.DOWNLOADING, TrackState.QUEUED, TrackState.FAILED, TrackState.CANCELLED},
    TrackState.DOWNLOADING: {TrackState.TRANSCODING, TrackState.TAGGING, TrackState.COMPLETE, TrackState.QUEUED, TrackState.FAILED, TrackState.CANCELLED},
    TrackState.TRANSCODING: {TrackState.TAGGING, TrackState.QUEUED, TrackState.FAILED, TrackState.CANCELLED},
    TrackState.TAGGING: {TrackState.COMPLETE, TrackState.COMPLETE_WITH_WARNINGS, TrackState.QUEUED, TrackState.FAILED, TrackState.CANCELLED},
    TrackState.FAILED: {TrackState.QUEUED, TrackState.SKIPPED},
    TrackState.COMPLETE: set(), TrackState.COMPLETE_WITH_WARNINGS: set(),
    TrackState.SKIPPED: set(), TrackState.CANCELLED: {TrackState.QUEUED},
}


def can_transition(current: TrackState | str, target: TrackState | str) -> bool:
    return TrackState(target) in ALLOWED_TRANSITIONS[TrackState(current)]


def retry_delay(attempt: int) -> int | None:
    """Return the delay after a failed 1-based attempt, or None when exhausted."""
    delays = (10, 30, 90)
    if attempt < 1 or attempt > len(delays):
        return None
    return delays[attempt - 1]

