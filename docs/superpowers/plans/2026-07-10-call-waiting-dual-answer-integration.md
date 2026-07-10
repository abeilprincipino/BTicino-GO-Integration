# Call-Waiting Dual Answer — Integration (HA) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Lovelace intercom card able to answer a waiting SIP call (auto-preview on ring, Answer with full-duplex mic, keep viewing when the indoor monitor answers), backed by coordinator state transitions that no longer mask the "previewing but unanswered" phase.

**Architecture:** Extract the coordinator's pure state-transition logic into a new HA-free module `transitions.py` (unit-testable with plain pytest), then apply three rule changes there (preview-while-ringing, `call.answered mode=elsewhere`, latch rehydration from the snapshot's new `ringing` field). The card gains a `call_state`-driven phase machine: auto-preview on ring, Answer/Mute/Hang Up controls, teardown on idle.

**Tech Stack:** Python 3.12+ (HA custom component), plain `pytest` (new, no HA test harness needed), vanilla-JS custom element (the card).

**Spec:** `docs/superpowers/specs/2026-07-10-call-waiting-dual-answer-design.md` in the `bticino-go-companion` repo (§2, §3, §4, §6 — Integration side). Copy available from the companion repo at `D:/Progetti/BTicinoGO/docs/superpowers/specs/`.

## Global Constraints

