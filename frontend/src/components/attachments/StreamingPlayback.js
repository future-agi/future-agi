// Native media owns Range buffering; never decode or split whole recordings.
const ALIGN_TOLERANCE = 0.025;
const DRIFT_TOLERANCE = 0.12;
const HAVE_FUTURE_DATA = 3;

export default class StreamingPlayback {
  constructor(elements, duration) {
    this.elements = elements;
    this.duration = duration;
    this.time = 0;
    this.playing = false;
    this.starting = false;
    this.wantsPlay = false;
    this.started = false;
    this.pendingSources = null;
    this.disposed = false;
    this.generation = 0;
    this.pendingMetadata = new Set();
    this.listeners = new Map();
    this.cleanups = [];
    elements.forEach((element, index) => {
      const listen = (name, fn) => {
        element.addEventListener(name, fn);
        this.cleanups.push(() => element.removeEventListener(name, fn));
      };
      listen("loadedmetadata", () => {
        this.pendingMetadata.delete(element);
        this.align();
        void this.resumeWhenReady();
      });
      ["canplay", "seeked"].forEach((event) =>
        listen(event, () => void this.resumeWhenReady()),
      );
      ["waiting", "seeking", "emptied"].forEach((event) =>
        listen(event, () => {
          this.hold();
          this.align();
          // A queued seeking event may arrive after the seek already completed.
          void this.resumeWhenReady();
        }),
      );
      listen("error", () => {
        this.pause();
        this.emit("error");
      });
      if (index === 0) {
        listen("timeupdate", () => {
          // The clock must not follow an early track while its peer is waiting.
          if (!this.playing || this.pendingMetadata.size) return;
          if (
            !this.ready() ||
            elements.some(
              (other) => Math.abs(other.currentTime - element.currentTime) > DRIFT_TOLERANCE,
            )
          ) {
            this.hold();
            this.align();
            void this.resumeWhenReady();
            return;
          }
          this.time = Math.min(this.duration, element.currentTime);
          this.emit("timeupdate", this.time);
        });
        listen("ended", () => {
          this.pause();
          this.emit("ended");
        });
      }
    });
  }
  on(event, callback) {
    if (!this.listeners.has(event)) this.listeners.set(event, new Set());
    this.listeners.get(event).add(callback);
    return () => this.listeners.get(event)?.delete(callback);
  }
  emit(event, value) {
    this.listeners.get(event)?.forEach((callback) => callback(value));
  }
  getCurrentTime() { return this.time; }
  getDuration() { return this.duration; }
  isPlaying() { return this.playing; }
  ready() {
    return !this.pendingMetadata.size && this.elements.every(
      (element) => element.readyState >= HAVE_FUTURE_DATA && !element.seeking,
    );
  }
  align() {
    this.elements.forEach((element) => {
      if (!this.pendingMetadata.has(element) && element.readyState >= 1 &&
          Math.abs(element.currentTime - this.time) > ALIGN_TOLERANCE)
        element.currentTime = this.time;
    });
  }
  // Keep play intent distinct from actual playback. One stalled/seeking channel
  // pauses EVERY channel at the earliest position, never skipping unheard audio.
  hold(retainPosition = true) {
    if (retainPosition && (this.playing || this.starting) && !this.pendingMetadata.size) {
      this.time = Math.max(0, Math.min(this.duration, ...this.elements.map((e) => e.currentTime)));
      this.emit("timeupdate", this.time);
    }
    this.generation += 1;
    this.starting = false;
    this.playing = false;
    this.elements.forEach((element) => element.pause());
    this.emit("pause");
    this.emit("buffering", this.wantsPlay);
  }
  setTime(seconds) {
    if (this.disposed || !Number.isFinite(seconds)) return;
    this.hold(false);
    this.time = Math.max(0, Math.min(this.duration, seconds));
    this.align();
    this.emit("timeupdate", this.time);
    void this.resumeWhenReady();
  }
  seekTo(progress) { this.setTime(progress * this.duration); }
  play() {
    if (this.disposed) return Promise.resolve();
    this.wantsPlay = true;
    this.emit("intent", true);
    this.started = true;
    this.loadSources();
    this.emit("buffering", !this.playing);
    return this.resumeWhenReady();
  }
  async resumeWhenReady() {
    if (this.disposed || !this.wantsPlay || this.playing || this.starting || !this.ready()) return;
    this.align();
    // Seeking to the retained clock can itself need another Range response.
    if (!this.ready()) return;
    this.starting = true;
    const generation = ++this.generation;
    try {
      // Call play in one task only AFTER every required channel can play.
      await Promise.all(this.elements.map((element) => element.play()));
      if (this.disposed || generation !== this.generation) return;
      this.starting = false;
      if (!this.ready()) {
        this.hold();
        return;
      }
      this.playing = true;
      this.emit("buffering", false);
      this.emit("play");
    } catch {
      if (generation !== this.generation || this.disposed) return;
      this.pause();
      this.emit("error");
    }
  }
  pause() {
    this.wantsPlay = false;
    this.emit("intent", false);
    this.hold();
  }
  replaceSources(paths) {
    if (this.disposed || paths.length !== this.elements.length) return;
    // Retain intent even if renewal happens during buffering, not just playback.
    this.hold();
    this.pendingSources = paths;
    // Chromium can request a Range despite preload=none: withhold src until Play.
    if (this.started) this.loadSources();
  }
  loadSources() {
    if (!this.pendingSources) return;
    const paths = this.pendingSources;
    this.pendingSources = null;
    this.elements.forEach((element, index) => {
      this.pendingMetadata.add(element);
      // After explicit play intent, buffer while paused until the shared barrier
      // opens. preload=none cannot establish readiness without starting a track.
      element.preload = "auto";
      element.src = paths[index];
      element.load();
    });
  }
  destroy() {
    if (this.disposed) return;
    this.disposed = true;
    this.pause();
    this.cleanups.forEach((cleanup) => cleanup());
    this.elements.forEach((element) => {
      element.removeAttribute("src");
      element.load();
    });
    this.listeners.clear();
  }
}
