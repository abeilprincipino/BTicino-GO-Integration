"""Shared Companion entity behavior."""

from __future__ import annotations


class CompanionAvailabilityMixin:
    """Make entity availability follow the push transport.

    An entity that works through the intercom itself (a door, a camera, the
    ringer) sets ``_requires_intercom``: it is also unavailable while the
    Companion reports the intercom firmware down, such as during the reload
    that follows a ringer unmute, and comes back when the firmware answers.
    """

    _requires_intercom = False

    @property
    def available(self) -> bool:
        if not self.coordinator.runtime.connected:
            return False
        if not self._requires_intercom:
            return True
        state = self.coordinator.data
        return not (state and state.intercom_unavailable)
