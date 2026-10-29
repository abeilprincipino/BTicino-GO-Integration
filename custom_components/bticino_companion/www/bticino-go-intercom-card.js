// Shared by the whole card. Scoped selectors: the carousel and the clip player
// have buttons and images of their own.
const CARD_STYLES = `
  :host { display: block; } ha-card { overflow: hidden; }
  .title-bar { display: flex; align-items: center; justify-content: space-between; padding: 12px 14px 0; }
  .title { font-size: 16px; font-weight: 600; } .subtitle { color: var(--secondary-text-color); font-size: 12px; margin-top: 2px; }
  .status { align-items: center; color: var(--secondary-text-color); display: flex; font-size: 12px; gap: 6px; }
  .dot { border-radius: 50%; height: 8px; width: 8px; } .dot-idle { background: rgba(127,127,127,.4); } .dot-active { background: #2e7d32; }
  .dot-ringing, .dot-connecting { animation: pulse 1.2s ease-in-out infinite; } .dot-ringing { background: #f9a825; } .dot-connecting { background: #90caf9; }
  @keyframes pulse { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: .5; transform: scale(1.3); } }
  .viewer { position: relative; background: #111; aspect-ratio: 4 / 3; display: grid; place-items: center; }
  .live { display: grid; inset: 0; place-items: center; position: absolute; }
  .playback { background: #111; display: grid; inset: 0; place-items: center; position: absolute; z-index: 1; }
  .live img, .live video { width: 100%; height: 100%; object-fit: cover; } .placeholder { color: #aaa; } .hidden { display: none !important; }
  .ring-overlay { align-items: center; background: rgba(0,0,0,.55); color: #fff; display: flex; flex-direction: column; gap: 8px; inset: 0; justify-content: center; position: absolute; }
  .ring-pulse { animation: pulse 1.2s ease-in-out infinite; background: #f9a825; border-radius: 50%; height: 20px; width: 20px; } .ring-label { font-size: 16px; font-weight: 600; }
  .controls { display: flex; flex-wrap: wrap; justify-content: center; gap: 16px; padding: 14px; } .controls button { align-items: center; border: 0; border-radius: 50%; color: #fff; cursor: pointer; display: flex; height: 52px; justify-content: center; width: 52px; } .controls button:disabled { cursor: not-allowed; opacity: .4; } .controls button ha-icon { --mdc-icon-size: 26px; }
  .call { background: #2e7d32; } .end, .decline { background: #c62828; } .mute { background: #546e7a; } .unlock { background: #6d6d6d; } .error { color: var(--error-color); padding: 0 14px 12px; font-size: 13px; }
  .playback video { background: #111; height: 100%; object-fit: contain; width: 100%; }
  .playback-bar { align-items: center; background: linear-gradient(rgba(0,0,0,.6), transparent); color: #fff; display: flex; font-size: 13px; font-weight: 500; justify-content: space-between; left: 0; padding: 8px 8px 22px 14px; pointer-events: none; position: absolute; right: 0; top: 0; }
  .round { align-items: center; background: rgba(0,0,0,.5); border: 0; border-radius: 50%; color: #fff; cursor: pointer; display: flex; justify-content: center; pointer-events: auto; }
  .round:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
  .playback-close { height: 36px; width: 36px; } .playback-close ha-icon { --mdc-icon-size: 20px; }
  .replay { height: 56px; position: absolute; width: 56px; } .replay ha-icon { --mdc-icon-size: 30px; }
  .playback-error { align-items: center; color: #ddd; display: flex; flex-direction: column; font-size: 14px; gap: 8px; position: absolute; } .playback-error ha-icon { --mdc-icon-size: 32px; }
  .clips { border-top: 1px solid var(--divider-color); padding: 4px 14px 14px; }
  .clips-header { align-items: baseline; color: var(--secondary-text-color); display: flex; font-size: 13px; font-weight: 600; justify-content: space-between; padding-top: 10px; }
  .clips-header .count { font-size: 12px; font-weight: 400; }
  .strip { display: flex; gap: 10px; overflow-x: auto; margin: 0 -4px; padding: 8px 4px 2px; scroll-behavior: smooth; scroll-padding: 0 4px; scroll-snap-type: x mandatory; scrollbar-width: none; }
  .strip::-webkit-scrollbar { display: none; }
  .strip.fade { -webkit-mask-image: linear-gradient(to right, #000 85%, transparent); mask-image: linear-gradient(to right, #000 85%, transparent); }
  .clip-item { flex: 0 0 auto; scroll-snap-align: start; }
  .clip { background: none; border: 0; color: inherit; cursor: pointer; display: flex; flex-direction: column; font: inherit; gap: 6px; padding: 0; text-align: center; width: 112px; }
  .thumb { aspect-ratio: 4 / 3; background: var(--secondary-background-color); border-radius: calc(var(--ha-card-border-radius, 12px) * .75); box-shadow: 0 1px 2px rgba(0,0,0,.15); display: grid; overflow: hidden; place-items: center; position: relative; transition: transform 150ms ease, box-shadow 150ms ease; width: 100%; }
  .thumb img { grid-area: 1 / 1; height: 100%; object-fit: cover; width: 100%; }
  .no-poster { --mdc-icon-size: 28px; color: var(--secondary-text-color); grid-area: 1 / 1; }
  .badge { -webkit-backdrop-filter: blur(4px); align-items: center; backdrop-filter: blur(4px); background: rgba(0,0,0,.45); border-radius: 50%; color: #fff; display: flex; grid-area: 1 / 1; height: 30px; justify-content: center; width: 30px; }
  .badge ha-icon { --mdc-icon-size: 18px; }
  @media (hover: hover) { .clip:hover .thumb { box-shadow: 0 4px 10px rgba(0,0,0,.25); transform: translateY(-2px); } }
  .clip:focus-visible { outline: none; }
  .clip:focus-visible .thumb, .clip.playing .thumb { outline: 2px solid var(--primary-color); outline-offset: 2px; }
  .label { display: flex; flex-direction: column; line-height: 1.2; min-width: 0; }
  .label .day, .label .time { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .label .day { color: var(--primary-text-color); font-size: 12px; font-weight: 500; }
  .label .time { color: var(--secondary-text-color); font-size: 11px; }
  .skeleton { width: 112px; }
  .skeleton .thumb, .skeleton .label span { animation: shimmer 1.2s linear infinite; background: linear-gradient(90deg, var(--secondary-background-color) 25%, var(--divider-color) 50%, var(--secondary-background-color) 75%); background-size: 200% 100%; box-shadow: none; }
  .skeleton .label span { border-radius: 4px; display: block; margin: 2px auto 0; width: 70%; }
  @keyframes shimmer { from { background-position: 200% 0; } to { background-position: -200% 0; } }
  @media (max-width: 399px) { .clip, .skeleton { width: 96px; } }
  @media (prefers-reduced-motion: reduce) { .skeleton .thumb, .skeleton .label span { animation: none; } .thumb { transition: none; } .clip:hover .thumb { transform: none; } .strip { scroll-behavior: auto; } }
`;

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
    // Settles with the preview's audio sender (undefined if the session ends first),
    // so an answer given while still connecting can wait for it.
    this._senderReady = undefined;
    this._resolveSender = undefined;
    // True while _answerPreview runs, so set hass does not mistake our own answer
    // for one made by another client.
    this._answering = false;
    // True once this card has asked to answer the current call, so a later
    // "active" is recognised as ours and not as an answer on another device.
    this._answeredHere = false;
    // True after this card closed its view because another device answered;
    // cleared when that call ends.
    this._leftForElsewhere = false;
    this._skeleton = false;
    // Ring clips: the list from bticino_companion/card_ring_clips and the clip
    // playing in the viewer. See docs/superpowers/specs/2026-10-26-ring-clip-design.md
    // in the Companion repository.
    this._clips = [];
    this._clipsLoadedAt = 0;
    // The bticino_last_ring_clip_at the list was loaded for.
    this._clipsKey = undefined;
    this._clipsLoading = false;
    this._clipsLoaded = false;
    this._clipsPromise = undefined;
    this._clipsReload = false;
    // recorded_at of the clip in the player, undefined when none plays.
    this._playing = undefined;
    // The player's video failed: keep "Clip unavailable" up until the user closes it.
    this._playbackFailed = false;
    this._onKeydown = (event) => {
      if (event.key === "Escape" && !event.defaultPrevented && this._playing) this._closePlayback();
    };
  }

  setConfig(config) {
    if (!config.camera) throw new Error("camera entity is required");
    const clips = config.clips ?? 10;
    if (!Number.isInteger(clips) || clips < 0 || clips > 50) throw new Error("clips must be an integer from 0 to 50");
    // unlocks: { <entrypoint id>: { icon, name, hide } }, all optional.
    const unlocks = config.unlocks ?? {};
    if (typeof unlocks !== "object" || Array.isArray(unlocks)) throw new Error("unlocks must map entrypoint ids to { icon, name, hide }");
    this._config = { ...config, clips, unlocks };
    this._clipsKey = undefined;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._syncClips();
    // The doorbell comes first: a ring or a call closes the clip player.
    if (this._playing && (this._isRinging() || this._callState() === "active")) this._closePlayback();
    this._endPreviewIfRingOver();
    if (this._leftForElsewhere && this._callState() !== "active") this._leftForElsewhere = false;
    if (this._pc || this._connecting) {
      // The call was answered through the companion by another device: this
      // card steps aside. Closing with leave never ends the call, which the
      // answering device now carries. See
      // docs/superpowers/specs/2026-10-22-shared-ringing-stream-design.md in the
      // Companion repository.
      if (!this._answering && !this._answeredHere && this._callState() === "active") {
        this._leftForElsewhere = true;
        this._cleanup({ leave: true });
        this._render();
        return;
      }
      this._attachVideo();
      return;
    }
    // One preview attempt per SIP call, reset when no SIP call is answerable: a
    // failed one is not retried until the next call, so a refusal cannot turn
    // into a loop of offers.
    // bticino_is_ringing also covers a bus ring with no SIP call, which the companion could only serve by dialling the ringing intercom.
    if (!this._canAnswer()) {
      this._previewTried = false;
    } else if (!this._previewTried && !this._leftForElsewhere && this.isConnected) {
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

  // A ringing preview ends with the ring — cancelled, timed out, answered
  // elsewhere or declined. The companion closes its side on its own; this
  // closes ours. An answer from this card turns the call active and keeps it,
  // and while it is in flight the companion may report "not ringing" a tick
  // before "active", so the check waits for it and is repeated when it ends.
  _endPreviewIfRingOver() {
    if (this._preview && !this._answering && (this._pc || this._connecting) && !this._isRinging() && this._callState() !== "active") {
      this._end();
    }
  }

  connectedCallback() {
    window.addEventListener("keydown", this._onKeydown);
  }

  disconnectedCallback() {
    window.removeEventListener("keydown", this._onKeydown);
    this._cleanup();
  }

  getCardSize() {
    return this._config.clips ? 7 : 5;
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

  _syncClips() {
    if (!this._config.clips) {
      if (this._clips.length || this._playing) {
        this._clips = [];
        this._closePlayback();
      }
      this._renderClips();
      return;
    }
    const key = this._cameraState()?.attributes?.bticino_last_ring_clip_at || "";
    if (key === this._clipsKey) return;
    this._clipsKey = key;
    this._loadClips().catch(() => {});
  }

  // One request at a time; a reload asked meanwhile runs once the current one ends.
  _loadClips() {
    if (this._clipsPromise) {
      this._clipsReload = true;
      return this._clipsPromise;
    }
    this._clipsPromise = this._fetchClips().finally(() => { this._clipsPromise = undefined; });
    return this._clipsPromise;
  }

  async _fetchClips() {
    this._clipsLoading = true;
    // Skeletons only on the first load: a reload keeps the strip as it is.
    if (!this._clipsLoaded) this._renderClips();
    try {
      do {
        this._clipsReload = false;
        const response = await this._send("bticino_companion/card_ring_clips", { limit: this._config.clips });
        this._clips = Array.isArray(response?.clips) ? response.clips : [];
        this._clipsLoadedAt = Date.now();
      } while (this._clipsReload);
    } catch (err) {
      // Clip errors never mix with call errors: the carousel just hides.
      this._clips = [];
      console.debug("bticino-go-intercom-card: ring clips unavailable", err);
    } finally {
      this._clipsLoading = false;
      this._clipsLoaded = true;
    }
    if (this._playing && !this._playbackFailed && !this._clips.some((clip) => clip.recorded_at === this._playing)) this._closePlayback();
    this._renderClips();
  }

  _renderClips() {
    const section = this.shadowRoot?.querySelector(".clips");
    if (!section) return;
    const loading = this._clipsLoading && !this._clipsLoaded;
    const visible = this._config.clips > 0 && (loading || this._clips.length > 0);
    section.classList.toggle("hidden", !visible);
    if (!visible) {
      section.innerHTML = "";
      return;
    }
    const scroll = section.querySelector(".strip")?.scrollLeft || 0;
    const focused = this.shadowRoot.activeElement;
    const focusedKey = focused && section.contains(focused) && focused.classList.contains("clip") ? focused.dataset.recordedAt : undefined;
    const items = loading
      ? Array.from({ length: 3 }, () => `<div class="clip-item skeleton" role="listitem" aria-hidden="true"><div class="thumb"></div><div class="label"><span class="day">&nbsp;</span><span class="time">&nbsp;</span></div></div>`).join("")
      : this._clips.map((clip) => {
        const { day, time } = this._clipLabel(clip.recorded_at);
        const playing = clip.recorded_at === this._playing;
        const poster = clip.poster_url
          ? `<img src="${this._escape(this._hass.hassUrl(clip.poster_url))}" alt="" loading="lazy" />`
          : `<ha-icon class="no-poster" icon="mdi:video"></ha-icon>`;
        return `<div class="clip-item" role="listitem"><button class="clip ${playing ? "playing" : ""}" data-recorded-at="${this._escape(clip.recorded_at)}" aria-label="Play ring clip, ${this._escape(day)} ${this._escape(time)}" aria-current="${playing}"><span class="thumb">${poster}<span class="badge"><ha-icon icon="${playing ? "mdi:equalizer" : "mdi:play"}"></ha-icon></span></span><span class="label"><span class="day">${this._escape(playing ? "Playing" : day)}</span><span class="time">${this._escape(time)}</span></span></button></div>`;
      }).join("");
    section.innerHTML = `<div class="clips-header"><span>Recent rings</span><span class="count">${loading ? "" : this._clips.length}</span></div><div class="strip" role="list">${items}</div>`;
    const strip = section.querySelector(".strip");
    strip.scrollTo({ left: scroll, behavior: "instant" });
    strip.addEventListener("scroll", () => this._updateFade(strip), { passive: true });
    strip.querySelectorAll("button.clip").forEach((button) => {
      button.addEventListener("click", () => this._play(button.dataset.recordedAt));
    });
    this._updateFade(strip);
    if (focusedKey !== undefined) {
      Array.from(strip.querySelectorAll("button.clip")).find((button) => button.dataset.recordedAt === focusedKey)?.focus({ preventScroll: true });
    }
  }

  // The right edge fades while more clips are off screen.
  _updateFade(strip) {
    strip.classList.toggle("fade", strip.scrollLeft + strip.clientWidth < strip.scrollWidth - 4);
  }

  // "Today 14:32", "Yesterday 09:10", "12 Oct 18:05", in the user's language
  // and 12/24-hour setting, without translation strings.
  _clipLabel(iso) {
    const date = new Date(iso);
    const language = this._hass?.locale?.language || navigator.language;
    const startOfDay = (value) => new Date(value.getFullYear(), value.getMonth(), value.getDate());
    const days = Math.round((startOfDay(date) - startOfDay(new Date())) / 86400000);
    let day;
    if (days === 0 || days === -1) {
      const relative = new Intl.RelativeTimeFormat(language, { numeric: "auto" }).format(days, "day");
      day = relative.charAt(0).toLocaleUpperCase(language) + relative.slice(1);
    } else {
      day = date.toLocaleDateString(language, { day: "numeric", month: "short" });
    }
    const format = this._hass?.locale?.time_format;
    const hour12 = format === "12" ? { hour12: true } : format === "24" ? { hourCycle: "h23" } : {};
    const time = date.toLocaleTimeString(language, { hour: "2-digit", minute: "2-digit", ...hour12 });
    return { day, time };
  }

  async _play(recordedAt) {
    // The doorbell comes first: no clip while it rings or a call is up.
    if (this._isRinging() || this._pc || this._connecting || this._callState() === "active") return;
    // Signed URLs last an hour: reload a list older than 50 minutes first.
    if (Date.now() - this._clipsLoadedAt > 50 * 60 * 1000) {
      await this._loadClips();
      // A ring, live view or answer may have started during the reload.
      if (this._isRinging() || this._pc || this._connecting || this._callState() === "active") return;
    }
    const clip = this._clips.find((item) => item.recorded_at === recordedAt);
    if (!clip) return;
    this._playing = recordedAt;
    this._playbackFailed = false;
    this._renderPlayback(clip);
    this._renderClips();
  }

  _renderPlayback(clip) {
    const layer = this.shadowRoot?.querySelector(".playback");
    if (!layer) return;
    if (!this._playing || !clip) {
      layer.classList.add("hidden");
      layer.innerHTML = "";
      return;
    }
    const { day, time } = this._clipLabel(clip.recorded_at);
    const poster = clip.poster_url ? ` poster="${this._escape(this._hass.hassUrl(clip.poster_url))}"` : "";
    // Muted for autoplay: the clip has no audio anyway.
    layer.innerHTML = `
      <video controls autoplay playsinline muted preload="auto"${poster} src="${this._escape(this._hass.hassUrl(clip.url))}"></video>
      <div class="playback-bar"><span>${this._escape(day)} · ${this._escape(time)}</span><button class="round playback-close" title="Close" aria-label="Close clip"><ha-icon icon="mdi:close"></ha-icon></button></div>
      <button class="round replay hidden" title="Replay" aria-label="Replay clip"><ha-icon icon="mdi:replay"></ha-icon></button>
      <div class="playback-error hidden"><ha-icon icon="mdi:video-off"></ha-icon><span>Clip unavailable</span></div>`;
    layer.classList.remove("hidden");
    const video = layer.querySelector("video");
    const replay = layer.querySelector(".replay");
    video.addEventListener("ended", () => replay.classList.remove("hidden"));
    video.addEventListener("play", () => replay.classList.add("hidden"));
    video.addEventListener("error", () => {
      // An error from a video that was replaced must not fail the new player.
      if (layer.querySelector("video") !== video) return;
      // A file purged meanwhile, or an expired URL: say so and refresh the list.
      video.classList.add("hidden");
      layer.querySelector(".playback-error").classList.remove("hidden");
      this._playbackFailed = true;
      this._loadClips().catch(() => {});
    });
    replay.addEventListener("click", () => {
      video.currentTime = 0;
      video.play().catch(() => {});
    });
    layer.querySelector(".playback-close").addEventListener("click", () => this._closePlayback());
  }

  _closePlayback() {
    if (!this._playing) return;
    this._playing = undefined;
    this._playbackFailed = false;
    this._renderPlayback();
    this._renderClips();
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
    this._closePlayback();
    this._connecting = true;
    this._preview = preview;
    this._error = "";
    // An explicit live view during a call is this device joining it, not a stale
    // preview: without this the next hass update would close it with a leave. The
    // companion's single-talker rule keeps two microphones from mixing.
    if (!preview && this._callState() === "active") this._answeredHere = true;
    this._render();
    // Declared outside the try so the catch can tell a teardown from a failure.
    let sessionId;
    try {
      this._sessionId = this._newSessionId();
      // Every await below can outlive the session (the ring ends, the card is
      // removed): a changed id means it was torn down and nothing may go on.
      sessionId = this._sessionId;
      if (preview) this._senderReady = new Promise((resolve) => { this._resolveSender = resolve; });
      this._remoteStream = new MediaStream();
      if (!preview && navigator.mediaDevices?.getUserMedia) {
        try {
          const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
          if (this._sessionId !== sessionId) {
            stream.getTracks().forEach((track) => track.stop());
            return;
          }
          this._localStream = stream;
          this._micTrack = this._localStream.getAudioTracks()[0];
          this._micTrack.enabled = false;
        } catch {
          this._localStream = undefined;
          this._micTrack = undefined;
        }
      }

      const configuration = await this._fetchRtcConfiguration();
      if (this._sessionId !== sessionId) return;
      const pc = new RTCPeerConnection(configuration);
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
        this._resolveSender?.(this._audioSender);
      } else if (this._micTrack) {
        pc.addTrack(this._micTrack, this._localStream);
      } else {
        pc.addTransceiver("audio", { direction: "recvonly" });
      }
      pc.addTransceiver("video", { direction: "recvonly" });
      const offer = await pc.createOffer();
      if (this._sessionId !== sessionId) return;
      await pc.setLocalDescription(offer);
      if (this._sessionId !== sessionId) return;
      if (beforeOffer) {
        await beforeOffer;
        if (this._sessionId !== sessionId) return;
      }
      const response = await this._send("bticino_companion/card_webrtc_offer", {
        session_id: sessionId,
        offer_sdp: pc.localDescription.sdp,
      });
      if (this._sessionId !== sessionId) return;
      if (!response?.answer_sdp) throw new Error("Camera returned no WebRTC answer");
      this._offerAccepted = true;
      await pc.setRemoteDescription({ type: "answer", sdp: response.answer_sdp });
      if (this._sessionId !== sessionId) return;
      await Promise.all(this._queuedCandidates.splice(0).map((candidate) => this._sendCandidate(candidate)));
      if (this._sessionId !== sessionId) return;
      this._attachVideo();
    } catch (err) {
      // Torn down meanwhile: the teardown already rendered, and the failure is
      // only the closed connection talking.
      if (this._sessionId !== sessionId) return;
      if (this._preview) {
        // A preview is a courtesy: when it cannot start the user sees no error.
        this._error = "";
        console.debug("bticino-go-intercom-card: ringing preview unavailable", err);
      } else {
        this._error = err?.message || String(err);
      }
      this._cleanup();
    }
    this._connecting = false;
    this._render();
  }

  _attachVideo() {
    const video = this.shadowRoot?.querySelector(".live video");
    if (video && this._remoteStream && video.srcObject !== this._remoteStream) {
      video.srcObject = this._remoteStream;
      // A ringing preview starts without a user gesture, and the autoplay policy
      // blocks unmuted playback: muted, the video plays. The preview carries no
      // audio anyway, and answering renders a new element unmuted.
      video.muted = this._preview;
      video.play().catch(() => {
        if (video.muted) return;
        video.muted = true;
        video.play().catch(() => {});
      });
    }
  }

  _toggleMute() {
    if (!this._micTrack || !this._connected) return;
    this._muted = !this._muted;
    this._micTrack.enabled = !this._muted;
    this._render();
  }

  async _unlock(entrypointId) {
    await this._send("bticino_companion/card_unlock", entrypointId ? { entrypoint_id: entrypointId } : {});
  }

  // One button per lock of the intercom, as the camera lists them, with the
  // card's icon, name and hide overrides. A camera from an older integration
  // has no list: it keeps the single button for its own entrypoint.
  _unlockButtons() {
    const listed = this._cameraState()?.attributes?.bticino_unlocks;
    if (!Array.isArray(listed)) return [{ id: "", name: "Unlock", icon: "mdi:lock-open" }];
    return listed
      .filter((item) => item && item.id && !this._config.unlocks[item.id]?.hide)
      .map((item) => {
        const custom = this._config.unlocks[item.id] || {};
        return { id: item.id, name: custom.name || item.label || item.id, icon: custom.icon || "mdi:lock-open" };
      });
  }

  async _answer() {
    if (this._preview && (this._pc || this._connecting)) return this._answerPreview();
    this._answeredHere = true;
    this._error = "";
    let answered;
    try {
      answered = this._send("bticino_companion/card_call_answer", {});
    } catch (err) {
      this._answeredHere = false;
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

  // The preview session is up or still coming up: answering only tells the
  // companion and attaches the microphone to the transceiver negotiated at the
  // start. The microphone is requested at the click, alongside the answer, so the
  // browser still sees the user gesture and neither waits for the other.
  async _answerPreview() {
    if (this._answering) return;
    // The session is identified by id, not by pc, which may not exist yet.
    const sessionId = this._sessionId;
    this._error = "";
    this._answering = true;
    this._answeredHere = true;
    try {
      const microphone = navigator.mediaDevices?.getUserMedia
        ? navigator.mediaDevices.getUserMedia({ audio: true }).catch(() => undefined)
        : Promise.resolve(undefined);
      try {
        await this._send("bticino_companion/card_call_answer", {});
      } catch (err) {
        // The prompt may still be open: release the stream whenever it arrives,
        // but show the error now.
        microphone.then((stream) => stream?.getTracks().forEach((track) => track.stop()));
        this._answeredHere = false;
        this._error = err?.message || String(err);
        this._render();
        return;
      }
      if (this._sessionId === sessionId) {
        this._preview = false;
        // Rendered here too: without a microphone nothing else would show the
        // hang-up button.
        this._render();
      }
      await this._attachMicrophone(sessionId, microphone);
    } finally {
      this._answering = false;
      this._endPreviewIfRingOver();
    }
  }

  // Puts the microphone on the audio transceiver negotiated by the preview.
  // microphone is a promise of a MediaStream, or undefined when none was obtained.
  async _attachMicrophone(sessionId, microphone) {
    const stream = await microphone;
    if (!stream) return;
    // A connecting preview has no sender yet: wait for it, or for the teardown.
    const sender = this._audioSender || await this._senderReady;
    if (this._sessionId !== sessionId || !sender) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
    if (this._localStream && this._localStream !== stream) {
      this._localStream.getTracks().forEach((track) => track.stop());
    }
    this._localStream = stream;
    this._micTrack = stream.getAudioTracks()[0];
    this._micTrack.enabled = !this._muted;
    try {
      await sender.replaceTrack(this._micTrack);
    } catch (err) {
      stream.getTracks().forEach((track) => track.stop());
      if (this._localStream === stream) {
        this._localStream = undefined;
        this._micTrack = undefined;
      }
      console.warn("bticino-go-intercom-card: could not attach the microphone", err);
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

  _cleanup({ leave = false } = {}) {
    const sessionId = this._sessionId;
    this._sessionId = undefined;
    this._offerAccepted = false;
    this._queuedCandidates = [];
    this._connected = false;
    this._connecting = false;
    this._muted = false;
    this._preview = false;
    this._answeredHere = false;
    this._audioSender = undefined;
    this._resolveSender?.(undefined);
    this._senderReady = undefined;
    this._resolveSender = undefined;
    this._micTrack?.stop();
    this._localStream?.getTracks().forEach((track) => track.stop());
    this._localStream = undefined;
    this._micTrack = undefined;
    const pc = this._pc;
    this._pc = undefined;
    pc?.close();
    this._remoteStream = undefined;
    if (sessionId && this._hass) {
      this._send("bticino_companion/card_webrtc_close", { session_id: sessionId, ...(leave ? { leave: true } : {}) }).catch(() => {});
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

  // The parts _render() rewrites on every hass update live in their own
  // containers; the clip player and the carousel do not, so a playing clip or a
  // scrolled strip survives. See docs/superpowers/specs/2026-10-26-ring-clip-design.md
  // in the Companion repository.
  _ensureSkeleton() {
    if (this._skeleton || !this.shadowRoot) return;
    this.shadowRoot.innerHTML = `<style>${CARD_STYLES}</style><ha-card><div class="top"></div><div class="viewer"><div class="live"></div><div class="playback hidden"></div></div><div class="bottom"></div><section class="clips hidden"></section></ha-card>`;
    this._skeleton = true;
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
    // A ringing SIP call can be refused. The companion answers it 603 Decline,
    // which ends every fork of the doorbell call, the indoor unit's included.
    const canDecline = this._canAnswer() && !canEnd;
    const status = this._leftForElsewhere && inCall
      ? ["In a call on another device", "active"]
      : this._elsewhereUntil
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
    this._ensureSkeleton();
    const root = this.shadowRoot;
    root.querySelector(".top").innerHTML = `<div class="title-bar"><div><div class="title">${this._escape(name)}</div><div class="subtitle">${this._escape(entrypoint)}</div></div><div class="status"><span class="dot dot-${status[1]}"></span>${status[0]}</div></div>`;
    root.querySelector(".live").innerHTML = `
        <img class="${active || !snapshot ? "hidden" : ""}" src="${this._escape(snapshot)}" alt="${this._escape(name)}" />
        <div class="placeholder ${active || snapshot ? "hidden" : ""}">No snapshot available</div>
        <video class="${this._connected ? "" : "hidden"}" playsinline autoplay></video>
        <div class="ring-overlay ${ringing && !active ? "" : "hidden"}"><div class="ring-pulse"></div><div class="ring-label">${this._escape(entrypoint)}</div><div>Incoming call</div></div>`;
    root.querySelector(".bottom").innerHTML = `<div class="controls">
        <button class="call ${(active && !this._preview) || (ringing && !canAnswer) || (this._leftForElsewhere && inCall) ? "hidden" : ""}" title="${canAnswer ? "Answer" : "Start live view"}"><ha-icon icon="mdi:phone"></ha-icon></button>
        <button class="decline ${canDecline ? "" : "hidden"}" title="Decline" aria-label="Decline call"><ha-icon icon="mdi:phone-hangup"></ha-icon></button>
        <button class="end ${canEnd ? "" : "hidden"}" title="${inCall ? "Hang up" : "End live view"}"><ha-icon icon="mdi:phone-hangup"></ha-icon></button>
        <button class="mute ${this._connected && this._micTrack ? "" : "hidden"}" title="${this._muted ? "Unmute" : "Mute"}"><ha-icon icon="${this._muted ? "mdi:microphone-off" : "mdi:microphone"}"></ha-icon></button>
        ${this._unlockButtons().map((unlock) => `<button class="unlock" data-entrypoint="${this._escape(unlock.id)}" title="${this._escape(unlock.name)}" aria-label="${this._escape(unlock.name)}"><ha-icon icon="${this._escape(unlock.icon)}"></ha-icon></button>`).join("")}
       </div><div class="error ${this._error ? "" : "hidden"}">${this._escape(this._error)}</div>`;
    this.shadowRoot.querySelector(".call")?.addEventListener("click", () => (canAnswer ? this._answer() : this._start()));
    this.shadowRoot.querySelector(".decline")?.addEventListener("click", () => this._hangup());
    this.shadowRoot.querySelector(".end")?.addEventListener("click", () => (inCall ? this._hangup() : this._end()));
    this.shadowRoot.querySelector(".mute")?.addEventListener("click", () => this._toggleMute());
    this.shadowRoot.querySelectorAll(".unlock").forEach((button) => button.addEventListener("click", () => this._unlock(button.dataset.entrypoint).catch((err) => { this._error = err.message; this._render(); })));
    if (this._connected) this._attachVideo();
  }
}

customElements.define("bticino-go-intercom-card", BTicinoGoIntercomCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: "bticino-go-intercom-card", name: "BTicino Go Intercom", description: "WebRTC intercom card with a button for every lock of the intercom." });
