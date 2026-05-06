from __future__ import annotations

from pathlib import Path

from chameleon.io.json import dump_json, load_json


def test_json_round_trips() -> None:
    src = '{"a": 1, "b": {"c": 2, "d": [1, 2, 3]}}'
    parsed = load_json(src)
    out = dump_json(parsed)
    assert load_json(out) == parsed


def test_json_preserves_key_insertion_order() -> None:
    src = '{"z": 1, "a": 2, "m": 3}'
    parsed = load_json(src)
    out = dump_json(parsed)
    assert out.index('"z"') < out.index('"a"') < out.index('"m"')


def test_json_indent_2() -> None:
    out = dump_json({"a": {"b": 1}})
    assert "  " in out
    assert "    " in out  # nested level


def test_load_json_from_path(tmp_path: Path) -> None:
    p = tmp_path / "x.json"
    p.write_text('{"foo": "bar"}', encoding="utf-8")
    assert load_json(p) == {"foo": "bar"}
