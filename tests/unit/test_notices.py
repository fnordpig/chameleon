from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from chameleon.state.notices import LoginNotice, NoticeStore


def test_notice_round_trip(tmp_path: Path) -> None:
    store = NoticeStore(tmp_path)
    notice = LoginNotice(
        timestamp=datetime.now(tz=UTC),
        merge_id="abc-123",
        exit_code=2,
        reason="conflict on identity.model",
        report_path=str(tmp_path / "report.txt"),
    )
    store.write(notice)
    listed = store.entries()
    assert len(listed) == 1
    assert listed[0].merge_id == "abc-123"


def test_clear_all(tmp_path: Path) -> None:
    store = NoticeStore(tmp_path)
    store.write(
        LoginNotice(
            timestamp=datetime.now(tz=UTC),
            merge_id="m",
            exit_code=2,
            reason="r",
            report_path="p",
        )
    )
    store.clear()
    assert store.entries() == []
