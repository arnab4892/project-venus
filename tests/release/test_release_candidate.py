"""RC lifecycle ledger (milestone 3a). Uses the rolled-back seeded_conn."""

from __future__ import annotations

from agentkit.release.candidate import create_rc, get_rc, list_rc, set_status


def _num(rc_id: str) -> int:
    return int(rc_id.rsplit(".", 1)[1])


def test_create_allocates_sequential_ids(seeded_conn) -> None:
    first = create_rc(seeded_conn, "jyotech")
    second = create_rc(seeded_conn, "jyotech")
    # Robust to any committed ledger rows: the second id is one past the first.
    assert first.startswith("rc.jyotech.")
    assert _num(second) == _num(first) + 1
    assert get_rc(seeded_conn, first)["status"] == "open"


def test_list_returns_created_candidates(seeded_conn) -> None:
    first = create_rc(seeded_conn, "jyotech")
    second = create_rc(seeded_conn, "jyotech")
    ids = [r["id"] for r in list_rc(seeded_conn, "jyotech")]
    # The two just-created candidates lead the list, newest first.
    assert ids[:2] == [second, first]


def test_status_transitions(seeded_conn) -> None:
    rc = create_rc(seeded_conn, "jyotech")
    set_status(seeded_conn, rc, "exported")
    assert get_rc(seeded_conn, rc)["status"] == "exported"


def test_bad_status_rejected(seeded_conn) -> None:
    rc = create_rc(seeded_conn, "jyotech")
    import pytest

    with pytest.raises(ValueError):
        set_status(seeded_conn, rc, "not-a-status")
