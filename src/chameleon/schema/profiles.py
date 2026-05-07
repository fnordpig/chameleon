"""Named overlay profiles (sibling of the eight domains, not a domain itself).

A Profile re-specifies any subset of any domain (or pass-through). At
merge time, a profile is applied as: base + overlay, where unset fields
in the overlay leave the base alone. Codecs run against the resolved
result, never against the raw overlay.

V0 ships profiles as schema-and-storage only; the `chameleon profile use`
command that activates an overlay is deferred.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from chameleon.schema.authorization import Authorization
from chameleon.schema.capabilities import Capabilities
from chameleon.schema.directives import Directives
from chameleon.schema.environment import Environment
from chameleon.schema.governance import Governance
from chameleon.schema.identity import Identity
from chameleon.schema.interface import Interface
from chameleon.schema.lifecycle import Lifecycle


class Profile(BaseModel):
    """A named overlay; every domain is optional (overlay-only)."""

    model_config = ConfigDict(extra="forbid")

    identity: Identity | None = None
    directives: Directives | None = None
    capabilities: Capabilities | None = None
    authorization: Authorization | None = None
    environment: Environment | None = None
    lifecycle: Lifecycle | None = None
    interface: Interface | None = None
    governance: Governance | None = None


__all__ = ["Profile"]
