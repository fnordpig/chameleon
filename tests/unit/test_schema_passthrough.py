from __future__ import annotations

from chameleon.schema.passthrough import PassThroughBag


def test_passthrough_bag_stores_values() -> None:
    bag = PassThroughBag(items={"voice": {"enabled": True, "mode": "tap"}})
    assert bag.items["voice"] == {"enabled": True, "mode": "tap"}


def test_passthrough_bag_empty() -> None:
    bag = PassThroughBag()
    assert bag.items == {}


def test_passthrough_round_trips() -> None:
    bag = PassThroughBag(items={"a": [1, "two", None], "b": {"nested": True}})
    dumped = bag.model_dump(mode="json")
    restored = PassThroughBag.model_validate(dumped)
    assert restored == bag
