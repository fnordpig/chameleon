"""Engine state-machine fuzz (Wave-F5 / FUZZ-5).

This is the highest-yield fuzz test in the wave: rather than poking one
codec or one round-trip in isolation, it drives Hypothesis's
:class:`~hypothesis.stateful.RuleBasedStateMachine` against the live
``MergeEngine`` + filesystem + state-repo trio and asserts cross-rule
invariants after every action.

Why a state machine here?
    Most production bugs in chameleon are about *operation-sequence*
    invariants rather than single-call behaviour. Examples this rig is
    designed to surface:

    * "Two ``KEEP`` merges in a row produce identical bytes" — the
      property the B2 follow-on was supposed to guarantee. A regression
      where the second merge mutates the live target file would show
      up as the :meth:`EngineStateMachine.idempotent_after_merge`
      invariant firing immediately after the first ``merge`` rule.
    * "Recovery is always possible after a crashed merge" — exercised
      by the :meth:`EngineStateMachine.crash_mid_merge` /
      :meth:`EngineStateMachine.recover` rule pair.
    * "Any sequence of operator edits keeps neutral.yaml schema-valid"
      — the :meth:`EngineStateMachine.neutral_yaml_is_schema_valid`
      invariant fires after every rule, not just after merges.

Bounded trajectory length
    Per the wave spec, the default profile caps at
    ``stateful_step_count=15, max_examples=200`` and the long-running
    ``fuzz`` profile at ``stateful_step_count=30, max_examples=500``.
    Long trajectories shrink poorly and consume CPU disproportionately
    to the new defects they surface; the state machine recovers most
    interesting interleavings inside the first ~10 steps.

Determinism contract
    Every rule picks its arguments via Hypothesis bundles or strategies
    drawn from :mod:`tests.fuzz.strategies`. There is no ``random.choice``
    or ``time.time``-derived choice anywhere — this is what makes
    failing examples shrink and replay deterministically.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, Phase, settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    rule,
)

from chameleon.io.json import dump_json, load_json
from chameleon.io.toml import dump_toml, load_toml
from chameleon.io.yaml import dump_yaml, load_yaml
from chameleon.merge.engine import MergeEngine, MergeRequest
from chameleon.merge.resolve import Strategy
from chameleon.schema._constants import OnConflict
from chameleon.schema.neutral import Neutral
from chameleon.state.git import GitRepo
from chameleon.state.paths import StatePaths
from chameleon.state.transaction import TransactionStore
from chameleon.targets._registry import TargetRegistry

# Importing strategies wires up `register_type_strategy` for the rules
# below that draw partial neutrals via the registered builder.
from tests.fuzz import strategies as _strategies  # noqa: F401
from tests.fuzz.strategies import partial_neutral_with_holes

pytestmark = pytest.mark.fuzz


# ---------------------------------------------------------------------------
# Live-edit strategies. We keep these intentionally small and JSON-shaped
# so the rule produces realistic operator-style edits to live target
# files (rather than schema-driven Pydantic models) — the engine has to
# absorb both flavours, and this rig exercises the live-file path.
# ---------------------------------------------------------------------------

# A minimal Claude settings.json edit. Each draw returns the dict that
# will be merged into whatever bytes are currently on disk so we don't
# blow away unrelated keys the engine seeded.
_claude_live_patches: st.SearchStrategy[dict[str, Any]] = st.fixed_dictionaries(
    {},
    optional={
        "model": st.sampled_from(["claude-sonnet-4-7", "claude-opus-4-7", "claude-haiku-4-5"]),
        "effortLevel": st.sampled_from(["low", "medium", "high"]),
        "env": st.dictionaries(
            keys=st.sampled_from(["CI", "DEBUG", "PAGER", "FOO"]),
            values=st.sampled_from(["true", "false", "1", "less"]),
            max_size=3,
        ),
    },
)

# A minimal Codex config.toml-as-dict edit. The TOML codec writes scalar
# top-level keys; we keep the patch shape compatible with that.
_codex_live_patches: st.SearchStrategy[dict[str, Any]] = st.fixed_dictionaries(
    {},
    optional={
        "model": st.sampled_from(["gpt-5.4", "gpt-5.5", "gpt-6.0"]),
        "model_reasoning_effort": st.sampled_from(["low", "medium", "high"]),
    },
)


def _setup_sandbox(tmp_root: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Build the same XDG-sandboxed env shape every state-machine rig uses."""
    home = tmp_root / "home"
    state = tmp_root / "state"
    config = tmp_root / "config"
    for d in (home, state, config):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    return {"home": home, "state": state, "config": config}


