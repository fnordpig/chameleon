from __future__ import annotations

from chameleon.schema.directives import Directives


def test_directives_minimal() -> None:
    d = Directives()
    assert d.system_prompt_file is None
    assert d.commit_attribution is None


def test_directives_with_values() -> None:
    d = Directives(
        system_prompt_file="~/.config/chameleon/AGENTS.md",
        commit_attribution="Generated with Chameleon",
    )
    assert d.system_prompt_file == "~/.config/chameleon/AGENTS.md"
    assert d.commit_attribution == "Generated with Chameleon"
