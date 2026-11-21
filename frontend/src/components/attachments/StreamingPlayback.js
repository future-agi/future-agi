// Native media owns Range buffering; never decode or split whole recordings.
const ALIGN_TOLERANCE = 0.025;
const DRIFT_TOLERANCE = 0.12;
const HAVE_FUTURE_DATA = 3;
// A window with no active element (every lane in a recorded gap) still plays
// as silent time on a wall clock.
const SILENT_TICK_MS = 250;

/**
 * One synchronized group of media elements behind one clock.
 *
 * Each element plays its own source with a `base`: the clock time at which
 * that source's local 0 sits (a segment's start on the recording timeline).
 * An element without a source is inactive and ignored, so lanes whose
 * segments start and end at different times share the clock. The first
 * active element still playing leads; with none active, a wall clock runs.
 * The clock never passes `limit`. A source that reaches it (or runs out)
 * first waits there, paused, while the others play their own tails; only
 * when EVERY active source is done does the window pause at `limit` and emit
 * `ended`, so the owner never replaces a source that is still playing.
 *
 * Events: timeupdate, intent, buffering, play, pause, ended, error.
 */
export default class StreamingPlayback {
  constructor(elements, duration, { wholeFile = false } = {}) {
    this.wholeFile = wholeFile;
    this.elements = elements;
    this.duration = duration;
    this.limit = duration;
    this.time = 0;
    this.playing = false;
    this.starting = false;
    this.wantsPlay = false;
    this.started = false;
    this.disposed = false;
    this.generation = 0;
    this.silent = undefined;
    this.base = elements.map(() => 0);
    this.active = elements.map(() => true);
    this.ended = elements.map(() => false);
    // The path each element is assigned (loaded or withheld until Play).
    this.sources = elements.map(() => null);
    this.pendingSources = new Map();
    this.pendingMetadata = new Set();
    this.listeners = new Map();
    this.cleanups = [];
    elements.forEach((element, index) => {
      const listen = (name, fn) => {
        const guarded = (event) => {
          // An element without a source (a lane in a gap) never drives state.
          if (this.active[index]) fn(event);
        };
        element.addEventListener(name, guarded);
        this.cleanups.push(() => element.removeEventListener(name, guarded));
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
      listen("timeupdate", () => {
        // Only the leading source moves the clock; a peer that is waiting
        // must not let it run ahead.
        if (
          !this.playing ||
          this.pendingMetadata.size ||
          this.leader() !== index
        )
          return;
        const at = this.position(index);
        // Chromium delivers a last timeupdate before its native ended event.
        // Pausing right there prevents ended from being dispatched, even with
        // every frame played (e.g. 206.5 ms container / 200 ms decoded Opus).
        // Allow its native EOF event to settle; keep mid-file boundaries exact.
        if (at >= this.limit && this.atNativeEnd(index)) return;
        if (
          !this.ready() ||
          this.running().some(
            (other) => Math.abs(this.position(other) - at) > DRIFT_TOLERANCE,
          )
        ) {
          this.hold();
          this.align();
          void this.resumeWhenReady();
          return;
        }
        // Never publish the boundary before every source has reached it.
        if (at >= this.limit) {
          this.arrive(index);
          return;
        }
        this.time = Math.min(this.duration, at);
        this.emit("timeupdate", this.time);
      });
      listen("ended", () => {
        // A queued `ended` for a source already replaced (whose successor has
        // not reported metadata yet) is stale; a source already done waits.
        if (this.pendingMetadata.has(element) || this.ended[index]) return;
        this.arrive(index);
      });
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
  // Active sources not yet done in this window (neither at its end nor run
  // out), in lane order.
  running() {
    return this.elements
      .map((_, index) => index)
      .filter((index) => this.active[index] && !this.ended[index]);
  }
  leader() {
    return this.running()[0] ?? -1;
  }
  // Element i's place on the recording timeline.
  position(index) {
    return this.base[index] + this.elements[index].currentTime;
  }
  /**
   * Source `index` reached the window's end or ran out. It is done and waits,
   * paused, at that point; so is any peer already there. While another
   * source is still short of the end, the clock stays on the earliest of
   * them and nothing advances: that source's own timeupdate/ended finish the
   * window. A seek (setTime) or new window (configure) clears every mark.
   */
  arrive(index) {
    this.ended[index] = true;
    this.elements[index].pause();
    // A source whose replacement is still loading has no position yet.
    if (this.pendingMetadata.size) return;
    this.running().forEach((other) => {
      if (this.position(other) < this.limit || this.atNativeEnd(other)) return;
      this.ended[other] = true;
      this.elements[other].pause();
    });
    const tails = this.running();
    if (!tails.length) {
      this.reachLimit();
      return;
    }
    this.time = Math.max(
      0,
      Math.min(
        this.limit,
        this.duration,
        ...tails.map((other) => this.position(other)),
      ),
    );
    this.emit("timeupdate", this.time);
  }
  atNativeEnd(index) {
    const element = this.elements[index];
    if (!Number.isFinite(element.duration) || element.duration <= 0)
      return false;
    // One whole authorized file can have a few milliseconds of container
    // padding beyond its decoded frame clock (e.g. Ogg Opus pre-skip). Let
    // the native element finish that tail within the existing alignment
    // tolerance. Never extend a segment window, gap or multitrack boundary.
    return (
      element.currentTime >= element.duration ||
      (this.wholeFile &&
        this.limit === this.duration &&
        Math.abs(element.duration - this.duration) <= ALIGN_TOLERANCE &&
        element.currentTime >= this.limit)
    );
  }
  ready() {
    return (
      !this.pendingMetadata.size &&
      this.running().every(
        (index) =>
          this.elements[index].readyState >= HAVE_FUTURE_DATA &&
          !this.elements[index].seeking,
      )
    );
  }
  align() {
    this.running().forEach((index) => {
      const element = this.elements[index];
      const local = Math.max(0, this.time - this.base[index]);
      if (
        !this.pendingMetadata.has(element) &&
        element.readyState >= 1 &&
        Math.abs(element.currentTime - local) > ALIGN_TOLERANCE
      )
        element.currentTime = local;
    });
  }
  // Keep play intent distinct from actual playback. One stalled/seeking channel
  // pauses EVERY channel at the earliest position, never skipping unheard audio.
  hold(retainPosition = true) {
    const sources = this.running();
    if (
      retainPosition &&
      (this.playing || this.starting) &&
      !this.pendingMetadata.size &&
      sources.length
    ) {
      this.time = Math.max(
        0,
        Math.min(
          this.limit,
          this.duration,
          ...sources.map((index) => this.position(index)),
        ),
      );
      this.emit("timeupdate", this.time);
    }
    this.stopSilence();
    this.generation += 1;
    this.starting = false;
    this.playing = false;
    this.elements.forEach((element, index) => {
      if (this.active[index]) element.pause();
    });
    this.emit("pause");
    this.emit("buffering", this.wantsPlay);
  }
  setTime(seconds) {
    if (this.disposed || !Number.isFinite(seconds)) return;
    this.hold(false);
    this.time = Math.max(0, Math.min(this.duration, seconds));
    // Seeking inside a window rewinds sources that were done (run out or
    // waiting at its end).
    this.ended = this.ended.map(() => false);
    this.align();
    this.emit("timeupdate", this.time);
    void this.resumeWhenReady();
  }
  seekTo(progress) {
    this.setTime(progress * this.duration);
  }
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
    if (
      this.disposed ||
      !this.wantsPlay ||
      this.playing ||
      this.starting ||
      !this.ready()
    )
      return;
    if (this.time >= this.limit) {
      this.reachLimit();
      return;
    }
    this.align();
    // Seeking to the retained clock can itself need another Range response.
    if (!this.ready()) return;
    const sources = this.running();
    if (!sources.length) {
      this.playing = true;
      this.startSilence();
      this.emit("buffering", false);
      this.emit("play");
      return;
    }
    this.starting = true;
    const generation = ++this.generation;
    try {
      // Call play in one task only AFTER every required channel can play.
      await Promise.all(sources.map((index) => this.elements[index].play()));
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
  startSilence() {
    this.stopSilence();
    let last = Date.now();
    this.silent = setInterval(() => {
      const now = Date.now();
      this.time = Math.min(this.limit, this.time + (now - last) / 1000);
      last = now;
      this.emit("timeupdate", this.time);
      if (this.time >= this.limit) this.reachLimit();
    }, SILENT_TICK_MS);
  }
  stopSilence() {
    clearInterval(this.silent);
    this.silent = undefined;
  }
  // The window is over (every source is done, the silent clock ran out, or a
  // seek landed on its end): stop exactly there and let the owner move on.
  reachLimit() {
    this.pause();
    this.time = Math.min(this.limit, this.duration);
    this.emit("ended");
  }
  pause() {
    this.wantsPlay = false;
    this.emit("intent", false);
    this.hold();
  }
  /**
   * Point every element at its source for the next window: `paths[i]` null
   * leaves element i silent (its source is dropped, freeing its buffer); an
   * unchanged path keeps its loaded bytes; a new path is withheld until Play.
   * Play intent is retained, exactly as in a renewal.
   */
  configure(paths, bases, limit, seconds) {
    if (this.disposed || paths.length !== this.elements.length) return;
    this.hold();
    paths.forEach((path, index) => {
      this.base[index] = bases?.[index] ?? 0;
      if (path === null) {
        this.release(index);
        return;
      }
      this.active[index] = true;
      if (path !== this.sources[index]) {
        this.sources[index] = path;
        this.pendingSources.set(index, path);
      }
    });
    // Chromium can request a Range despite preload=none: withhold src until Play.
    if (this.started) this.loadSources();
    this.limit = Math.min(this.duration, limit ?? this.duration);
    this.setTime(seconds ?? this.time);
  }
  replaceSources(paths) {
    if (this.disposed || paths.length !== this.elements.length) return;
    // Retain intent even if renewal happens during buffering, not just playback.
    this.configure(paths, this.base, this.limit, this.time);
  }
  release(index) {
    const element = this.elements[index];
    this.active[index] = false;
    this.pendingSources.delete(index);
    this.pendingMetadata.delete(element);
    if (this.sources[index] === null) return;
    this.sources[index] = null;
    element.pause();
    element.removeAttribute("src");
    element.load();
  }
  loadSources() {
    this.pendingSources.forEach((path, index) => {
      const element = this.elements[index];
      this.pendingMetadata.add(element);
      // After explicit play intent, buffer while paused until the shared barrier
      // opens. preload=none cannot establish readiness without starting a track.
      element.preload = "auto";
      element.src = path;
      element.load();
    });
    this.pendingSources.clear();
  }
  destroy() {
    if (this.disposed) return;
    this.pause();
    this.disposed = true;
    this.cleanups.forEach((cleanup) => cleanup());
    this.elements.forEach((element) => {
      element.removeAttribute("src");
      element.load();
    });
    this.listeners.clear();
  }
}
