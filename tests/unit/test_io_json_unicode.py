"""Unicode preservation through `dump_json`/`load_json` (B4).

Real Claude / Codex configs contain non-ASCII content all over the
place: em-dashes in personality strings, smart quotes, emoji, and
multilingual user content. The JSON spec permits raw UTF-8, so the
correct behaviour is to round-trip the literal bytes, NOT escape
to `\\uXXXX` sequences.

Python's stdlib defaults `ensure_ascii=True`, which corrupts those
characters on every chameleon merge. `dump_json` must override that
default; this test pins the contract.

Source uses `\\u` escapes for the smart-quote codepoints (which ruff
RUF001 flags as ambiguous in source) and literal codepoints for
em-dash, emoji, and CJK (which are unambiguous).
"""

from __future__ import annotations

from chameleon.io.json import dump_json, load_json

# Real-world non-ASCII codepoints:
#   - U+2014 EM DASH (the personality string in the exemplar fixture)
#   - U+2018, U+2019, U+201C, U+201D smart quotes
#   - U+1F4A1 LIGHT BULB emoji (BMP-outside; surrogate-pair-escapes
#     under ensure_ascii=True)
#   - U+4E2D, U+6587 CJK ideographs (multi-byte UTF-8)
EM_DASH = "—"
# Smart quotes built from chr() to avoid ruff RUF001 ambiguity warnings
# (literal U+2018 etc in source is flagged as visually ambiguous with ASCII).
LSQUO = chr(0x2018)
RSQUO = chr(0x2019)
LDQUO = chr(0x201C)
RDQUO = chr(0x201D)
LIGHT_BULB = "\U0001f4a1"
CJK_ZHONG = "中"
CJK_WEN = "文"

NON_ASCII_PAYLOAD = {
    "personality": f"Patient cactus{EM_DASH}in a blizzard.",
    "smart_quotes": f"{LSQUO}single{RSQUO} and {LDQUO}double{RDQUO}",
    "emoji": f"idea: {LIGHT_BULB}",
    "cjk": f"{CJK_ZHONG}{CJK_WEN}",
}


def test_dump_json_preserves_em_dash_literally() -> None:
    """The em-dash from `companion.personality` must NOT escape to \\u2014."""
    out = dump_json({"personality": f"Patient cactus{EM_DASH}in a blizzard."})
    assert EM_DASH in out, "em-dash should be a literal UTF-8 codepoint"
    assert "\\u2014" not in out, "em-dash must not be escaped to \\u2014"


def test_dump_json_preserves_smart_quotes_literally() -> None:
    out = dump_json({"q": f"{LSQUO}a{RSQUO} and {LDQUO}b{RDQUO}"})
    for cp in (LSQUO, RSQUO, LDQUO, RDQUO):
        assert cp in out, f"smart quote U+{ord(cp):04X} should be literal"
        assert f"\\u{ord(cp):04x}" not in out.lower(), (
            f"smart quote U+{ord(cp):04X} must not be escaped"
        )


def test_dump_json_preserves_emoji_literally() -> None:
    """Emoji (BMP-outside) would surrogate-pair-escape under ensure_ascii."""
    out = dump_json({"e": LIGHT_BULB})
    assert LIGHT_BULB in out, "emoji should be a literal UTF-8 codepoint"
    assert "\\ud83d" not in out.lower(), (
        "emoji must not be split into UTF-16 surrogate pair escapes"
    )


def test_dump_json_preserves_cjk_literally() -> None:
    out = dump_json({"c": f"{CJK_ZHONG}{CJK_WEN}"})
    assert CJK_ZHONG in out, "CJK ideograph U+4E2D should be literal"
    assert CJK_WEN in out, "CJK ideograph U+6587 should be literal"
    assert "\\u4e2d" not in out, "CJK ideograph U+4E2D must not be escaped"
    assert "\\u6587" not in out, "CJK ideograph U+6587 must not be escaped"


def test_dump_json_load_json_round_trips_full_payload() -> None:
    """After dump_json -> load_json the dict is structurally identical."""
    out = dump_json(NON_ASCII_PAYLOAD)
    again = load_json(out)
    assert again == NON_ASCII_PAYLOAD


def test_dump_json_output_is_valid_utf8_bytes() -> None:
    """The string returned by dump_json must encode to valid UTF-8
    (no lone surrogates, no replacement chars)."""
    out = dump_json(NON_ASCII_PAYLOAD)
    encoded = out.encode("utf-8")
    decoded = encoded.decode("utf-8")
    assert decoded == out