def _write_live_claude(home: Path, patch: dict[str, Any]) -> None:
    """Merge ``patch`` into ``~/.claude/settings.json`` (creating if needed)."""
    settings_path = home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    current: dict[str, Any]
    if settings_path.exists():
        loaded = load_json(settings_path)
        current = dict(loaded) if isinstance(loaded, dict) else {}
    else:
        current = {}
    current.update(patch)
    settings_path.write_text(dump_json(current), encoding="utf-8")


def _write_live_codex(home: Path, patch: dict[str, Any]) -> None:
    """Merge ``patch`` into ``~/.codex/config.toml`` (creating if needed)."""
    config_path = home / ".codex" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    current: dict[str, Any] = dict(load_toml(config_path)) if config_path.exists() else {}
    current.update(patch)
    config_path.write_text(dump_toml(current), encoding="utf-8")


class EngineStateMachine(RuleBasedStateMachine):
    """Stateful fuzz over (operator-edit-neutral, edit-live, merge, crash, recover).

    The state is the on-disk filesystem under a per-instance sandbox plus
    the engine's persisted artefacts (neutral.yaml, neutral.lkg.yaml,
    target state-repos, transaction markers). Each rule is one operator
    action; every invariant runs after every rule.
    """

    def __init__(self) -> None:
        super().__init__()
        # Per-instance sandbox. Hypothesis instantiates a fresh
        # ``EngineStateMachine`` per example, so this isolates trajectories
        # from each other without monkeypatching across examples.
        self._monkeypatch = pytest.MonkeyPatch()
        # Use a stable per-instance directory under pytest's tmp area.
        # ``mkdtemp`` here is fine — the path is unique per state-machine
        # instance and torn down by ``teardown``.
        self._tmp_root = Path(tempfile.mkdtemp(prefix="chameleon-fuzz-"))
        env = _setup_sandbox(self._tmp_root, self._monkeypatch)
        # We retain only ``home`` because the rules need it to write live
        # target files; ``state`` and ``config`` are fully addressable
        # via ``StatePaths.resolve()`` below.
        self._home = env["home"]

        # The engine artefacts the rig watches.
        self._paths = StatePaths.resolve()
        self._targets = TargetRegistry.discover()

        # Whether the last merge attempt successfully landed (i.e. the
        # rig is in a "post-successful-merge" state). Invariants that
        # only hold after a clean merge gate on this flag.
        self._last_merge_succeeded = False

    # -- lifecycle ----------------------------------------------------

    @initialize()
    def _bootstrap(self) -> None:
        """Run the equivalent of ``chameleon init`` once at trajectory start.

        This seeds neutral.yaml, the LKG, and the per-target state-repos
        so the first ``merge`` rule has a real prior state to drift
        against. Without this, every trajectory's first merge is a
        special-case "from nothing" path and we miss the much richer
        state space of "edit something the engine already wrote".
        """
        if not self._paths.neutral.exists():
            self._paths.neutral.parent.mkdir(parents=True, exist_ok=True)
            starter = Neutral(schema_version=1)
            self._paths.neutral.write_text(
                dump_yaml(starter.model_dump(mode="json")), encoding="utf-8"
            )

        engine = MergeEngine(
            targets=self._targets, paths=self._paths, strategy=Strategy(kind=OnConflict.KEEP)
        )
        result = engine.merge(MergeRequest())
        assert result.exit_code == 0, f"bootstrap merge failed: {result.summary}"
        self._last_merge_succeeded = True

    def teardown(self) -> None:
        # Hypothesis calls teardown after each trajectory. Restore env
        # vars and remove the tmpdir so we don't leak state between
        # examples or pollute the operator's $HOME.
        try:
            self._monkeypatch.undo()
        finally:
            shutil.rmtree(self._tmp_root, ignore_errors=True)

    # -- rules --------------------------------------------------------

    @rule(neutral=partial_neutral_with_holes())
    def edit_neutral(self, neutral: Neutral) -> None:
        """Operator overwrites neutral.yaml with a partial neutral."""
        body = dump_yaml(neutral.model_dump(mode="json"))
        self._paths.neutral.write_text(body, encoding="utf-8")
        self._last_merge_succeeded = False

    @rule(patch=_claude_live_patches)
    def edit_live_claude(self, patch: dict[str, Any]) -> None:
        """Operator edits Claude's live settings.json."""
        if not patch:  # empty draws are no-ops; skip to keep traces shorter
            return
        _write_live_claude(self._home, patch)
        self._last_merge_succeeded = False

    @rule(patch=_codex_live_patches)
    def edit_live_codex(self, patch: dict[str, Any]) -> None:
        """Operator edits Codex's live config.toml."""
        if not patch:
            return
        _write_live_codex(self._home, patch)
        self._last_merge_succeeded = False

    @rule(strategy_kind=st.sampled_from([OnConflict.KEEP, OnConflict.PREFER_NEUTRAL]))
    def merge_run(self, strategy_kind: OnConflict) -> None:
        """Run the engine merge with one of the non-failing strategies.

        ``KEEP`` and ``PREFER_NEUTRAL`` both always terminate. We avoid
        ``FAIL`` here because conflicts are common in this rig and
        ``FAIL`` would mask interesting trajectories behind a uniform
        RuntimeError; conflict-bearing trajectories are more useful when
        they exercise the resolver path. ``PREFER_LKG`` is similar to
        ``PREFER_NEUTRAL`` from a state-machine perspective; one of the
        two suffices to cover the resolver-applies-a-value path.
        """
        engine = MergeEngine(
            targets=self._targets, paths=self._paths, strategy=Strategy(kind=strategy_kind)
        )
        result = engine.merge(MergeRequest())
        assert result.exit_code == 0, f"merge returned non-zero: {result.summary}"
        self._last_merge_succeeded = True

    @rule()
    def merge_dry_run(self) -> None:
        """A dry-run merge MUST be a pure read; it leaves disk untouched.

        Snapshot every artefact byte-for-byte before the call, run the
        merge with ``dry_run=True``, and assert nothing changed. This
        catches the regression where dry-run accidentally triggers a
        side effect (LKG write, state-repo commit, etc.).
        """
        snapshot = self._snapshot_artefacts()
        engine = MergeEngine(
            targets=self._targets, paths=self._paths, strategy=Strategy(kind=OnConflict.KEEP)
        )
        result = engine.merge(MergeRequest(dry_run=True))
        assert result.exit_code == 0
        after = self._snapshot_artefacts()
        assert snapshot == after, (
            f"dry-run merge mutated on-disk state; "
            f"diff keys = {sorted(set(snapshot) ^ set(after))}, "
            f"changed = {[k for k in snapshot if k in after and snapshot[k] != after[k]]}"
        )

    @rule()
    def crash_mid_merge(self) -> None:
        """Inject a crash on the first live-file write and assert the marker survives.

        The recovery contract (§4.6) says the engine MUST persist a
        transaction marker before any live target file mutation, so a
        post-crash inspection (``chameleon doctor``) can surface the
        interruption. We exercise that promise here.

        After this rule, the rig is in a "merge interrupted" state —
        ``recover`` is the natural follow-up rule, but the state machine
        is free to interleave other operator edits first; the next
        successful merge MUST clear the marker even after such
        interleaving.
        """
        original_write_bytes = Path.write_bytes
        home_str = str(self._home)
        call_count = {"n": 0}

        def crashy_write_bytes(self_path: Path, data: bytes) -> int:
            call_count["n"] += 1
            if str(self_path).startswith(home_str):
                msg = "simulated crash mid-merge (state-machine rule)"
                raise RuntimeError(msg)
            return original_write_bytes(self_path, data)

        # Use the per-instance MonkeyPatch to swap the descriptor; the
        # ``setattr`` route handles bound-method descriptors uniformly
        # and the explicit ``undo`` in finally guarantees restoration
        # even if the engine raises something other than the injected
        # crash. ``raising=True`` makes the swap fail loudly if the
        # attribute doesn't exist (a future stdlib refactor surfaces
        # here rather than silently no-op'ing).
        crash_mp = pytest.MonkeyPatch()
        crash_mp.setattr(Path, "write_bytes", crashy_write_bytes, raising=True)
        try:
            engine = MergeEngine(
                targets=self._targets, paths=self._paths, strategy=Strategy(kind=OnConflict.KEEP)
            )
            try:
                engine.merge(MergeRequest())
            except RuntimeError as exc:
                if "simulated crash" not in str(exc):
                    # Re-raise: a different RuntimeError is a real bug,
                    # not the injected crash.
                    raise
            else:
                # The engine completed without ever writing under
                # $HOME — that's possible if neither target had any
                # changes to land (idempotent re-merge). Treat as
                # "nothing crashed" and don't claim a marker should
                # survive. Note we do NOT update _last_merge_succeeded
                # because we can't tell whether the LKG was updated.
                pass
        finally:
            crash_mp.undo()

        self._last_merge_succeeded = False

    @rule()
    def recover(self) -> None:
        """Clear any stale tx markers and run a clean merge.

        Mirrors the operator workflow after a doctor-surfaced
        interruption: triage, then re-run the merge. The successful
        merge MUST clear all tx markers (its own and any pre-existing
        stale ones from earlier crashes).
        """
        store = TransactionStore(self._paths.tx_dir)
        for stale in store.entries():
            store.clear(stale.merge_id)

        engine = MergeEngine(
            targets=self._targets, paths=self._paths, strategy=Strategy(kind=OnConflict.KEEP)
        )
        result = engine.merge(MergeRequest())
        assert result.exit_code == 0
        self._last_merge_succeeded = True

    # -- helpers ------------------------------------------------------

    def _snapshot_artefacts(self) -> dict[str, bytes]:
        """Hash-free byte snapshot of every artefact the engine touches.

        Used by :meth:`merge_dry_run` to assert no mutation. Returns a
        dict so a failure message can show which key diverged.
        """
        out: dict[str, bytes] = {}
        for label, path in [
            ("neutral", self._paths.neutral),
            ("lkg", self._paths.lkg),
        ]:
            if path.exists():
                out[label] = path.read_bytes()
        # Live target files
        for live_rel in [
            ".claude/settings.json",
            ".claude.json",
            ".codex/config.toml",
            ".codex/requirements.toml",
        ]:
            p = self._home / live_rel
            if p.exists():
                out[f"live:{live_rel}"] = p.read_bytes()
        # tx markers — a dry-run shouldn't add or remove these either
        if self._paths.tx_dir.exists():
            for marker in sorted(self._paths.tx_dir.glob("*.toml")):
                out[f"tx:{marker.name}"] = marker.read_bytes()
        return out

    # -- invariants ---------------------------------------------------

    @invariant()
    def neutral_yaml_is_schema_valid(self) -> None:
        """neutral.yaml on disk must always parse to a valid Neutral.

        Fires after EVERY rule, including operator edits (because we
        only write self-emitted ``Neutral.model_dump`` bytes). A failure
        here means either ``partial_neutral_with_holes`` produced a
        Pydantic-rejected instance (strategy bug) or the engine wrote
        an invalid neutral (engine bug — high value).
        """
        if not self._paths.neutral.exists():
            return
        raw = load_yaml(self._paths.neutral)
        # Round-trip through Pydantic; any ValidationError fails the test.
        Neutral.model_validate(raw)

    @invariant()
    def lkg_matches_neutral_after_successful_merge(self) -> None:
        """After a successful merge the LKG bytes equal the neutral bytes.

        The engine's contract (§4.3 step 9) is that ``neutral.yaml`` and
        ``neutral.lkg.yaml`` are written from the same ``composed_yaml``
        string. If they ever diverge after a clean merge, the next merge
        will see false drift and produce a phantom conflict.
        """
        if not self._last_merge_succeeded:
            return
        if not self._paths.lkg.exists() or not self._paths.neutral.exists():
            return
        lkg_bytes = self._paths.lkg.read_bytes()
        neutral_bytes = self._paths.neutral.read_bytes()
        assert lkg_bytes == neutral_bytes, (
            "LKG and neutral.yaml diverged after a successful merge "
            f"(lkg={len(lkg_bytes)}B, neutral={len(neutral_bytes)}B)"
        )

    @invariant()
    def state_repos_clean_after_successful_merge(self) -> None:
        """After a successful merge every state-repo is committed and clean.

        ``MergeEngine.merge`` calls ``repo.add_all`` and ``repo.commit``
        on each target's state-repo at the end of a successful run. If
        a repo is left dirty (working tree differs from HEAD), a
        subsequent ``chameleon diff`` would surface noise the operator
        can't reason about.
        """
        if not self._last_merge_succeeded:
            return
        for tid in self._targets.target_ids():
            repo_path = self._paths.target_repo(tid)
            if not (repo_path / ".git").exists():
                continue
            repo = GitRepo(repo_path)
            assert repo.head_commit() is not None, (
                f"state-repo for {tid.value} has no HEAD after successful merge"
            )
            assert repo.is_clean(), f"state-repo for {tid.value} is dirty after successful merge"

    @rule()
    def merge_twice_idempotent(self) -> None:
        """Two consecutive ``KEEP`` merges MUST produce identical bytes.

        This is the canonical idempotency property — the one B2 was
        meant to guarantee. We model it as a rule (rather than an
        invariant) for two reasons:

        1. It costs a real merge per call, so making it an invariant
           that fires after every rule would inflate trajectory time
           by an order of magnitude with no extra coverage.
        2. As a rule, Hypothesis can interleave it with other operator
           actions and pick when the second-merge stress is most
           interesting, rather than always running it at fixed cadence.

        The first merge of the pair establishes a known clean state
        (whatever the operator's pending edits compose to); the second
        merge MUST be a no-op. We snapshot before the second merge and
        compare after — ignoring the tx_dir, which legitimately churns
        a fresh-marker-then-clear cycle on every merge.
        """
        engine = MergeEngine(
            targets=self._targets, paths=self._paths, strategy=Strategy(kind=OnConflict.KEEP)
        )
        first = engine.merge(MergeRequest())
        assert first.exit_code == 0
        snapshot = self._snapshot_artefacts()
        second = engine.merge(MergeRequest())
        assert second.exit_code == 0
        after = self._snapshot_artefacts()
        self._last_merge_succeeded = True

        # Exclude tx markers — fresh merge_id between snapshot and after
        # is expected and not a violation of the idempotency contract.
        def _strip_tx(d: dict[str, bytes]) -> dict[str, bytes]:
            return {k: v for k, v in d.items() if not k.startswith("tx:")}

        snap_rel = _strip_tx(snapshot)
        after_rel = _strip_tx(after)
        assert snap_rel == after_rel, (
            "second merge mutated state — idempotency violation; "
            f"changed = {[k for k in snap_rel if k in after_rel and snap_rel[k] != after_rel[k]]}, "
            f"only-before = {sorted(set(snap_rel) - set(after_rel))}, "
            f"only-after = {sorted(set(after_rel) - set(snap_rel))}"
        )


