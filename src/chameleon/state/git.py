"""Subprocess-based git wrapper for per-target state-repos.

We deliberately avoid GitPython — subprocess gives us less indirection,
no extra dependency, and clearer error messages when git itself is the
problem (e.g. not installed).
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class GitNotInstalledError(RuntimeError):
    """Raised when `git` is not in PATH at runtime."""


@dataclass(frozen=True)
class CommitInfo:
    sha: str
    subject: str
    body: str
    trailers: str


class GitRepo:
    def __init__(self, path: Path) -> None:
        self.path = path

    @staticmethod
    def _git() -> str:
        exe = shutil.which("git")
        if exe is None:
            msg = "`git` executable not found on PATH; install git or fix PATH"
            raise GitNotInstalledError(msg)
        return exe

    @classmethod
    def init(cls, path: Path) -> GitRepo:
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [cls._git(), "init", "-q", "-b", "main", str(path)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [cls._git(), "-C", str(path), "config", "user.email", "chameleon@localhost"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [cls._git(), "-C", str(path), "config", "user.name", "Chameleon"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [cls._git(), "-C", str(path), "config", "commit.gpgsign", "false"],
            check=True,
            capture_output=True,
        )
        return cls(path)

    def add_all(self) -> None:
        subprocess.run(
            [self._git(), "-C", str(self.path), "add", "-A"],
            check=True,
            capture_output=True,
        )

    def commit(self, subject: str, *, trailer: Mapping[str, str] | None = None) -> str:
        body = ""
        if trailer:
            body = "\n\n" + "\n".join(f"{k}: {v}" for k, v in trailer.items())
        subprocess.run(
            [
                self._git(),
                "-C",
                str(self.path),
                "commit",
                "--allow-empty-message",
                "-q",
                "-m",
                subject + body,
            ],
            check=True,
            capture_output=True,
        )
        out = subprocess.run(
            [self._git(), "-C", str(self.path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return out.stdout.strip()

    def head_commit(self) -> str | None:
        result = subprocess.run(
            [self._git(), "-C", str(self.path), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    def read_at_head(self, repo_path: str) -> bytes | None:
        """Return the raw bytes of `repo_path` at HEAD, or None if absent.

        `repo_path` is repo-relative (e.g. `settings/settings.json`). Returns
        None when there's no HEAD yet, when the path isn't tracked at HEAD,
        or when the blob is unreadable. Uses `git show HEAD:<path>` with
        binary output so non-UTF8 blobs round-trip cleanly.
        """
        if self.head_commit() is None:
            return None
        result = subprocess.run(
            [self._git(), "-C", str(self.path), "show", f"HEAD:{repo_path}"],
            check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            return None
        return result.stdout

    def is_clean(self) -> bool:
        out = subprocess.run(
            [self._git(), "-C", str(self.path), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        )
        return out.stdout.strip() == ""

    def log(self) -> list[dict[str, str]]:
        sep = "\x1e"
        out = subprocess.run(
            [
                self._git(),
                "-C",
                str(self.path),
                "log",
                f"--pretty=format:%H{sep}%s{sep}%b{sep}%(trailers)%x00",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        commits: list[dict[str, str]] = []
        commits_str = out.stdout.rstrip("\x00")
        for commit_str in commits_str.split("\x00"):
            if not commit_str:
                continue
            parts = commit_str.split(sep)
            while len(parts) < 4:
                parts.append("")
            sha, subject, body, trailers = parts[:4]
            commits.append({"sha": sha, "subject": subject, "body": body, "trailers": trailers})
        return commits


__all__ = ["CommitInfo", "GitNotInstalledError", "GitRepo"]
