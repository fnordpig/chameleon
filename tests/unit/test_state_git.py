from __future__ import annotations

from pathlib import Path

from chameleon.state.git import GitRepo


def test_init_creates_repo(tmp_path: Path) -> None:
    repo = GitRepo.init(tmp_path)
    assert (tmp_path / ".git").exists()
    assert repo.head_commit() is None  # no commits yet


def test_commit_files(tmp_path: Path) -> None:
    repo = GitRepo.init(tmp_path)
    (tmp_path / "x.txt").write_text("hello\n")
    repo.add_all()
    sha = repo.commit("initial: x.txt", trailer={"Merge-Id": "abc"})
    assert sha
    assert repo.head_commit() == sha
    log = repo.log()
    assert log[0]["subject"] == "initial: x.txt"
    assert "Merge-Id: abc" in log[0]["trailers"]


def test_status_clean(tmp_path: Path) -> None:
    repo = GitRepo.init(tmp_path)
    (tmp_path / "x.txt").write_text("hello\n")
    repo.add_all()
    repo.commit("initial")
    assert repo.is_clean()


def test_status_dirty_after_modify(tmp_path: Path) -> None:
    repo = GitRepo.init(tmp_path)
    (tmp_path / "x.txt").write_text("hello\n")
    repo.add_all()
    repo.commit("initial")
    (tmp_path / "x.txt").write_text("changed\n")
    assert not repo.is_clean()