- Repo: `D:/Progetti/BTicinoGO-Integration`. All commands run from this repo root.
- Test runner: `python -m pytest tests/ -v` — plain pytest, NO homeassistant import in tests (that is why `transitions.py` must not import HA).
- Server contract (already shipped by the companion plan): `stream.started` during unanswered ring keeps `call_state=ringing` with `stream_state=preview`; `call.answered` may carry payload `{"mode": "elsewhere"}`; `GET /api/v2/state` has a boolean `ringing` field. The coordinator must mirror these rules for SSE-driven updates and stay correct even against an OLD companion (the rules below only ever *improve* on today's behavior; no companion-version checks).
- The `call_state` sensor keeps exactly three values (`idle`/`ringing`/`active`); NO new HA entities.
- Card: no new required config; single new optional key `auto_preview` (default `true`).
- Card manual E2E test is a release gate (device required); everything else is offline.

---

### Task 1: Extract pure transition logic into `transitions.py` + pytest scaffolding

**Files:**
- Create: `custom_components/bticino_companion/transitions.py`
- Create: `tests/conftest.py`
- Create: `tests/test_transitions.py`
- Create: `requirements-test.txt`
- Modify: `custom_components/bticino_companion/coordinator.py:33` (`_RING_ACTIVE_KEY`), `:349-441` (`_apply_state_transition`), `:520-560` (`_normalize_state_payload`)

**Interfaces:**
- Consumes: current bodies of `CompanionCoordinator._apply_state_transition` and `._normalize_state_payload` (moved verbatim in this task — behavior change comes in Tasks 2-4).
- Produces: module `transitions` with `RING_ACTIVE_KEY: str = "_ring_active"`, `apply_state_transition(state: dict, event_type: str, payload: Any = None) -> None`, `normalize_state_payload(state: dict) -> dict`. Tests import it via the `transitions` fixture (importlib file loading, bypassing the package `__init__` which imports homeassistant).

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

Create `tests/test_transitions.py` with parity tests for CURRENT behavior:

```python
def test_ring_started_sets_ringing(transitions):
    state = {}
    transitions.apply_state_transition(state, "ring.started")
    assert state["call_state"] == "ringing"
    assert state[transitions.RING_ACTIVE_KEY] is True


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
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pip install -r requirements-test.txt` then `python -m pytest tests/ -v`
Expected: ERROR — `transitions.py` does not exist yet (`FileNotFoundError` from the fixture).

- [ ] **Step 3: Create `transitions.py` (verbatim move)**

Create `custom_components/bticino_companion/transitions.py` by moving the two static-method bodies out of `coordinator.py` unchanged (only `_RING_ACTIVE_KEY` → `RING_ACTIVE_KEY`):

```python
"""Pure state-transition logic for the BTicino Companion coordinator.

No Home Assistant imports: this module is unit-tested with plain pytest.
"""
from __future__ import annotations

from typing import Any

RING_ACTIVE_KEY = "_ring_active"


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

(a) Add to the relative imports (after the `.const` import block, line ~29):

```python
from .transitions import (
    RING_ACTIVE_KEY,
    apply_state_transition,
    normalize_state_payload,
)
```

(b) Replace line 33 `_RING_ACTIVE_KEY = "_ring_active"` with:

```python
_RING_ACTIVE_KEY = RING_ACTIVE_KEY
```

(c) Replace the whole body of `_apply_state_transition` (lines 349-441) with a delegate:

```python
    @staticmethod
    def _apply_state_transition(state: dict[str, Any], event_type: str, payload: Any = None) -> None:
        apply_state_transition(state, event_type, payload)
```

(d) Replace the whole body of `_normalize_state_payload` (lines 520-560) with a delegate:

```python
    @staticmethod
    def _normalize_state_payload(state: dict[str, Any]) -> dict[str, Any]:
        return normalize_state_payload(state)
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/ -v`
Expected: 3 PASS.

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
- Produces: SSE rule mirrored from the companion reducer — during an unanswered ring, `stream.started` yields `stream_state="preview"`, `stream_active=True`, `talk_enabled=False`, `call_state` stays `"ringing"`. Task 5's card relies on `call_state` staying `ringing` here.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_transitions.py`:

```python
def test_stream_started_during_unanswered_ring_is_preview(transitions):
    state = {"call_state": "ringing", transitions.RING_ACTIVE_KEY: True}
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
        transitions.RING_ACTIVE_KEY: True,
    }
    transitions.apply_state_transition(state, "call.answered", {"mode": "elsewhere"})
    assert state["call_state"] == "active"
    assert state["talk_enabled"] is False
    assert state[transitions.RING_ACTIVE_KEY] is False
    assert state["stream_state"] == "active"
    assert state["stream_active"] is True


def test_call_answered_locally_enables_talk_and_clears_latch(transitions):
    state = {"call_state": "ringing", transitions.RING_ACTIVE_KEY: True}
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
- Consumes: the companion's `/api/v2/state` top-level boolean `ringing` (added by the companion plan, Task 3 there).
- Produces: after normalization, `RING_ACTIVE_KEY` is True whenever the snapshot said `ringing: true`, even if HA just restarted mid-ring. The raw `ringing` key is still stripped from the normalized dict (entities are unchanged).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_transitions.py`:

```python
def test_normalize_rehydrates_ring_latch_from_snapshot_ringing(transitions):
    normalized = transitions.normalize_state_payload({"call_state": "ringing", "ringing": True})
    assert normalized[transitions.RING_ACTIVE_KEY] is True
    assert "ringing" not in normalized

    # ringing field alone (defensive: latch survives even if call_state drifted)
    normalized = transitions.normalize_state_payload({"call_state": "idle", "ringing": True})
    assert normalized[transitions.RING_ACTIVE_KEY] is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_transitions.py -v -k rehydrates`
Expected: FAIL on the second assert block (latch False when only `ringing` is set).

- [ ] **Step 3: Implement**

In `normalize_state_payload`, replace the latch block:

```python
    ring_active = bool(normalized.get(RING_ACTIVE_KEY))
    if normalized["call_state"] == "ringing":
        ring_active = True
    if bool(normalized.get("ringing")):
        ring_active = True
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

### Task 5: Card — call phase machine (auto-preview, Answer, Mute, Hang Up)

**Files:**
- Modify: `custom_components/bticino_companion/www/bticino-go-intercom-card.js`

**Interfaces:**
- Consumes: `call_state` sensor entity (card config key `call_state`, already read at `_stateText(cfg.call_state, "-")`), HA services `bticino_companion.call_answer` / `call_hangup` (registered in `__init__.py:215-222`), existing card internals `_start()`, `_stop(callHangup)`, `_micTrack`, `_talking`, `_updateDynamicParts()`.
- Produces: card behavior per spec §3. New optional config key `auto_preview` (default true). New internal fields `_answered`, `_autoStarted`, `_muted`, `_lastCallState`.

No JS unit-test infra exists; this task is verified by manual E2E (Task 7). Keep each step a separate concern so review can bisect.

- [ ] **Step 1: Add the phase state fields**

In the constructor (line 2-16), after `this._error = "";` add:

```javascript
    this._answered = false;
    this._autoStarted = false;
    this._muted = false;
    this._lastCallState = undefined;
```

- [ ] **Step 2: Drive the phase machine from `set hass`**

Replace the `set hass` setter (lines 25-32) with:

```javascript
  set hass(hass) {
    this._hass = hass;
    this._onCallStateChanged(this._stateText(this._config?.call_state, "-"));
    if (this._pc) {
      this._updateDynamicParts();
      return;
    }
    this._render();
  }
```

- [ ] **Step 3: Implement `_onCallStateChanged`, `_answer`, `_toggleMute`, `_hangup`**

Add these methods after `_setTalk` (line ~205):

```javascript
  _onCallStateChanged(callState) {
    const prev = this._lastCallState;
    this._lastCallState = callState;
    if (callState === prev) return;
    const autoPreview = this._config?.auto_preview !== false;
    if (callState === "ringing" && autoPreview && !this._pc && !this._starting) {
      this._autoStarted = true;
      this._start();
      return;
    }
    if (callState === "idle") {
      this._answered = false;
      this._muted = false;
      if (this._autoStarted) {
        this._autoStarted = false;
        this._stop(false);
      }
      return;
    }
    if (callState === "active" && !this._answered && this._micTrack) {
      // Answered elsewhere (indoor monitor): keep viewing, mic stays off.
      this._micTrack.enabled = false;
      this._talking = false;
    }
  }

  async _answer() {
    if (!this._hass || this._answered) return;
    try {
      await this._hass.callService("bticino_companion", "call_answer", {
        entry_id: this._config.entry_id,
      });
      this._answered = true;
      this._muted = false;
      this._error = "";
      if (!this._pc && !this._starting) {
        this._autoStarted = true;
        await this._start();
      }
      if (this._micTrack) {
        // Full-duplex call: open the mic continuously; Mute toggles it.
        this._micTrack.enabled = true;
        this._talking = true;
      }
    } catch (err) {
      // Typical race: 409 no_incoming_call (indoor monitor answered first,
      // or the 60s incoming window expired). Non-blocking message.
      this._error = err?.message || String(err);
    }
    this._render();
  }

  _toggleMute() {
    if (!this._answered || !this._micTrack) return;
    this._muted = !this._muted;
    this._micTrack.enabled = !this._muted;
    this._talking = !this._muted;
    this._updateDynamicParts();
  }

  async _hangup() {
    this._answered = false;
    this._muted = false;
    this._autoStarted = false;
    await this._stop(true);
  }
```

- [ ] **Step 4: Render the new controls**

In `_render` (line ~287-292), replace the `.controls` div with (all seven buttons always in the DOM; visibility is toggled):

```html
          <div class="controls">
            <button class="answer" data-action="answer" style="display:none">Answer</button>
            <button class="talk" data-action="mute" style="display:none">Mute</button>
            <button class="danger" data-action="hangup" style="display:none">Hang Up</button>
            <button class="primary" ${startDisabled ? "disabled" : ""} data-action="start">${this._starting ? "Starting..." : "Start"}</button>
            <button class="talk" ${talkDisabled ? "disabled" : ""} data-action="talk">${this._talking ? "Talking" : "Hold Talk"}</button>
            <button class="unlock" data-action="unlock">Unlock</button>
            <button class="danger" ${stopDisabled ? "disabled" : ""} data-action="stop">Stop</button>
          </div>
```

In the `<style>` block add, next to `.unlock`:

```css
        .answer { background: #0f8b4c; color: white; }
```

and change the `.controls` rule so hidden buttons do not leave holes:

```css
        .controls { display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); gap: 8px; }
```

At the end of `_render` (after `this._bindEvents();`) and at the end of `_updateDynamicParts()` (after `this._attachRemoteStream();`), add:

```javascript
    this._updateControlsVisibility();
```

Add the method after `_setRoleText` (line ~370):

```javascript
  _updateControlsVisibility() {
    if (!this.shadowRoot) return;
    const callState = this._stateText(this._config?.call_state, "-");
    const ringing = callState === "ringing" && !this._answered;
    const inCall = this._answered;
    const show = (action, visible) => {
      const el = this.shadowRoot.querySelector(`[data-action="${action}"]`);
      if (el) el.style.display = visible ? "" : "none";
    };
    show("answer", ringing);
    show("mute", inCall);
    show("hangup", inCall);
    // Start/Stop stay visible while ringing so auto_preview: false users can
    // still open the video manually; they hide only during an answered call.
    show("start", !inCall);
    show("talk", !inCall);
    show("stop", !inCall);
    const mute = this.shadowRoot.querySelector('[data-action="mute"]');
    if (mute) {
      mute.textContent = this._muted ? "Unmute" : "Mute";
      mute.disabled = !this._micTrack;
    }
  }
```

- [ ] **Step 5: Bind the new buttons**

In `_bindEvents` (lines 301-313), add:

```javascript
    const answer = this.shadowRoot.querySelector('[data-action="answer"]');
    const mute = this.shadowRoot.querySelector('[data-action="mute"]');
    const hangup = this.shadowRoot.querySelector('[data-action="hangup"]');
    answer?.addEventListener("click", () => this._answer());
    mute?.addEventListener("click", () => this._toggleMute());
    hangup?.addEventListener("click", () => this._hangup());
```

- [ ] **Step 6: Update the card registration description**

At the bottom of the file (line ~385-389), replace the `window.customCards.push` description:

```javascript
window.customCards.push({
  type: "bticino-go-intercom-card",
  name: "BTicino Go Intercom Card",
  description: "BTicino Companion intercom card: auto-preview on ring, answer with two-way audio, unlock.",
});
```

- [ ] **Step 7: Syntax check and commit**

Run: `node --check custom_components/bticino_companion/www/bticino-go-intercom-card.js`
Expected: no output (syntax OK). If `node` is unavailable, load the file in a browser console via a minimal HTML harness and confirm no parse error.

```bash
git add custom_components/bticino_companion/www/bticino-go-intercom-card.js
git commit -m "card: call phase machine - auto-preview, answer with full-duplex mic, mute, hang up"
```

---

### Task 6: README — document the card and the new semantics

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a card section**

Add a "Intercom card" section to `README.md` documenting: how to register the card resource (`/hacsfiles/bticino_companion/bticino-go-intercom-card.js` or local path), a config example:

```yaml
type: custom:bticino-go-intercom-card
entry_id: <config entry id>
entrypoint_id: main
camera: camera.bticino_main
call_state: sensor.bticino_call_state
stream_state: sensor.bticino_stream_state
ringing: binary_sensor.bticino_ringing
active_entrypoint: sensor.bticino_active_entrypoint
auto_preview: true   # optional, default true: start video automatically on ring
```

and the behavior: on ring the card auto-starts video preview and shows Answer; Answer takes the same SIP call the indoor monitor is ringing on (full-duplex audio, Mute/Hang Up); if the indoor monitor answers first the card keeps showing video until the call ends. Also update the "Call State" description (README.md:97): during an unanswered ring with video preview, `call_state` stays `ringing` and `stream_state` reads `preview`.

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

1. Ring the outdoor unit → card auto-starts video within ~2s, Answer button appears, `sensor.call_state` = `ringing`, `binary_sensor.ringing` = on.
2. Turn on the indoor monitor preview WITHOUT answering → card still shows Answer; `sensor.stream_state` = `preview`; `sensor.call_state` stays `ringing`.
3. Press Answer on the card → two-way audio card↔outdoor unit; Mute toggles the mic; Hang Up ends the call and the card returns to idle.
4. Repeat the ring, answer on the indoor monitor → Answer disappears, card keeps showing video, mic stays off; when the indoor conversation ends the card returns to idle.
5. Repeat the ring, let it expire (60s) → card returns to idle.
6. Press Answer simultaneously with the indoor monitor → loser gets a non-blocking error message and the card re-aligns.

- [ ] **Step 2: Unblock the release**

Only when all six pass: release the integration update (HACS tag) — coordinated with the companion `v*` release.
