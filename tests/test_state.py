import pytest

from app.state import TrackState, can_transition, retry_delay


def test_track_state_happy_path():
    path = [
        TrackState.RESOLVED, TrackState.QUEUED, TrackState.SEARCHING, TrackState.MATCHED,
        TrackState.DOWNLOADING, TrackState.TRANSCODING, TrackState.TAGGING, TrackState.COMPLETE,
    ]
    assert all(can_transition(current, target) for current, target in zip(path, path[1:]))
    assert not can_transition(TrackState.COMPLETE, TrackState.QUEUED)


@pytest.mark.parametrize("attempt, expected", [(1, 10), (2, 30), (3, 90), (4, None), (0, None)])
def test_retry_policy(attempt, expected):
    assert retry_delay(attempt) == expected

