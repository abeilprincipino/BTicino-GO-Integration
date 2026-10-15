class BTicinoGoIntercomCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._pc = undefined;
    this._localStream = undefined;
    this._micTrack = undefined;
    this._remoteStream = undefined;
    this._sessionId = undefined;
    this._queuedCandidates = [];
    this._offerAccepted = false;
    this._connecting = false;
    this._connected = false;
    this._muted = false;
    this._error = "";
    this._elsewhereUntil = 0;
    this._elsewhereLatched = false;
    // A ringing preview is the session opened while the call rings: video only,
    // microphone transceiver negotiated but empty until the answer.
    this._preview = false;
    this._previewTried = false;
    this._audioSender = undefined;
  }

  setConfig(config) {
    if (!config.camera) throw new Error("camera entity is required");
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    // A ringing preview ends with the ring — cancelled, timed out, answered
    // elsewhere or declined. The companion closes its side on its own; this
    // closes ours. An answer from this card turns the call active and keeps it.
    if (this._preview && (this._pc || this._connecting) && !this._isRinging() && this._callState() !== "active") {
      this._end();
    }
    if (this._pc || this._connecting) {
      this._attachVideo();
      return;
    }
    // One preview attempt per ring: a failed one is not retried until the next
    // call, so a refusal cannot turn into a loop of offers.
    if (!this._isRinging()) {
      this._previewTried = false;
    } else if (!this._previewTried && this.isConnected) {
      this._previewTried = true;
      this._start(undefined, { preview: true });
      return;
    }
    // The companion keeps last_incoming_call_end set until the next call starts,
    // so the notice is latched once per call-end: it stays suppressed while the
    // attribute remains true and re-arms only after it has gone back to false.
    if (this._answeredElsewhere()) {
      if (!this._elsewhereLatched) {
        this._elsewhereLatched = true;
        this._elsewhereUntil = Date.now() + 4000;
        setTimeout(() => { this._elsewhereUntil = 0; this._render(); }, 4000);
      }
    } else {
      this._elsewhereLatched = false;
      this._elsewhereUntil = 0;
    }
    this._render();
    this._attachVideo();
  }

  disconnectedCallback() {
    this._cleanup();
  }

  getCardSize() {
    return 5;
  }

  _cameraState() {
    return this._hass?.states[this._config.camera];
  }

  _snapshotUrl() {
    const picture = this._cameraState()?.attributes?.entity_picture;
    return picture ? this._hass.hassUrl(picture) : "";
  }

  _entrypointLabel() {
    const attributes = this._cameraState()?.attributes;
    return attributes?.bticino_entrypoint_label || this._config.name || attributes?.friendly_name || this._config.camera || "Intercom";
  }

  _callState() {
    return this._cameraState()?.attributes?.bticino_call_state || "idle";
  }

  _isRinging() {
    return this._cameraState()?.attributes?.bticino_is_ringing === true;
  }

  _canAnswer() {
    return this._cameraState()?.attributes?.bticino_can_answer === true;
  }

  _answeredElsewhere() {
    return this._cameraState()?.attributes?.bticino_answered_elsewhere === true;
  }

  _send(type, payload) {
    if (!this._hass) throw new Error("Home Assistant is not connected");
    return this._hass.connection.sendMessagePromise({
      type,
      camera_entity_id: this._config.camera,
      ...payload,
    });
  }

  _candidatePayload(candidate) {
    return {
      candidate: candidate.candidate,
      sdpMid: candidate.sdpMid,
      sdpMLineIndex: candidate.sdpMLineIndex,
      usernameFragment: candidate.usernameFragment,
    };
  }

  async _fetchRtcConfiguration() {
    // Reuses Home Assistant's own resolved ICE servers (incl. Nabu Casa Cloud
    // TURN, if subscribed) so this card's RTCPeerConnection isn't limited to
    // host candidates that are unreachable off the intercom's LAN.
    try {
      const clientConfig = await this._hass.callWS({
        type: "camera/webrtc/get_client_config",
        entity_id: this._config.camera,
      });
      if (clientConfig?.configuration) return clientConfig.configuration;
    } catch {
      // Older HA core, or no camera-provided configuration; fall through.
    }
    return { iceServers: [{ urls: ["stun:stun.l.google.com:19302"] }] };
  }

  _newSessionId() {
    if (globalThis.crypto?.randomUUID) return `bticino-card-${globalThis.crypto.randomUUID()}`;
    if (globalThis.crypto?.getRandomValues) {
      const values = new Uint32Array(2);
      globalThis.crypto.getRandomValues(values);
      return `bticino-card-${Date.now()}-${values[0].toString(16)}${values[1].toString(16)}`;
    }
    return `bticino-card-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  _sendCandidate(candidate) {
    if (!this._sessionId) return Promise.resolve();
    return this._send("bticino_companion/card_webrtc_candidate", {
      session_id: this._sessionId,
      candidate,
    });
  }

  // beforeOffer, when given, is awaited just before the offer goes out: everything
  // up to the local offer is prepared while it is still pending. preview opens the
  // session while the call rings: no microphone is asked for until the answer.
  async _start(beforeOffer, { preview = false } = {}) {
    if (this._pc || this._connecting) return;
    this._connecting = true;
    this._preview = preview;
    this._error = "";
    this._render();
    try {
      this._sessionId = this._newSessionId();
      this._remoteStream = new MediaStream();
      if (!preview && navigator.mediaDevices?.getUserMedia) {
        try {
          this._localStream = await navigator.mediaDevices.getUserMedia({ audio: true });
          this._micTrack = this._localStream.getAudioTracks()[0];
          this._micTrack.enabled = false;
        } catch {
          this._localStream = undefined;
          this._micTrack = undefined;
        }
      }

      const pc = new RTCPeerConnection(await this._fetchRtcConfiguration());
      this._pc = pc;
      pc.ontrack = (event) => {
        for (const track of event.streams[0]?.getTracks() || [event.track]) {
          if (!this._remoteStream.getTracks().some((current) => current.id === track.id)) {
            this._remoteStream.addTrack(track);
          }
        }
        this._attachVideo();
      };
      pc.onicecandidate = ({ candidate }) => {
        if (!candidate) return;
        const payload = this._candidatePayload(candidate);
        if (this._offerAccepted) {
          this._sendCandidate(payload).catch((err) => {
            console.warn("bticino-go-intercom-card: failed to send ICE candidate", err);
          });
        }
        else this._queuedCandidates.push(payload);
      };
      pc.onconnectionstatechange = () => {
        this._connected = pc.connectionState === "connected";
        if (this._connected && this._micTrack) this._micTrack.enabled = !this._muted;
        if (["failed", "closed"].includes(pc.connectionState) && this._pc === pc) {
          this._error = this._preview ? "" : "WebRTC connection was lost.";
          this._cleanup();
        }
        this._render();
      };
      if (preview) {
        // Negotiated now, filled at the answer with replaceTrack: answering then
        // needs no new offer and the video never stops.
        this._audioSender = pc.addTransceiver("audio", { direction: "sendrecv" }).sender;
      } else if (this._micTrack) {
        pc.addTrack(this._micTrack, this._localStream);
      } else {
        pc.addTransceiver("audio", { direction: "recvonly" });
      }
      pc.addTransceiver("video", { direction: "recvonly" });
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      if (beforeOffer) {
        await beforeOffer;
        // The card may have been torn down while the answer was in flight.
        if (this._pc !== pc) return;
      }
      const response = await this._send("bticino_companion/card_webrtc_offer", {
        session_id: this._sessionId,
        offer_sdp: pc.localDescription.sdp,
      });
      if (!response?.answer_sdp) throw new Error("Camera returned no WebRTC answer");
      this._offerAccepted = true;
      await pc.setRemoteDescription({ type: "answer", sdp: response.answer_sdp });
      await Promise.all(this._queuedCandidates.splice(0).map((candidate) => this._sendCandidate(candidate)));
      this._attachVideo();
    } catch (err) {
      this._error = err?.message || String(err);
      this._cleanup();
    }
    this._connecting = false;
    this._render();
  }

  _attachVideo() {
    const video = this.shadowRoot?.querySelector("video");
    if (video && this._remoteStream && video.srcObject !== this._remoteStream) {
      video.srcObject = this._remoteStream;
      video.play().catch(() => {});
    }
  }

  _toggleMute() {
    if (!this._micTrack || !this._connected) return;
    this._muted = !this._muted;
    this._micTrack.enabled = !this._muted;
    this._render();
  }

  async _unlock() {
    await this._send("bticino_companion/card_unlock", {});
  }

  async _answer() {
    if (this._preview && this._pc) return this._answerPreview();
    this._error = "";
    let answered;
    try {
      answered = this._send("bticino_companion/card_call_answer", {});
    } catch (err) {
      this._error = err?.message || String(err);
      this._render();
      return;
    }
    // Marks the rejection as handled now; _start still sees it when it awaits.
    answered.catch(() => {});
    // Without a preview session the offer waits for the answer, so the
    // companion adopts the answered call; the microphone, the ICE configuration
    // and the local offer do not wait, so they are prepared while it is in flight.
    // A failed answer surfaces through _start, which also drops what it prepared.
    await this._start(answered);
  }

  // The preview session is already up: answering only tells the companion and
  // attaches the microphone to the transceiver negotiated at the start. The
  // microphone is requested at the click, alongside the answer, so the browser
  // still sees the user gesture and neither waits for the other.
  async _answerPreview() {
    const pc = this._pc;
    this._error = "";
    const microphone = navigator.mediaDevices?.getUserMedia
      ? navigator.mediaDevices.getUserMedia({ audio: true }).catch(() => undefined)
      : Promise.resolve(undefined);
    try {
      await this._send("bticino_companion/card_call_answer", {});
    } catch (err) {
      (await microphone)?.getTracks().forEach((track) => track.stop());
      this._error = err?.message || String(err);
      this._render();
      return;
    }
    const stream = await microphone;
    if (this._pc !== pc) {
      stream?.getTracks().forEach((track) => track.stop());
      return;
    }
    this._preview = false;
    if (stream && this._audioSender) {
      this._localStream = stream;
      this._micTrack = stream.getAudioTracks()[0];
      this._micTrack.enabled = !this._muted;
      try {
        await this._audioSender.replaceTrack(this._micTrack);
      } catch (err) {
        console.warn("bticino-go-intercom-card: could not attach the microphone", err);
      }
    }
    this._render();
  }

  async _hangup() {
    try {
      await this._send("bticino_companion/card_call_hangup", {});
    } catch (err) {
      this._error = err?.message || String(err);
    }
    this._end();
  }

  _cleanup() {
    const sessionId = this._sessionId;
    this._sessionId = undefined;
    this._offerAccepted = false;
    this._queuedCandidates = [];
    this._connected = false;
    this._connecting = false;
    this._muted = false;
    this._preview = false;
    this._audioSender = undefined;
    this._micTrack?.stop();
    this._localStream?.getTracks().forEach((track) => track.stop());
    this._localStream = undefined;
    this._micTrack = undefined;
    const pc = this._pc;
    this._pc = undefined;
    pc?.close();
    this._remoteStream = undefined;
    if (sessionId && this._hass) {
      this._send("bticino_companion/card_webrtc_close", { session_id: sessionId }).catch(() => {});
    }
  }

  _end() {
    this._cleanup();
    this._render();
  }

  _escape(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[character]));
  }

  _render() {
    if (!this.shadowRoot) return;
    const name = this._config.name || this._cameraState()?.attributes?.friendly_name || this._config.camera || "Intercom";
    const entrypoint = this._entrypointLabel();
    const snapshot = this._snapshotUrl();
    const ringing = this._isRinging();
    const active = !!this._pc || this._connecting;
    const callState = this._callState();
    const remoteActive = callState === "active" || callState === "preview";
    const canAnswer = this._canAnswer() && (!active || this._preview);
    // inCall is read from the camera's own state, not from this card's session,
    // so it still holds when _start() has failed on an answered call: _cleanup()
    // drops _pc and clears _connecting, and the _render() at the tail of _start()
    // then sees active false but inCall true. That is the one case where the end
    // button has to be shown without a local session behind it — the SIP call is
    // up on the companion, and without the button there is nothing left that can
    // hang it up: every later preview would return without dialling and every
    // later inbound call would be answered busy, until the companion restarts.
    const inCall = callState === "active";
    const canEnd = (active && !this._preview) || inCall;
    const status = this._elsewhereUntil
      ? ["Answered elsewhere", "idle"]
      : this._preview
      ? ["Ringing", "ringing"]
      : this._connecting
      ? ["Connecting", "connecting"]
        : this._connected
          ? [this._muted ? "Muted" : "Connected", "active"]
          : this._pc
            ? ["Reconnecting", "connecting"]
          : ringing
          ? ["Ringing", "ringing"]
          : remoteActive
            ? [callState === "preview" ? "Preview" : "Active", "active"]
            : ["Idle", "idle"];
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; } ha-card { overflow: hidden; }
        .title-bar { display: flex; align-items: center; justify-content: space-between; padding: 12px 14px 0; }
        .title { font-size: 16px; font-weight: 600; } .subtitle { color: var(--secondary-text-color); font-size: 12px; margin-top: 2px; }
        .status { align-items: center; color: var(--secondary-text-color); display: flex; font-size: 12px; gap: 6px; }
        .dot { border-radius: 50%; height: 8px; width: 8px; } .dot-idle { background: rgba(127,127,127,.4); } .dot-active { background: #2e7d32; }
        .dot-ringing, .dot-connecting { animation: pulse 1.2s ease-in-out infinite; } .dot-ringing { background: #f9a825; } .dot-connecting { background: #90caf9; }
        @keyframes pulse { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: .5; transform: scale(1.3); } }
        .viewer { position: relative; background: #111; aspect-ratio: 4 / 3; display: grid; place-items: center; } img, video { width: 100%; height: 100%; object-fit: cover; } .placeholder { color: #aaa; } .hidden { display: none !important; }
        .ring-overlay { align-items: center; background: rgba(0,0,0,.55); color: #fff; display: flex; flex-direction: column; gap: 8px; inset: 0; justify-content: center; position: absolute; }
        .ring-pulse { animation: pulse 1.2s ease-in-out infinite; background: #f9a825; border-radius: 50%; height: 20px; width: 20px; } .ring-label { font-size: 16px; font-weight: 600; }
        .controls { display: flex; justify-content: center; gap: 16px; padding: 14px; } button { align-items: center; border: 0; border-radius: 50%; color: #fff; cursor: pointer; display: flex; height: 52px; justify-content: center; width: 52px; } button:disabled { cursor: not-allowed; opacity: .4; } button ha-icon { --mdc-icon-size: 26px; }
        .call { background: #2e7d32; } .end { background: #c62828; } .mute { background: #546e7a; } .unlock { background: #6d6d6d; } .error { color: var(--error-color); padding: 0 14px 12px; font-size: 13px; }
      </style>
      <ha-card><div class="title-bar"><div><div class="title">${this._escape(name)}</div><div class="subtitle">${this._escape(entrypoint)}</div></div><div class="status"><span class="dot dot-${status[1]}"></span>${status[0]}</div></div><div class="viewer">
        <img class="${active || !snapshot ? "hidden" : ""}" src="${this._escape(snapshot)}" alt="${this._escape(name)}" />
        <div class="placeholder ${active || snapshot ? "hidden" : ""}">No snapshot available</div>
        <video class="${this._connected ? "" : "hidden"}" playsinline autoplay></video>
        <div class="ring-overlay ${ringing && !active ? "" : "hidden"}"><div class="ring-pulse"></div><div class="ring-label">${this._escape(entrypoint)}</div><div>Incoming call</div></div>
      </div><div class="controls">
        <button class="call ${(active && !this._preview) || (ringing && !canAnswer) ? "hidden" : ""}" title="${canAnswer ? "Answer" : "Start live view"}"><ha-icon icon="mdi:phone"></ha-icon></button>
        <button class="end ${canEnd ? "" : "hidden"}" title="${inCall ? "Hang up" : "End live view"}"><ha-icon icon="mdi:phone-hangup"></ha-icon></button>
        <button class="mute ${this._connected && this._micTrack ? "" : "hidden"}" title="${this._muted ? "Unmute" : "Mute"}"><ha-icon icon="${this._muted ? "mdi:microphone-off" : "mdi:microphone"}"></ha-icon></button>
        <button class="unlock" title="Unlock"><ha-icon icon="mdi:lock-open"></ha-icon></button>
       </div><div class="error ${this._error ? "" : "hidden"}">${this._escape(this._error)}</div></ha-card>`;
    this.shadowRoot.querySelector(".call")?.addEventListener("click", () => (canAnswer ? this._answer() : this._start()));
    this.shadowRoot.querySelector(".end")?.addEventListener("click", () => (inCall ? this._hangup() : this._end()));
    this.shadowRoot.querySelector(".mute")?.addEventListener("click", () => this._toggleMute());
    this.shadowRoot.querySelector(".unlock")?.addEventListener("click", () => this._unlock().catch((err) => { this._error = err.message; this._render(); }));
    if (this._connected) this._attachVideo();
  }
}

customElements.define("bticino-go-intercom-card", BTicinoGoIntercomCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: "bticino-go-intercom-card", name: "BTicino Go Intercom", description: "Single-gate outgoing WebRTC intercom card." });
