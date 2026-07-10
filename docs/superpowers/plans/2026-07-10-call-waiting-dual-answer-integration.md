# Call-Waiting Dual Answer — Integration (HA) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the intercom card able to answer a waiting SIP call even while the indoor monitor's video preview is up: coordinator transitions stop masking the "previewing but unanswered" phase, and the card gains auto-preview, answered-state mic gating, Mute, and correct answered-elsewhere behavior.

**Architecture:** Work happens on branch `dev/card` (the modernized card already has Answer/Decline wired to `bticino_companion.call_answer`; its gap is that `_isRinging()` dies when `call_state` flips to `active` on `stream.started`). Extract the coordinator's pure transition logic into a new HA-free `transitions.py` (plain-pytest testable), apply three rule changes there (preview-while-ringing, `call.answered mode=elsewhere`, ring-latch rehydration from the snapshot's `ringing` field), then adjust the card: mic opens only after answering, auto-preview on ring, Mute toggle, hangup that doesn't kill an indoor conversation.

**Tech Stack:** Python 3.12+ (HA custom component), plain `pytest` (new), vanilla-JS custom element.

**Spec:** `docs/superpowers/specs/2026-07-10-call-waiting-dual-answer-design.md` in the `bticino-go-companion` repo (§2, §3, §4, §6 — Integration side), at `D:/Progetti/BTicinoGO/docs/superpowers/specs/`.

## Global Constraints

