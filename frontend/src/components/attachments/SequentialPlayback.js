import StreamingPlayback from "./StreamingPlayback";

// A grant must outlast the rest of its segment plus this margin before play.
const RENEW_MARGIN_MS = 10000;
// Never spin on a near-expired grant. Backend must issue usable leases.
const MIN_GRANT_MS = 2000;
const GAP_TICK_MS = 250;

/**
 * Plays the distinct segment occurrences of one logical recording in timeline
 * order on ONE media element, behind one global clock and duration.
 *
 * Every media-session exchange replaces the browser's single media cookie,
 * and a grant only works under the session that issued it. So exactly one
 * segment is ever live: the next one is authorized when it is needed (at its
 * boundary, on a seek, or when the live grant would lapse), one request at a
 * time, and its bytes are never fetched ahead. Nothing is joined or decoded.
 * Gaps stay silent time on the clock; a denial anywhere stops everything.
 *
 * Events: timeupdate, intent, buffering, play, pause, ended, ready (first
 * grant), unavailable ({status, retryable}).
 */
export default class SequentialPlayback {
  constructor(element, parts, duration, authorize) {
    this.parts = parts.map((part) => ({
      ...part,
      start: part.start_ms / 1000,
      end: (part.start_ms + part.duration_ms) / 1000,
    }));
    this.duration = duration;
    this.authorize = authorize;
    this.controller = new AbortController();
    this.time = 0;
    this.index = 0;
    this.wantsPlay = false;
    this.disposed = false;
    this.prepared = false;
    this.authorizing = false;
    this.recoveryUsed = false;
    this.quiet = false;
    this.grant = null;
    this.loadedIndex = -1;
    this.loadedPath = null;
    this.renewal = undefined;
    this.gapTimer = undefined;
    this.listeners = new Map();
    this.elements = [element];
    const first = this.parts.find((part) => part.kind === "segment");
    this.transport = new StreamingPlayback([element], first.end - first.start);
    // Forward only the live segment, never echoes of our own source/seek calls.
    const live = () => !this.quiet && this.loadedIndex === this.index;
    this.transport.on("timeupdate", (local) => {
      if (!live()) return;
      const part = this.parts[this.index];
      this.time = Math.min(part.end, part.start + local);
      this.emit("timeupdate", this.time);
    });
    this.transport.on("buffering", (value) => {
      if (live()) this.emit("buffering", value && this.wantsPlay);
    });
    this.transport.on("play", () => {
      if (live()) this.emit("play");
    });
    this.transport.on("ended", () => {
      if (live()) this.advance();
    });
    this.transport.on("error", () => {
      if (live()) this.recover();
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
    return (
      this.gapTimer !== undefined ||
      (!this.quiet &&
        this.loadedIndex === this.index &&
        this.transport.isPlaying())
    );
  }
  partAt(seconds) {
    let index = this.parts.length - 1;
    while (index > 0 && this.parts[index].start > seconds) index -= 1;
    return index;
  }
  // Authorize the opening segment so availability is known before Play.
  prepare() {
    void this.requestGrant();
  }
  setTime(seconds) {
    if (this.disposed || !Number.isFinite(seconds)) return;
    const time = Math.max(0, Math.min(this.duration, seconds));
    const index = this.partAt(time);
    const moved = index !== this.index;
    this.time = time;
    this.index = index;
    if (moved) {
      clearTimeout(this.renewal);
      this.stopGap();
      if (this.loadedIndex !== index) this.transport.pause();
    }
    const part = this.parts[index];
    if (part.kind === "segment" && this.loadedIndex === index) {
      this.quietly(() => this.transport.setTime(time - part.start));
      if (this.wantsPlay) this.emit("buffering", !this.transport.isPlaying());
    }
    this.emit("timeupdate", time);
    if (moved && this.wantsPlay) void this.ensure();
  }
  quietly(action) {
    this.quiet = true;
    try {
      action();
    } finally {
      this.quiet = false;
    }
  }
  seekTo(progress) {
    this.setTime(progress * this.duration);
  }
  play() {
    if (this.disposed) return Promise.resolve();
    if (this.time >= this.duration) this.setTime(0);
    this.wantsPlay = true;
    this.emit("intent", true);
    return this.ensure();
  }
  pause() {
    this.wantsPlay = false;
    clearTimeout(this.renewal);
    this.stopGap();
    this.emit("intent", false);
    if (!this.disposed) this.transport.pause();
    this.emit("pause");
    this.emit("buffering", false);
  }
  async ensure() {
    if (this.disposed || !this.wantsPlay) return;
    const part = this.parts[this.index];
    if (part.kind === "gap") {
      this.startGap();
      return;
    }
    if (!this.covers()) {
      this.emit("buffering", true);
      await this.requestGrant();
      return;
    }
    if (this.loadedIndex !== this.index || this.loadedPath !== this.grant.path)
      this.load();
    this.scheduleRenewal();
    await this.transport.play();
  }
  covers() {
    const part = this.parts[this.index];
    return (
      this.grant?.index === this.index &&
      this.grant.expiresAt - Date.now() >=
        (part.end - this.time) * 1000 + RENEW_MARGIN_MS
    );
  }
  // The segment to authorize next: the one under the clock, or before the
  // first grant, the first segment after an opening gap.
  authTarget() {
    if (this.parts[this.index].kind === "segment") return this.index;
    if (this.prepared) return -1;
    return this.parts.findIndex(
      (part, index) => index > this.index && part.kind === "segment",
    );
  }
  // Single flight, latest target wins: overlapping exchanges could leave the
  // cookie and the grant on different media sessions.
  async requestGrant() {
    if (this.authorizing || this.disposed) return;
    this.authorizing = true;
    try {
      for (;;) {
        const index = this.authTarget();
        if (index < 0) return;
        const part = this.parts[index];
        let result;
        try {
          result = await this.authorize(part, this.controller.signal);
        } catch {
          if (!this.disposed) this.fail("not_found", true);
          return;
        }
        if (this.disposed) return;
        // Never skip a denied, deleted or expired segment.
        if (result?.status !== "ready") {
          this.fail(result?.status || "not_found", false);
          return;
        }
        const expiresAt = Date.parse(result.expires_at);
        if (
          result.occurrence_id !== part.occurrence_id ||
          !(expiresAt - Date.now() >= MIN_GRANT_MS)
        ) {
          this.fail("not_found", false);
          return;
        }
        if (index !== this.authTarget()) {
          // The clock moved meanwhile. A paused seek waits for Play, and a
          // covered target is already playing; otherwise authorize it now.
          if (this.prepared && (!this.wantsPlay || this.covers())) return;
          continue;
        }
        this.grant = { index, path: result.gateway_path, expiresAt };
        if (!this.prepared) {
          this.prepared = true;
          this.emit("ready");
        }
        if (index === this.index) {
          this.load();
          if (this.wantsPlay) {
            this.scheduleRenewal();
            void this.transport.play();
          }
        }
        // A seek from a `ready` listener found this flight busy: serve it.
        const target = this.authTarget();
        if (this.wantsPlay && target >= 0 && target !== index) continue;
        return;
      }
    } finally {
      this.authorizing = false;
    }
  }
  load() {
    const part = this.parts[this.index];
    // Withheld until Play, as before; the previous segment's source is replaced.
    this.quietly(() =>
      this.transport.replaceSegment(
        [this.grant.path],
        part.end - part.start,
        this.time - part.start,
      ),
    );
    this.loadedIndex = this.index;
    this.loadedPath = this.grant.path;
  }
  scheduleRenewal() {
    clearTimeout(this.renewal);
    if (!this.grant || !this.wantsPlay || this.disposed) return;
    this.renewal = setTimeout(
      () => void this.requestGrant(),
      Math.min(
        2147483647,
        Math.max(1000, this.grant.expiresAt - Date.now() - RENEW_MARGIN_MS),
      ),
    );
  }
  advance() {
    clearTimeout(this.renewal);
    const next = this.index + 1;
    if (next >= this.parts.length) {
      this.finish();
      return;
    }
    this.index = next;
    this.time = this.parts[next].start;
    this.emit("timeupdate", this.time);
    if (this.wantsPlay) void this.ensure();
  }
  finish() {
    this.wantsPlay = false;
    this.time = this.duration;
    this.emit("intent", false);
    this.emit("timeupdate", this.time);
    this.emit("buffering", false);
    this.emit("pause");
    this.emit("ended");
  }
  // A recording gap plays as silence for its real length, keeping every later
  // segment at its own start_ms rather than closing the gap.
  startGap() {
    if (this.gapTimer !== undefined) return;
    let last = Date.now();
    this.emit("buffering", false);
    this.emit("play");
    this.gapTimer = setInterval(() => {
      const part = this.parts[this.index];
      const now = Date.now();
      this.time = Math.min(part.end, this.time + (now - last) / 1000);
      last = now;
      this.emit("timeupdate", this.time);
      if (this.time >= part.end) {
        this.stopGap();
        this.advance();
      }
    }, GAP_TICK_MS);
  }
  stopGap() {
    clearInterval(this.gapTimer);
    this.gapTimer = undefined;
  }
  // One bounded reauthorization, never a provider URL retry. Like the single
  // file player, a media error pauses; Play resumes on the renewed grant.
  recover() {
    if (this.recoveryUsed) {
      this.fail("not_found", true);
      return;
    }
    this.recoveryUsed = true;
    this.pause();
    this.grant = null;
    void this.requestGrant();
  }
  fail(status, retryable) {
    if (this.disposed) return;
    this.wantsPlay = false;
    this.emit("unavailable", { status, retryable });
    this.destroy();
  }
  destroy() {
    if (this.disposed) return;
    this.disposed = true;
    this.wantsPlay = false;
    this.listeners.clear();
    clearTimeout(this.renewal);
    this.stopGap();
    this.controller.abort();
    this.transport.destroy();
  }
}
