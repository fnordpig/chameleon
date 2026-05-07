"""TARGET_SPECIFIC removes cross-target propagation.

When the operator records a TARGET_SPECIFIC resolution for a path,
each target keeps its own value; the unified neutral path stays unset
(or at the schema default); a LossWarning surfaces the per-target
status. This test does NOT exercise the interactive ``[t]`` choice —
that's W15-B's territory. We seed the resolution + per-target values
directly into the neutral YAML and drive merge non-interactively.
"""

from __future__ import annotations

from datetime import UTC, datetime
from os.path import expanduser
from pathlib import Path

import pytest

from chameleon import cli
from chameleon._types import TargetId
from chameleon.codecs._protocol import TranspileCtx
from chameleon.io.yaml import dump_yaml, load_yaml
from chameleon.merge.changeset import walk_changes
from chameleon.merge.conflict import Conflict
from chameleon.merge.engine import MergeEngine, MergeRequest
from chameleon.merge.resolutions import compute_decision_hash
from chameleon.merge.resolve import Resolver, ResolverOutcome, Strategy
from chameleon.schema._constants import (
    BUILTIN_CLAUDE,
    BUILTIN_CODEX,
    Domains,
    OnConflict,
)
from chameleon.schema.neutral import (
    Neutral,
    Resolution,
    ResolutionDecisionKind,
)
from chameleon.state.paths import StatePaths
from chameleon.targets._registry import TargetRegistry


def _setup_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    state = tmp_path / "state"
    config = tmp_path / "config"
    home = tmp_path / "home"
    state.mkdir()
    config.mkdir()
    home.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("HOME", str(home))
    return {"state": state, "config": config, "home": home}


def _read_neutral(path: Path) -> Neutral:
    return Neutral.model_validate(load_yaml(path))