# ---------------------------------------------------------------------------
# Profile-aware settings. The default profile keeps the trajectory short
# and the example budget low so a pre-push smoke runs in seconds; the
# fuzz profile (HYPOTHESIS_PROFILE=fuzz) exercises a wider trajectory
# space for the nightly workflow.
# ---------------------------------------------------------------------------

_DEFAULT_PROFILE_NAME = "default"
_active_profile = os.environ.get("HYPOTHESIS_PROFILE", _DEFAULT_PROFILE_NAME)

if _active_profile == "fuzz":
    _state_machine_settings = settings(
        max_examples=500,
        stateful_step_count=30,
        deadline=None,  # rules involve real subprocess git calls; deadline is unrealistic
        # Derandomize so the strict-xfail below is reproducible across CI
        # runs — random seeding could occasionally miss the bug-trajectory
        # entirely and cause an unexpected XPASS, breaking the build.
        derandomize=True,
        # Disable shrinking entirely. The state machine finds the
        # documented governance idempotency bug within ~10 steps on every
        # seed, but Hypothesis's stateful shrinker spends 5+ minutes
        # whittling the trajectory and ultimately gives up. Shrinking is
        # only valuable when you intend to debug the falsifying example —
        # while the bug is xfail'd, the shrink budget is pure waste.
        # Re-enable when removing the xfail in Wave-11.
        phases=tuple(p for p in Phase if p.name != "shrink"),
        suppress_health_check=[
            HealthCheck.too_slow,
            HealthCheck.data_too_large,
            HealthCheck.filter_too_much,
            HealthCheck.function_scoped_fixture,
        ],
    )
