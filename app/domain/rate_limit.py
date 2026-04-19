"""Rate-limit value objects.

Pure domain — no Redis, no asyncio, no logging. Used by the
application-layer ``evaluate`` service and the infrastructure-layer
``RateLimitGate`` implementations.

See ``docs/36-rate-limiting.md`` §5.1 for the design rationale.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class LimitLayer(str, Enum):
    """Stable identifier for each layer (matches log/metric labels in §7)."""

    USER_BURST = "user_burst"
    USER_HOURLY = "user_hourly"
    CHAT_BURST = "chat_burst"
    GLOBAL_BURST = "global_burst"
    DOMAIN_BURST = "domain_burst"

    @property
    def user_facing_category(self) -> str:
        """Coarse category exposed to the user (§4.1)."""
        if self in (LimitLayer.USER_BURST, LimitLayer.USER_HOURLY):
            return "personal"
        if self is LimitLayer.CHAT_BURST:
            return "chat"
        if self is LimitLayer.GLOBAL_BURST:
            return "service-wide"
        return "platform"


@dataclass(frozen=True, slots=True)
class LimitWindow:
    """A single (limit, window) policy. Immutable, comparable, log-safe."""

    layer: LimitLayer
    limit: int
    window_s: int

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError(f"LimitWindow.limit must be >= 1 (got {self.limit})")
        if self.window_s < 1:
            raise ValueError(f"LimitWindow.window_s must be >= 1 (got {self.window_s})")


@dataclass(frozen=True, slots=True)
class LimitDecision:
    """The result of one or more gate checks.

    ``allowed=True``  → all checked layers passed; ``layer`` is ``None``.
    ``allowed=False`` → first denying layer is recorded in ``layer`` and
                       ``retry_after_s`` carries the operator-friendly hint
                       (already ceil()'d to whole seconds).
    """

    allowed: bool
    layer: LimitLayer | None
    retry_after_s: int

    @classmethod
    def allow(cls) -> LimitDecision:
        return cls(allowed=True, layer=None, retry_after_s=0)

    @classmethod
    def deny(cls, layer: LimitLayer, retry_after_s: int) -> LimitDecision:
        return cls(allowed=False, layer=layer, retry_after_s=max(1, retry_after_s))