- Repo: `D:/Progetti/BTicinoGO-Integration`, branch **`dev/card`** (tracking `origin/dev/card`). All commands run from the repo root.
- Test runner: `python -m pytest tests/ -v` — plain pytest, NO homeassistant import in tests (that is why `transitions.py` must not import HA).
- Server contract (shipped by the companion plan): `stream.started` during unanswered ring keeps `call_state=ringing` with `stream_state=preview`; `call.answered` may carry payload `{"mode": "elsewhere"}`; `GET /api/v2/state` has a boolean `ringing` field. The coordinator mirrors these rules for SSE updates and must stay safe against an OLD companion (rules only ever improve on today's behavior; no version checks).
- `dev/card` already gates ringing on a valid `active_entrypoint` (`_event_has_valid_entrypoint`, normalize downgrade). Preserve that gating exactly — the extraction must not weaken it.
- The `call_state` sensor keeps exactly three values (`idle`/`ringing`/`active`); NO new HA entities.
- Card: no new required config; single new optional key `auto_preview` (default `true`).
- Card manual E2E is a release gate (device required); everything else is offline.
- There is a local branch `wip/ice-candidates` (ICE-candidate + `camera/webrtc/get_client_config` fixes for the OLD card). It is NOT part of this plan; porting those fixes to the modernized card is a separate follow-up.

---

### Task 1: Extract pure transition logic into `transitions.py` + pytest scaffolding

**Files:**
- Create: `custom_components/bticino_companion/transitions.py`
- Create: `tests/conftest.py`
- Create: `tests/test_transitions.py`
- Create: `requirements-test.txt`
- Modify: `custom_components/bticino_companion/coordinator.py` — `_RING_ACTIVE_KEY` (line 33), `_apply_state_transition` (~line 347-443), `_normalize_state_payload` (~line 520-566), `_has_active_entrypoint` / `_event_has_valid_entrypoint` (~line 574-582)

**Interfaces:**
- Consumes: current `dev/card` bodies of `_apply_state_transition`, `_normalize_state_payload`, `_has_active_entrypoint` (moved verbatim in this task — behavior changes come in Tasks 2-4).
- Produces: module `transitions` with `RING_ACTIVE_KEY: str = "_ring_active"`, `has_active_entrypoint(state: dict) -> bool`, `apply_state_transition(state: dict, event_type: str, payload: Any = None) -> None`, `normalize_state_payload(state: dict) -> dict`. Tests import it via the `transitions` fixture (importlib file loading, bypassing the package `__init__` which imports homeassistant).

- [ ] **Step 1: Create the test scaffolding and parity tests (failing)**

Create `requirements-test.txt`:

```
pytest>=8
```

Create `tests/conftest.py`:

```python
"""Load transitions.py directly by path: the package __init__ imports
homeassistant, which is not installed in the plain-pytest environment."""
import importlib.util
from pathlib import Path

import pytest

_TRANSITIONS_PATH = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "bticino_companion"
    / "transitions.py"
)


@pytest.fixture(scope="session")
def transitions():
    spec = importlib.util.spec_from_file_location("transitions", _TRANSITIONS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
```

Create `tests/test_transitions.py` with parity tests for CURRENT `dev/card` behavior (including the entrypoint gating):

```python
def test_ring_started_with_entrypoint_sets_ringing(transitions):
    state = {"active_entrypoint": "main"}
    transitions.apply_state_transition(state, "ring.started")
    assert state["call_state"] == "ringing"
    assert state[transitions.RING_ACTIVE_KEY] is True


def test_ring_started_without_entrypoint_is_ignored(transitions):
    state = {}
    transitions.apply_state_transition(state, "ring.started")
    assert "call_state" not in state


def test_call_ended_without_stream_or_ring_resets(transitions):
    state = {"call_state": "active", "stream_state": "active"}
    transitions.apply_state_transition(state, "call.ended")
    assert state["call_state"] == "idle"
    assert state["stream_state"] == "idle"


def test_normalize_defaults(transitions):
    normalized = transitions.normalize_state_payload({})
    assert normalized["call_state"] == "idle"
    assert normalized["active_entrypoint"] == "none"
    assert normalized["stream_state"] == "idle"
    assert normalized[transitions.RING_ACTIVE_KEY] is False
    assert "ringing" not in normalized


def test_normalize_downgrades_ringing_without_entrypoint(transitions):
    normalized = transitions.normalize_state_payload({"call_state": "ringing"})
    assert normalized["call_state"] == "idle"
    assert normalized[transitions.RING_ACTIVE_KEY] is False
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pip install -r requirements-test.txt` then `python -m pytest tests/ -v`
Expected: ERROR — `transitions.py` does not exist yet (`FileNotFoundError` from the fixture).

- [ ] **Step 3: Create `transitions.py` (verbatim move of the dev/card logic)**

Create `custom_components/bticino_companion/transitions.py`. This is the CURRENT `dev/card` logic moved unchanged, with `_RING_ACTIVE_KEY` → `RING_ACTIVE_KEY` and `CompanionCoordinator._event_has_valid_entrypoint(state)` / `._has_active_entrypoint(...)` → `has_active_entrypoint(...)`:

```python
"""Pure state-transition logic for the BTicino Companion coordinator.

No Home Assistant imports: this module is unit-tested with plain pytest.
"""
from __future__ import annotations

from typing import Any

RING_ACTIVE_KEY = "_ring_active"


def has_active_entrypoint(state: dict[str, Any]) -> bool:
    value = str(state.get("active_entrypoint", "")).strip()
    return bool(value and value != "none")


def apply_state_transition(state: dict[str, Any], event_type: str, payload: Any = None) -> None:
    stream_active = bool(state.get("stream_active"))
    ring_active = bool(state.get(RING_ACTIVE_KEY))
    if not ring_active and str(state.get("call_state", "")).strip().lower() == "ringing":
        ring_active = True
    audio = state.get("audio")
    if not isinstance(audio, dict):
        audio = {"muted": False}
        state["audio"] = audio

    if event_type in ("ring.started", "call.incoming"):
        if not has_active_entrypoint(state):
            return
        state[RING_ACTIVE_KEY] = True
        state["call_state"] = "ringing"
        return

    if event_type == "ring.ended":
        state[RING_ACTIVE_KEY] = False
        state["call_state"] = "active" if stream_active else "idle"
        return

    if event_type == "call.answered":
        state["call_state"] = "active"
        state["talk_enabled"] = True
        if state.get("stream_state") == "preview":
            state["stream_state"] = "active"
            state["stream_active"] = True
        return

    if event_type == "call.ended":
        if stream_active:
            state["call_state"] = "active"
        elif ring_active:
            state["call_state"] = "ringing"
        else:
            state["call_state"] = "idle"
            state["stream_state"] = "idle"
            state["talk_enabled"] = False
        return

    if event_type == "preview.started":
        if not has_active_entrypoint(state):
            return
        state["stream_state"] = "preview"
        state["stream_active"] = False
        state["talk_enabled"] = False
        if str(state.get("call_state", "")).strip().lower() in ("", "idle"):
            state["call_state"] = "ringing"
        return

    if event_type == "preview.stopped":
        if state.get("stream_state") == "preview":
            state["stream_state"] = "idle"
        state["talk_enabled"] = False
        if not ring_active and not bool(state.get("stream_active")):
            state["call_state"] = "idle"
        return

    if event_type == "stream.started":
        state["stream_active"] = True
        state["stream_state"] = "active"
        state["call_state"] = "active"
        return

    if event_type == "stream.stopped":
        state["stream_active"] = False
        state["stream_state"] = "idle"
        state["talk_enabled"] = False
        state["call_state"] = "ringing" if ring_active else "idle"
        return

    if event_type == "audio.muted":
        audio["muted"] = True
        return

    if event_type == "audio.unmuted":
        audio["muted"] = False
        return

    voicemail = state.get("voicemail")
    if not isinstance(voicemail, dict):
        voicemail = {"enabled": False, "welcome_message_enabled": False}
        state["voicemail"] = voicemail

    if event_type == "voicemail.enabled":
        voicemail["enabled"] = True
        if isinstance(payload, dict) and "welcome_message_enabled" in payload:
            voicemail["welcome_message_enabled"] = bool(payload.get("welcome_message_enabled", False))
        return

    if event_type == "voicemail.disabled":
        voicemail["enabled"] = False
        if isinstance(payload, dict) and "welcome_message_enabled" in payload:
            voicemail["welcome_message_enabled"] = bool(payload.get("welcome_message_enabled", False))
        return


def normalize_state_payload(state: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(state or {})
    active = normalized.get("active_entrypoint")
    if not isinstance(active, str) or not active.strip():
        normalized["active_entrypoint"] = "none"
    else:
        normalized["active_entrypoint"] = active.strip()
    if not isinstance(normalized.get("call_state"), str) or not str(normalized.get("call_state", "")).strip():
        normalized["call_state"] = "idle"
    else:
        normalized["call_state"] = str(normalized.get("call_state", "")).strip().lower()
    normalized["stream_active"] = bool(normalized.get("stream_active", False))
    stream_state = normalized.get("stream_state")
    if not isinstance(stream_state, str) or not stream_state.strip():
        normalized["stream_state"] = "active" if normalized["stream_active"] else "idle"
    else:
        normalized_stream_state = stream_state.strip().lower()
        if normalized_stream_state not in ("idle", "preview", "active"):
            normalized_stream_state = "active" if normalized["stream_active"] else "idle"
        normalized["stream_state"] = normalized_stream_state
    normalized["talk_enabled"] = bool(normalized.get("talk_enabled", False))
    ring_active = bool(normalized.get(RING_ACTIVE_KEY))
    if normalized["call_state"] == "ringing":
        ring_active = True
    if ring_active and not has_active_entrypoint(normalized):
        ring_active = False
        if normalized["call_state"] == "ringing":
            normalized["call_state"] = "idle"
    normalized[RING_ACTIVE_KEY] = ring_active
    normalized.pop("ringing", None)
    audio = normalized.get("audio")
    if not isinstance(audio, dict):
        audio = {}
    normalized["audio"] = {
        "muted": bool(audio.get("muted", False)),
    }
    voicemail = normalized.get("voicemail")
    if not isinstance(voicemail, dict):
        voicemail = {}
    normalized["voicemail"] = {
        "enabled": bool(voicemail.get("enabled", False)),
        "welcome_message_enabled": bool(voicemail.get("welcome_message_enabled", False)),
    }
    return normalized
```

- [ ] **Step 4: Delegate from the coordinator**

In `custom_components/bticino_companion/coordinator.py`:

(a) Add to the relative imports (after the `.const` import block, ~line 29):

```python
from .transitions import (
    RING_ACTIVE_KEY,
    apply_state_transition,
    has_active_entrypoint,
    normalize_state_payload,
)
```

(b) Replace line 33 `_RING_ACTIVE_KEY = "_ring_active"` with:

```python
_RING_ACTIVE_KEY = RING_ACTIVE_KEY
```

(c) Replace the whole body of `_apply_state_transition` with a delegate:

```python
    @staticmethod
    def _apply_state_transition(state: dict[str, Any], event_type: str, payload: Any = None) -> None:
        apply_state_transition(state, event_type, payload)
```

(d) Replace the whole body of `_normalize_state_payload` with a delegate:

```python
    @staticmethod
    def _normalize_state_payload(state: dict[str, Any]) -> dict[str, Any]:
        return normalize_state_payload(state)
```

(e) Replace the body of `_has_active_entrypoint` with a delegate (keep `_event_has_valid_entrypoint` and `_is_ringing_state` as they are — they call `_has_active_entrypoint`):

```python
    @staticmethod
    def _has_active_entrypoint(state: dict[str, Any]) -> bool:
        return has_active_entrypoint(state)
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/ -v`
Expected: 5 PASS.

- [ ] **Step 6: Commit**

```bash
git add custom_components/bticino_companion/transitions.py custom_components/bticino_companion/coordinator.py tests/ requirements-test.txt
git commit -m "refactor: extract pure state transitions into transitions.py with pytest coverage"
```

---

### Task 2: `stream.started` during an unanswered ring becomes `preview`

**Files:**
- Modify: `custom_components/bticino_companion/transitions.py` (the `stream.started` branch)
- Test: `tests/test_transitions.py`

**Interfaces:**
- Consumes: `ring_active` local already computed at the top of `apply_state_transition`.
- Produces: SSE rule mirrored from the companion reducer — during an unanswered ring, `stream.started` yields `stream_state="preview"`, `stream_active=True`, `talk_enabled=False`, `call_state` stays `"ringing"`. The card's `_isRinging()` (Task 5) relies on `call_state` staying `ringing` here.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_transitions.py`:

```python
def test_stream_started_during_unanswered_ring_is_preview(transitions):
    state = {
        "call_state": "ringing",
        "active_entrypoint": "main",
        transitions.RING_ACTIVE_KEY: True,
    }
    transitions.apply_state_transition(state, "stream.started")
    assert state["stream_state"] == "preview"
    assert state["stream_active"] is True
    assert state["call_state"] == "ringing"
    assert state["talk_enabled"] is False


def test_stream_started_outside_ring_stays_active(transitions):
    state = {"call_state": "idle"}
    transitions.apply_state_transition(state, "stream.started")
    assert state["stream_state"] == "active"
    assert state["call_state"] == "active"
    assert state["stream_active"] is True
```

- [ ] **Step 2: Run to verify the first fails**

Run: `python -m pytest tests/test_transitions.py -v -k stream_started`
Expected: `test_stream_started_during_unanswered_ring_is_preview` FAILS (`stream_state == "active"`); the second PASSES.

- [ ] **Step 3: Implement**

In `transitions.py`, replace the `stream.started` branch:

```python
    if event_type == "stream.started":
        state["stream_active"] = True
        if ring_active and str(state.get("call_state", "")).strip().lower() == "ringing":
            # Video is flowing but nobody answered (indoor preview or card
            # auto-preview): do not mask the ringing phase.
            state["stream_state"] = "preview"
            state["talk_enabled"] = False
            return
        state["stream_state"] = "active"
        state["call_state"] = "active"
        return
```

- [ ] **Step 4: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add custom_components/bticino_companion/transitions.py tests/test_transitions.py
git commit -m "coordinator: stream during unanswered ring maps to preview, keeps ringing"
```

---

### Task 3: `call.answered` — clear the ring latch, honor `mode=elsewhere`

**Files:**
- Modify: `custom_components/bticino_companion/transitions.py` (the `call.answered` branch)
- Test: `tests/test_transitions.py`

**Interfaces:**
- Consumes: event payload dict (`payload` parameter, may be `None`).
- Produces: `call.answered` with `payload["mode"]=="elsewhere"` → `call_state="active"`, `talk_enabled=False`, ring latch cleared, preview promoted. Otherwise same but `talk_enabled=True`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_transitions.py`:

```python
def test_call_answered_elsewhere_keeps_talk_disabled(transitions):
    state = {
        "call_state": "ringing",
        "stream_state": "preview",
        "active_entrypoint": "main",
        transitions.RING_ACTIVE_KEY: True,
    }
    transitions.apply_state_transition(state, "call.answered", {"mode": "elsewhere"})
    assert state["call_state"] == "active"
    assert state["talk_enabled"] is False
    assert state[transitions.RING_ACTIVE_KEY] is False
    assert state["stream_state"] == "active"
    assert state["stream_active"] is True


def test_call_answered_locally_enables_talk_and_clears_latch(transitions):
    state = {"call_state": "ringing", "active_entrypoint": "main", transitions.RING_ACTIVE_KEY: True}
    transitions.apply_state_transition(state, "call.answered")
    assert state["call_state"] == "active"
    assert state["talk_enabled"] is True
    assert state[transitions.RING_ACTIVE_KEY] is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_transitions.py -v -k call_answered`
Expected: both FAIL (`talk_enabled` True in the first, latch still True in both).

- [ ] **Step 3: Implement**

In `transitions.py`, replace the `call.answered` branch:

```python
    if event_type == "call.answered":
        state["call_state"] = "active"
        state[RING_ACTIVE_KEY] = False
        mode = payload.get("mode") if isinstance(payload, dict) else None
        state["talk_enabled"] = mode != "elsewhere"
        if state.get("stream_state") == "preview":
            state["stream_state"] = "active"
            state["stream_active"] = True
        return
```

- [ ] **Step 4: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add custom_components/bticino_companion/transitions.py tests/test_transitions.py
git commit -m "coordinator: call.answered clears ring latch and supports mode=elsewhere"
```

---

### Task 4: Rehydrate the ring latch from the snapshot's `ringing` field

**Files:**
- Modify: `custom_components/bticino_companion/transitions.py` (`normalize_state_payload`)
- Test: `tests/test_transitions.py`

**Interfaces:**
- Consumes: the companion's `/api/v2/state` top-level boolean `ringing` (companion plan, Task 3 there).
- Produces: after normalization, `RING_ACTIVE_KEY` is True whenever the snapshot said `ringing: true` AND an entrypoint is active (the existing `dev/card` downgrade still wins without one). The raw `ringing` key is still stripped (entities unchanged).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_transitions.py`:

```python
def test_normalize_rehydrates_ring_latch_from_snapshot_ringing(transitions):
    normalized = transitions.normalize_state_payload(
        {"call_state": "ringing", "ringing": True, "active_entrypoint": "main"}
    )
    assert normalized[transitions.RING_ACTIVE_KEY] is True
    assert "ringing" not in normalized

    # ringing field alone (call_state drifted): latch still rehydrates
    normalized = transitions.normalize_state_payload(
        {"call_state": "idle", "ringing": True, "active_entrypoint": "main"}
    )
    assert normalized[transitions.RING_ACTIVE_KEY] is True

    # without an entrypoint the existing downgrade still wins
    normalized = transitions.normalize_state_payload({"call_state": "idle", "ringing": True})
    assert normalized[transitions.RING_ACTIVE_KEY] is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_transitions.py -v -k rehydrates`
Expected: FAIL on the second block (latch False when only `ringing` is set).

- [ ] **Step 3: Implement**

In `normalize_state_payload`, replace the latch block:

```python
    ring_active = bool(normalized.get(RING_ACTIVE_KEY))
    if normalized["call_state"] == "ringing":
        ring_active = True
    if bool(normalized.get("ringing")):
        ring_active = True
    if ring_active and not has_active_entrypoint(normalized):
        ring_active = False
        if normalized["call_state"] == "ringing":
            normalized["call_state"] = "idle"
    normalized[RING_ACTIVE_KEY] = ring_active
    normalized.pop("ringing", None)
```

- [ ] **Step 4: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add custom_components/bticino_companion/transitions.py tests/test_transitions.py
git commit -m "coordinator: rehydrate ring latch from snapshot ringing field"
```

---

### Task 5: Card — answered-state mic gating, auto-preview, Mute, safe hangup

The modernized card (`dev/card`) already has: ring overlay + Answer/Decline calling `call_answer`/`call_hangup`, `_isRinging()` gated on the ringing sensor + `call_state` + entrypoint match, and video kept visible while `call_state=active` with a live PeerConnection. What it gets wrong for this feature: (1) the mic auto-opens on ANY connected session (`_startWebRTC` line 120 and 146) — including pure viewing and answered-elsewhere; (2) no auto-preview on ring; (3) no Mute; (4) `_answer()` refuses to run when a PeerConnection already exists (`if (this._connecting || this._pc) return`), which breaks answering during auto-preview; (5) Hang up always calls `call_hangup`, which would interfere while the indoor monitor owns the call.

**Files:**
- Modify: `custom_components/bticino_companion/www/bticino-go-intercom-card.js`

**Interfaces:**
- Consumes: `_isRinging()` (line 56-58), `_startWebRTC()` (97-150), `_answer()` (176-190), `_hangup()` (200-206), `_cleanup()` (234-256), `_updateDynamicParts()` (346-415), `_bindEvents()` (437-443). HA services `bticino_companion.call_answer` / `call_hangup`.
- Produces: new internal fields `_answered`, `_autoStarted`, `_muted`, `_lastRinging`, `_lastCallState`; new methods `_onCallPhaseChanged()`, `_toggleMute()`; new `mute` button; new optional config key `auto_preview` (default true). Mic contract: track enabled ⇔ `connected && _answered && !_muted`.

No JS unit-test infra exists; verified by `node --check` + manual E2E (Task 7).

- [ ] **Step 1: Add the phase state fields**

In the constructor (lines 2-16), after `this._error = "";` add:

```javascript
    this._answered = false;
    this._autoStarted = false;
    this._muted = false;
    this._lastRinging = false;
    this._lastCallState = "idle";
```

- [ ] **Step 2: Gate the mic on the answered state**

In `_startWebRTC` replace the `onconnectionstatechange` handler (lines 118-123) with:

```javascript
    pc.onconnectionstatechange = () => {
      this._connected = ["connected", "completed"].includes(pc.connectionState);
      if (this._micTrack) this._micTrack.enabled = this._connected && this._answered && !this._muted;
      if (["failed", "closed", "disconnected"].includes(pc.connectionState)) this._talking = false;
      this._updateDynamicParts();
    };
```

and replace line 146 `if (this._micTrack) this._micTrack.enabled = true;` with:

```javascript
    if (this._micTrack) this._micTrack.enabled = this._answered && !this._muted;
```

- [ ] **Step 3: Auto-preview + teardown via a phase hook**

Replace the `set hass` setter (lines 25-32) with:

```javascript
  set hass(hass) {
    this._hass = hass;
    this._onCallPhaseChanged();
    if (this.shadowRoot?.querySelector("ha-card")) {
      this._updateDynamicParts();
      return;
    }
    this._render();
  }
```

Add after `_matchesEntrypoint()` (line 62):

```javascript
  _onCallPhaseChanged() {
    const ringing = this._isRinging();
    const callState = this._stateText(this._config.call_state, "idle");
    const wasRinging = this._lastRinging;
    const prevCallState = this._lastCallState;
    this._lastRinging = ringing;
    this._lastCallState = callState;
    const autoPreview = this._config?.auto_preview !== false;
    if (ringing && !wasRinging && autoPreview && !this._pc && !this._connecting) {
      // Ring started: open the video in view-only mode (mic stays off until
      // Answer), mirroring the indoor monitor preview.
      this._autoStarted = true;
      this._start();
      return;
    }
    if (callState === "idle" && prevCallState !== "idle") {
      this._answered = false;
      this._muted = false;
      if (this._autoStarted && this._pc) {
        this._autoStarted = false;
        this._cleanup();
        this._render();
      }
    }
  }
```

- [ ] **Step 4: Make Answer work while the preview session is already up**

Replace `_answer()` (lines 176-190) with:

```javascript
  async _answer() {
    if (this._connecting || this._answered) return;
    this._error = "";
    try {
      await this._callService("bticino_companion", "call_answer", {});
      this._answered = true;
      this._muted = false;
      if (!this._pc) {
        this._connecting = true;
        this._render();
        await this._startWebRTC();
      } else if (this._micTrack) {
        // Preview session already running: just open the mic.
        this._micTrack.enabled = this._connected;
      }
    } catch (err) {
      // Typical race: 409 no_incoming_call (indoor monitor answered first,
      // or the 60s incoming window expired). Non-blocking message.
      this._error = err?.message || String(err);
    }
    this._connecting = false;
    this._updateDynamicParts();
  }
```

- [ ] **Step 5: Manual Call keeps hands-free; Hang up only signals when we own the call**

In `_bindEvents()` (line 438), change the `call` binding so a user-initiated call opens the mic (previous hands-free behavior):

```javascript
    this.shadowRoot.querySelector('[data-action="call"]')?.addEventListener("click", () => { this._answered = true; this._start(); });
```

Replace `_hangup()` (lines 200-206) with:

```javascript
  async _hangup() {
    if (this._answered) {
      try {
        await this._callService("bticino_companion", "call_hangup", {});
      } catch {}
    }
    this._answered = false;
    this._muted = false;
    this._autoStarted = false;
    this._cleanup();
    this._render();
  }
```

Also reset the flags in `_cleanup()` — after `this._talking = false;` (line 239) add:

```javascript
    this._muted = false;
```

(`_answered` is deliberately NOT reset in `_cleanup()`: `_answer()` may call `_startWebRTC` after setting it.)

- [ ] **Step 6: Add the Mute button**

In `_render()`, inside `<div class="controls">` (after the hangup button, line ~335), add:

```html
          <button class="btn btn-unlock" data-action="mute" title="Mute">
            <svg viewBox="0 0 24 24"><path d="M12,2A3,3 0 0,1 15,5V11A3,3 0 0,1 12,14A3,3 0 0,1 9,11V5A3,3 0 0,1 12,2M19,11C19,14.53 16.39,17.44 13,17.93V21H11V17.93C7.61,17.44 5,14.53 5,11H7A5,5 0 0,0 12,16A5,5 0 0,0 17,11H19Z"/></svg>
          </button>
```

Add the method after `_setTalk` (line ~221):

```javascript
  _toggleMute() {
    if (!this._micTrack || !this._answered) return;
    this._muted = !this._muted;
    this._micTrack.enabled = this._connected && !this._muted;
    this._updateDynamicParts();
  }
```

In `_bindEvents()` add:

```javascript
    this.shadowRoot.querySelector('[data-action="mute"]')?.addEventListener("click", () => this._toggleMute());
```

In `_updateDynamicParts()`, next to the other `_setActionVisible` calls (lines 404-412), add:

```javascript
    this._setActionVisible("mute", showActive && this._answered);
    const muteBtn = this.shadowRoot.querySelector('[data-action="mute"]');
    if (muteBtn) {
      muteBtn.title = this._muted ? "Unmute" : "Mute";
      muteBtn.style.background = this._muted ? "#c62828" : "";
      muteBtn.style.color = this._muted ? "#fff" : "";
      muteBtn.disabled = !this._micTrack;
    }
```

- [ ] **Step 7: Syntax check and commit**

Run: `node --check custom_components/bticino_companion/www/bticino-go-intercom-card.js`
Expected: no output (syntax OK).

```bash
git add custom_components/bticino_companion/www/bticino-go-intercom-card.js
git commit -m "card: auto-preview on ring, mic gated on answered state, mute, safe hangup"
```

---

### Task 6: README — document the card and the new semantics

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a card section**

Add an "Intercom card" section to `README.md` documenting: how to register the card resource (`/hacsfiles/bticino_companion/bticino-go-intercom-card.js` or local path), a config example:

```yaml
type: custom:bticino-go-intercom-card
entry_id: <config entry id>
entrypoint_id: main
camera: camera.bticino_main
call_state: sensor.bticino_call_state
ringing: binary_sensor.bticino_ringing
active_entrypoint: sensor.bticino_active_entrypoint
auto_preview: true   # optional, default true: start video automatically on ring
```

and the behavior: on ring the card auto-starts a view-only video preview (mic off) and shows Answer/Decline; Answer takes the same SIP call the indoor monitor is ringing on (hands-free audio, Mute, Hang up); if the indoor monitor answers first the card keeps showing video with the mic off until the call ends. Also update the "Call State" description (README.md ~line 97): during an unanswered ring with video preview, `call_state` stays `ringing` and `stream_state` reads `preview`.

- [ ] **Step 2: Run tests and commit**

Run: `python -m pytest tests/ -v`
Expected: PASS.

```bash
git add README.md
git commit -m "docs: document intercom card and preview-while-ringing semantics"
```

---

### Task 7: Release gate — manual E2E on a live setup (BLOCKED until device available)

**Files:** none (operational task). Requires the companion build from the companion plan deployed on the intercom.

- [ ] **Step 1: Scenario walkthrough**

With the updated integration installed in HA and the card configured:

1. Ring the outdoor unit → card auto-starts video preview within ~2s (mic OFF), Answer/Decline visible, `sensor.call_state` = `ringing`, `binary_sensor.ringing` = on.
2. Turn on the indoor monitor preview WITHOUT answering → card still shows Answer; `sensor.stream_state` = `preview`; `sensor.call_state` stays `ringing`.
3. Press Answer on the card → two-way audio card↔outdoor unit; Mute toggles the mic; Hang up ends the call and the card returns to idle.
4. Repeat the ring, answer on the indoor monitor → Answer disappears, card keeps showing video, mic stays off; when the indoor conversation ends the card returns to idle.
5. Repeat the ring, let it expire (60s) → card returns to idle.
6. Press Answer simultaneously with the indoor monitor → loser gets a non-blocking error message and the card re-aligns.

- [ ] **Step 2: Unblock the release**

Only when all six pass: release the integration update (HACS tag) — coordinated with the companion `v*` release.
