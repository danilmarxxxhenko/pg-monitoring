"""Chaos mode state shared by shoppers and background jobs."""
from __future__ import annotations

import logging

from .metrics import CHAOS_MODE

log = logging.getLogger(__name__)

MODES = (
    "off",
    "slow_queries",
    "lock_contention",
    "traffic_spike",
    "brute_force",
    "payment_failures",
)


class ChaosState:
    def __init__(self) -> None:
        self.mode = "off"
        self._publish()

    def set(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"unknown chaos mode {mode!r}, expected one of {MODES}")
        previous, self.mode = self.mode, mode
        self._publish()
        log.warning("chaos mode changed", extra={"chaos_from": previous, "chaos_to": mode})

    def _publish(self) -> None:
        for m in MODES:
            CHAOS_MODE.labels(m).set(1 if m == self.mode else 0)

    @property
    def rps_multiplier(self) -> float:
        return 10.0 if self.mode == "traffic_spike" else 1.0


chaos = ChaosState()
