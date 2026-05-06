from __future__ import annotations

from pathlib import Path

from chameleon.io.yaml import dump_yaml, load_yaml


def test_yaml_round_trips_preserves_keys_and_values(tmp_path: Path) -> None:
    src = "a: 1\nb:\n  c: 2\n  d: [1, 2, 3]\n"
    parsed = load_yaml(src)
    out = dump_yaml(parsed)
    re_parsed = load_yaml(out)
    assert re_parsed == parsed


def test_yaml_preserves_comments(tmp_path: Path) -> None:
    src = "# top comment\na: 1  # inline\nb: 2\n"
    parsed = load_yaml(src)
    out = dump_yaml(parsed)
    assert "# top comment" in out
    assert "# inline" in out


def test_yaml_preserves_key_order() -> None:
    src = "z: 1\na: 2\nm: 3\n"
    parsed = load_yaml(src)
    out = dump_yaml(parsed)
    assert out.index("z:") < out.index("a:") < out.index("m:")


def test_load_yaml_from_path(tmp_path: Path) -> None:
    p = tmp_path / "x.yaml"
    p.write_text("foo: bar\n", encoding="utf-8")
    assert load_yaml(p) == {"foo": "bar"}
