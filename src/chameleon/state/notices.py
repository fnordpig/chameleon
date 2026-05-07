"""Login-time conflict notices."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from chameleon.io.toml import dump_toml, load_toml


class LoginNotice(BaseModel):
    """Persistent surface for a non-interactive merge that failed."""

    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    merge_id: str
    exit_code: int
    reason: str
    report_path: str


class NoticeStore:
    def __init__(self, dir_path: Path) -> None:
        self.dir = dir_path

    def write(self, notice: LoginNotice) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        ts_str = notice.timestamp.strftime("%Y%m%dT%H%M%SZ")
        path = self.dir / f"{ts_str}-{notice.merge_id}.toml"
        path.write_text(dump_toml(notice.model_dump(mode="json")), encoding="utf-8")

    def entries(self) -> list[LoginNotice]:
        if not self.dir.exists():
            return []
        out: list[LoginNotice] = []
        for path in sorted(self.dir.glob("*.toml")):
            raw = load_toml(path)
            out.append(LoginNotice.model_validate(dict(raw)))
        return out

    def clear(self) -> None:
        if not self.dir.exists():
            return
        for path in self.dir.glob("*.toml"):
            path.unlink()


__all__ = ["LoginNotice", "NoticeStore"]
