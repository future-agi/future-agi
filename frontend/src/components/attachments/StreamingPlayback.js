// Small common transport for one mixed recording or server-derived channels.
// Native media elements own Range buffering; no fetch/blob/AudioContext decode.
export default class StreamingPlayback {
  constructor(elements, duration) {
    this.elements = elements;
    this.duration = duration;
    this.time = 0;
    this.playing = false;
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
        element.currentTime = this.time;
        this.pendingMetadata.delete(element);
      });
      listen("error", () => {
        this.pause();
        this.emit("error");
      });
      if (index === 0) {
        listen("timeupdate", () => {
          if (this.pendingMetadata.has(element)) return;
          this.time = Math.min(this.duration, element.currentTime);
          elements.slice(1).forEach((other) => {
            if (
              other.readyState >= 1 &&
              Math.abs(other.currentTime - this.time) > 0.12
            )
              other.currentTime = this.time;
          });
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
  getCurrentTime() {
    return this.time;
  }
  getDuration() {
    return this.duration;
  }
  isPlaying() {
    return this.playing;
  }
  setTime(seconds) {
    if (this.disposed || !Number.isFinite(seconds)) return;
    this.time = Math.max(0, Math.min(this.duration, seconds));
    this.elements.forEach((element) => {
      if (element.readyState >= 1) element.currentTime = this.time;
    });
    this.emit("timeupdate", this.time);
  }
  seekTo(progress) {
    this.setTime(progress * this.duration);
  }
  async play() {
    if (this.disposed) return;
    this.started = true;
    this.loadSources();
    const generation = ++this.generation;
    try {
      await Promise.all(this.elements.map((element) => element.play()));
      if (this.disposed || generation !== this.generation) return;
      this.playing = true;
      this.emit("play");
    } catch {
      if (generation !== this.generation || this.disposed) return;
      this.pause();
      this.emit("error");
    }
  }
  pause() {
    this.generation += 1;
    this.elements.forEach((element) => element.pause());
    this.playing = false;
    this.emit("pause");
  }
  replaceSources(paths) {
    if (this.disposed || paths.length !== this.elements.length) return;
    // Renew in place. Never reset the shared transcript clock or allocate a
    // second player; loadedmetadata restores the seek on each new grant.
    const resume = this.playing;
    this.pause();
    this.pendingSources = paths;
    // Chromium may request a Range even with preload=none once src/load is set.
    // Before the first user play intent keep the source entirely unassigned.
    if (this.started) this.loadSources();
    if (resume) void this.play();
  }
  loadSources() {
    if (!this.pendingSources) return;
    const paths = this.pendingSources;
    this.pendingSources = null;
    this.elements.forEach((element, index) => {
      this.pendingMetadata.add(element);
      element.preload = "none";
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