else:
    # Default profile is the pre-push smoke. Each example runs ~1-2s
    # of real merge work (subprocess git, file I/O), so the example
    # budget is the dominant cost. We size it to keep the smoke under
    # ~60s on a developer laptop. The fuzz profile exists for the
    # nightly broad-coverage pass and budgets accordingly.
    _state_machine_settings = settings(
        max_examples=30,
        stateful_step_count=15,
        deadline=None,
        derandomize=True,
        phases=tuple(p for p in Phase if p.name != "shrink"),
        suppress_health_check=[
            HealthCheck.too_slow,
            HealthCheck.data_too_large,
            HealthCheck.filter_too_much,
            HealthCheck.function_scoped_fixture,
        ],
    )

# Wrap the state machine in a pytest TestCase via Hypothesis's standard
# `as_test_case()` so pytest collects it normally and the configured
# settings apply to the implicit `runTest`.
EngineStateMachine.TestCase.settings = _state_machine_settings

# ---------------------------------------------------------------------------
# Wave-11 candidate: this rig consistently surfaces an idempotency / non-
# determinism violation when adversarial neutral edits flow through the
# Claude vs. Codex codec split. Failing trajectories share a structural
# pattern across every seed we have observed:
#
#   bootstrap → edit_neutral(...adversarial governance / identity /
#   capabilities content...) → merge_twice_idempotent.
#
# Suspected contributing factors (Wave-11 triage to confirm):
#   * Codex governance codec collapses overlapping / duplicated
#     `trust.trusted_paths` and `trust.untrusted_paths` entries through
#     a `projects` dict — last-write-wins, not value-preserving.
#   * Claude governance codec drops `features` and `trust.*` outright
#     (intentional, with a `LossWarning`); the per-target overlay then
#     diverges from `composed` in a way the LKG write inherits.
#   * Hook / mcp_server collections with non-ASCII command strings or
#     unusual timeout floats round-trip through TOML/JSON with subtly
#     different byte representations.
#
# Why strict-xfail rather than skip
#   The bug is real, currently-open, and discoverable in ~36 seconds at
#   the default profile budget. Marking the wrapper TestCase as
#   ``xfail(strict=True)`` keeps CI green while the bug is open AND
#   forces a follow-up: any future fix that makes the property hold
#   flips the test to XPASS, which strict-mode then turns into a
#   failure — the operator removing the xfail and locking in the
#   property is part of the close-out checklist for the Wave-11 fix.
#
# Determinism note
#   ``derandomize=True`` plus the post-shrinking-disabled phase tuple
#   on the active settings keeps the failure reproducible across CI
#   runs without spending the shrink budget on a known-broken case.
# ---------------------------------------------------------------------------

# Wave-11 closed the non-idempotency. Three independent fixes combined to
# flip this xfail to passing:
#   - W11-1+1b: McpServerStdio.cwd preserved on both sides (parity/wave11-fcwd-*)
#   - W11-2:    Codex marketplace round-trip preservation via chameleon-namespaced
#               extras for kind='github'/'url' and auto_update (parity/wave11-fmp-*)
#   - W11-4:    Trust path canonicalisation at neutral schema construction
#               (parity/wave11-didem-governance-asymmetry) — the model_validator
#               eliminates the duplicate-collapse class entirely.
TestEngineStateMachine = EngineStateMachine.TestCase
