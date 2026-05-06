from __future__ import annotations

from pathlib import Path

from chameleon.io.toml import dump_toml, load_toml


def test_toml_round_trips() -> None:
    src = 'a = 1\nb = "two"\n[nested]\nc = 3\n'
    parsed = load_toml(src)
    out = dump_toml(parsed)
    assert load_toml(out) == parsed


def test_toml_preserves_comments() -> None:
    src = "# top comment\na = 1  # inline\n[nested]\nb = 2\n"
    parsed = load_toml(src)
    out = dump_toml(parsed)
    assert "# top comment" in out
    assert "# inline" in out


def test_toml_preserves_table_order() -> None:
    src = "[zoo]\na = 1\n\n[apples]\nb = 2\n\n[mango]\nc = 3\n"
    parsed = load_toml(src)
    out = dump_toml(parsed)
    assert out.index("[zoo]") < out.index("[apples]") < out.index("[mango]")


def test_load_toml_from_path(tmp_path: Path) -> None:
    p = tmp_path / "x.toml"
    p.write_text('foo = "bar"\n', encoding="utf-8")
    parsed = load_toml(p)
    assert parsed["foo"] == "bar"