def test_target_specific_resolution_preserves_per_target_values(  # noqa: PLR0915 — full e2e
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Acceptance: TARGET_SPECIFIC keeps Claude=high, Codex=low.

    Drives the engine via a NonInteractiveResolver but seeds a stored
    TARGET_SPECIFIC Resolution with a matching hash so the engine's
    auto-replay path applies it (the resolver is never invoked for that
    leaf). The unified neutral path stays unset; each target's live
    config carries its own value.
    """
    paths = _setup_env(monkeypatch, tmp_path)
    neutral_file = paths["config"] / "chameleon" / "neutral.yaml"

    # 1. Bootstrap.
    assert cli.main(["init"]) == 0

    # 2. Drive an initial merge so per-target live files exist with a
    # shared value at identity.reasoning_effort.
    contents: dict[str, object] = {
        "schema_version": 1,
        "identity": {"reasoning_effort": "high"},
    }
    neutral_file.write_text(dump_yaml(contents), encoding="utf-8")
    assert cli.main(["merge", "--on-conflict=prefer-neutral"]) == 0

    # 3. Drift BOTH targets to different non-LKG values so the walker
    # sees a real CONFLICT (two distinct non-LKG sources). One side
    # alone would be CONSENSUAL.
    claude_settings = paths["home"] / ".claude" / "settings.json"
    claude_text = claude_settings.read_text()
    claude_text = claude_text.replace('"effortLevel": "high"', '"effortLevel": "medium"')
    claude_settings.write_text(claude_text)
    codex_config = paths["home"] / ".codex" / "config.toml"
    text = codex_config.read_text()
    text = text.replace('model_reasoning_effort = "high"', 'model_reasoning_effort = "low"')
    codex_config.write_text(text)

    # 4. Remove the operator's authored value (so the unified path is
    # unset — TARGET_SPECIFIC means "no unified opinion") and seed a
    # TARGET_SPECIFIC resolution + per-target values into neutral.yaml.
    # We delete the key from the raw YAML rather than writing ``null``,
    # because the operator-omission rule (issue #44) treats an explicit
    # ``null`` identical to "operator wrote null on purpose" if the key
    # is present — and we want true absence.
    contents_no_effort: dict[str, object] = {"schema_version": 1, "identity": {}}
    neutral_file.write_text(dump_yaml(contents_no_effort), encoding="utf-8")
    n = _read_neutral(neutral_file)
    sp = StatePaths.resolve(neutral_override=neutral_file)
    targets = TargetRegistry.discover()
    n0 = _read_neutral(sp.lkg)
    n_for_classify = n.model_copy(deep=True)

    ctx = TranspileCtx()
    per_target_neutral: dict[TargetId, Neutral] = {}
    for tid in targets.target_ids():
        target_cls = targets.get(tid)
        if target_cls is None:
            continue
        live: dict[str, bytes] = {}
        for spec in target_cls.assembler.files:
            live_path = Path(expanduser(spec.live_path))
            if live_path.exists():
                live[spec.repo_path] = live_path.read_bytes()
        domains, _ = target_cls.assembler.disassemble(live, ctx=ctx)
        target_neutral = Neutral(schema_version=1)
        for codec_cls in target_cls.codecs:
            if codec_cls.domain not in domains:
                continue
            try:
                fragment = codec_cls.from_target(domains[codec_cls.domain], ctx)
            except NotImplementedError:
                continue
            setattr(target_neutral, codec_cls.domain.value, fragment)
        per_target_neutral[tid] = target_neutral

    records = walk_changes(n0, n_for_classify, per_target_neutral)
    target_record = next(r for r in records if r.path.render() == "identity.reasoning_effort")
    assert target_record.domain is Domains.IDENTITY
    target_hash = compute_decision_hash(target_record)

    n.resolutions.items["identity.reasoning_effort"] = Resolution(
        decided_at=datetime.now(tz=UTC),
        decision=ResolutionDecisionKind.TARGET_SPECIFIC,
        decision_hash=target_hash,
    )
    neutral_file.write_text(
        dump_yaml(n.model_dump(mode="json", exclude_none=False)),
        encoding="utf-8",
    )

    # 5. Run merge programmatically so we can capture LossWarnings.
    sp2 = StatePaths.resolve(neutral_override=neutral_file)
    engine = MergeEngine(
        targets=targets,
        paths=sp2,
        strategy=Strategy(kind=OnConflict.FAIL),
    )
    result = engine.merge(MergeRequest())
    assert result.exit_code == 0

    # 6. Each target's live file still carries its own per-target value.
    claude_settings_text = (paths["home"] / ".claude" / "settings.json").read_text()
    codex_config_text = (paths["home"] / ".codex" / "config.toml").read_text()
    assert '"effortLevel": "medium"' in claude_settings_text
    assert 'model_reasoning_effort = "low"' in codex_config_text

    # 7. The unified neutral path is unset (or schema default).
    n_after = _read_neutral(neutral_file)
    assert n_after.identity.reasoning_effort is None

    # 8. ``targets.<tid>.target_specific`` carries each target's value.
    claude_bag = n_after.targets.get(BUILTIN_CLAUDE)
    codex_bag = n_after.targets.get(BUILTIN_CODEX)
    assert claude_bag is not None
    assert codex_bag is not None
    assert claude_bag.target_specific.get("identity.reasoning_effort") == "medium"
    assert codex_bag.target_specific.get("identity.reasoning_effort") == "low"

    # 9. A LossWarning fires noting the target-specific status.
    target_specific_warnings = [w for w in result.warnings if "target-specific" in w.message]
    assert target_specific_warnings, "expected at least one target-specific LossWarning"


def test_resolver_returning_target_specific_outcome(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A resolver that yields a TARGET_SPECIFIC outcome plumbs the same way.

    Drives the engine with a fake resolver that returns
    ``ResolverOutcome(decision=TARGET_SPECIFIC, persist=True)`` for the
    sole conflict. After merge, the resolution is persisted and each
    target keeps its own value.
    """
    paths = _setup_env(monkeypatch, tmp_path)
    neutral_file = paths["config"] / "chameleon" / "neutral.yaml"

    assert cli.main(["init"]) == 0
    contents: dict[str, object] = {
        "schema_version": 1,
        "identity": {"reasoning_effort": "high"},
    }
    neutral_file.write_text(dump_yaml(contents), encoding="utf-8")
    assert cli.main(["merge", "--on-conflict=prefer-neutral"]) == 0
    claude_settings = paths["home"] / ".claude" / "settings.json"
    claude_text = claude_settings.read_text()
    claude_text = claude_text.replace('"effortLevel": "high"', '"effortLevel": "medium"')
    claude_settings.write_text(claude_text)
    codex_config = paths["home"] / ".codex" / "config.toml"
    text = codex_config.read_text()
    text = text.replace('model_reasoning_effort = "high"', 'model_reasoning_effort = "low"')
    codex_config.write_text(text)

    # Clear the unified value (delete the key) so the disagreement
    # between the two targets is the sole source of conflict.
    cleared: dict[str, object] = {"schema_version": 1, "identity": {}}
    neutral_file.write_text(dump_yaml(cleared), encoding="utf-8")

    class _TargetSpecificResolver:
        """Deterministic resolver: every conflict → TARGET_SPECIFIC."""

        def resolve(self, conflict: Conflict) -> ResolverOutcome:
            return ResolverOutcome(
                decision=ResolutionDecisionKind.TARGET_SPECIFIC,
                persist=True,
            )

    sp = StatePaths.resolve(neutral_override=neutral_file)
    targets = TargetRegistry.discover()
    resolver: Resolver = _TargetSpecificResolver()
    engine = MergeEngine(targets=targets, paths=sp, resolver=resolver)
    result = engine.merge(MergeRequest())
    assert result.exit_code == 0

    n_after = _read_neutral(neutral_file)
    # Resolution persisted (persist=True path).
    assert "identity.reasoning_effort" in n_after.resolutions.items
    assert (
        n_after.resolutions.items["identity.reasoning_effort"].decision
        is ResolutionDecisionKind.TARGET_SPECIFIC
    )
    # Each target's value preserved.
    claude_bag = n_after.targets.get(BUILTIN_CLAUDE)
    codex_bag = n_after.targets.get(BUILTIN_CODEX)
    assert claude_bag is not None
    assert codex_bag is not None
    assert claude_bag.target_specific.get("identity.reasoning_effort") == "medium"
    assert codex_bag.target_specific.get("identity.reasoning_effort") == "low"
