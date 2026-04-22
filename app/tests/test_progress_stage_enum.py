"""
``ProgressStage`` — domain enum contract.

Verifies:

* Every stage has a lowercase, hyphen-free string value (safe to stuff
  into a Redis hash field and into a pubsub event payload).
* Terminal stages are exactly the three we expect the bot's progress
  updater to stop watching on.
* Non-terminal stages do NOT claim to be terminal (prevents silent
  truncation of progress streams if a new stage is added without a
  review of ``is_terminal``).
"""

from __future__ import annotations

from app.domain.enums import ProgressStage


def test_values_are_stable_strings() -> None:
    assert ProgressStage.ANALYZING.value == "analyzing"
    assert ProgressStage.DOWNLOADING.value == "downloading"
    assert ProgressStage.PROCESSING.value == "processing"
    assert ProgressStage.UPLOADING.value == "uploading"
    assert ProgressStage.DONE.value == "done"
    assert ProgressStage.CANCELLED.value == "cancelled"
    assert ProgressStage.FAILED.value == "failed"


def test_terminal_set_matches_spec() -> None:
    terminal = {s for s in ProgressStage if s.is_terminal}
    assert terminal == {
        ProgressStage.DONE,
        ProgressStage.CANCELLED,
        ProgressStage.FAILED,
    }


def test_non_terminal_stages_are_progress_samples() -> None:
    non_terminal = {s for s in ProgressStage if not s.is_terminal}
    assert non_terminal == {
        ProgressStage.ANALYZING,
        ProgressStage.DOWNLOADING,
        ProgressStage.PROCESSING,
        ProgressStage.UPLOADING,
    }


def test_str_enum_round_trip() -> None:
    """Round-trip through str — matters for Redis hash values and JSON
    payloads where we store ``stage.value`` and later call
    ``ProgressStage(value)`` on read."""
    for stage in ProgressStage:
        assert ProgressStage(stage.value) is stage
