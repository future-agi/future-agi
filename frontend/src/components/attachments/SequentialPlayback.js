import StreamingPlayback from "./StreamingPlayback";

// A grant must outlast the rest of its part (at most one segment's length)
// plus this margin before play.
const RENEW_MARGIN_MS = 10000;
const HORIZON_S = 30;
// Never spin on a near-expired grant. Backend must issue usable leases.
const MIN_GRANT_MS = 2000;

/**
 * Plays one logical recording behind one global clock and duration, as lanes
 * of parts on its timeline (see contract.recordingLanes): one lane for a mixed
 * recording, one per track for stereo channel derivatives or separate tracks.
 * Each lane owns ONE media element; its segments play there in order, a gap
 * keeps it silent, and lanes play simultaneously on the shared clock.
 *
 * A grant only works under the media session that issued it. Each session's
 * cookie has its own path, so another image, segment or tab exchanging a new
 * session never orphans the live grants. Only the segments under the clock
 * are ever live: they are authorized together in one request when needed (at
 * a boundary, on a seek, or when a live grant would lapse), one request at a
 * time, and no bytes are fetched ahead. Nothing is joined or decoded. Any
 * lane's denial stops every lane.
 *
 * `authorize(parts, signal, {fresh})` answers one result per part, in order.
 * The legacy single-lane form `(element, parts, duration, authorize)` keeps a
 * per-part `authorize(part, signal, {fresh})`.
 *
 * Events: timeupdate, intent, buffering, play, pause, ended, ready (first
 * grants), unavailable ({status, retryable}).
 */
export default class SequentialPlayback {
  constructor(elements, lanes, duration, authorize) {
    if (!Array.isArray(elements)) {
      // One element, one lane of parts, one part per authorization.
      const single = authorize;
      elements = [elements];
      lanes = [{ parts: lanes }];
      authorize = async (parts, signal, options) => [
        await single(parts[0], signal, options),
      ];
    }
    this.lanes = lanes.map((lane) => ({
      ...lane,
      index: 0,
      grant: null,
      parts: lane.parts.map((part) => ({
        ...part,
        start: part.start_ms / 1000,
        end: (part.start_ms + part.duration_ms) / 1000,
      })),
    }));
    this.duration = duration;
    this.authorize = authorize;
    this.controller = new AbortController();
    this.time = 0;
    this.wantsPlay = false;
    this.disposed = false;
    this.prepared = false;
    this.authorizing = false;
    this.recoveryUsed = false;
    this.freshSession = false;
    this.quiet = false;
    this.loaded = null;
    this.renewal = undefined;
    this.listeners = new Map();
    this.elements = elements;
    const onlyPart =
      this.lanes.length === 1 && this.lanes[0].parts.length === 1
        ? this.lanes[0].parts[0]
        : null;
    this.transport = new StreamingPlayback(elements, duration, {
      wholeFile:
        onlyPart?.kind === "segment" &&
        onlyPart.start === 0 &&
        onlyPart.end === duration,
    });
    // Forward only the loaded window, never echoes of our own source/seek calls.
    const live = () => !this.quiet && this.isLoaded();
    this.transport.on("timeupdate", (time) => {
      if (!live()) return;
      this.time = Math.min(this.windowEnd(), time);
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
    return !this.quiet && this.isLoaded() && this.transport.isPlaying();
  }
  partAt(lane, seconds) {
    let index = lane.parts.length - 1;
    while (index > 0 && lane.parts[index].start > seconds) index -= 1;
    return index;
  }
  part(lane) {
    return lane.parts[lane.index];
  }
  // The current window ends where the first lane changes part.
  windowEnd() {
    return Math.min(
      this.duration,
      ...this.lanes.map((lane) => this.part(lane).end),
    );
  }
  // The window's sources: each segment lane's granted path, else silence.
  window() {
    return this.lanes.map((lane) =>
      this.part(lane).kind === "segment" && lane.grant?.index === lane.index
        ? lane.grant.path
        : null,
    );
  }
  isLoaded() {
    return (
      this.loaded !== null &&
      this.lanes.every(
        (lane, index) => this.loaded.indexes[index] === lane.index,
      )
    );
  }
  // Authorize the opening segments so availability is known before Play.
  prepare() {
    void this.requestGrants();
  }
  setTime(seconds) {
    if (this.disposed || !Number.isFinite(seconds)) return;
    const time = Math.max(0, Math.min(this.duration, seconds));
    const indexes = this.lanes.map((lane) => this.partAt(lane, time));
    const moved = this.lanes.some(
      (lane, index) => lane.index !== indexes[index],
    );
    this.time = time;
    this.lanes.forEach((lane, index) => {
      lane.index = indexes[index];
    });
    if (time >= this.duration && this.wantsPlay) {
      // Seeking to the very end while playing ends the recording.
      clearTimeout(this.renewal);
      this.finish();
      return;
    }
    if (moved) {
      clearTimeout(this.renewal);
      if (!this.isLoaded()) this.quietly(() => this.transport.pause());
    }
    if (this.isLoaded()) {
      this.quietly(() => this.transport.setTime(time));
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
    this.emit("intent", false);
    if (!this.disposed) this.transport.pause();
    this.emit("pause");
    this.emit("buffering", false);
  }
  async ensure() {
    if (this.disposed || !this.wantsPlay) return;
    if (!this.covered()) {
      this.emit("buffering", true);
      await this.requestGrants();
      return;
    }
    const paths = this.window();
    if (
      !this.isLoaded() ||
      paths.some((path, index) => path !== this.loaded.paths[index])
    )
      this.load();
    this.scheduleRenewal();
    await this.transport.play();
  }
  // This lane's segment under the clock holds a grant that outlasts the rest
  // of its part (capped at one segment's length) plus the renewal margin.
  covers(lane) {
    const part = this.part(lane);
    return (
      part.kind !== "segment" ||
      (lane.grant?.index === lane.index &&
        lane.grant.expiresAt - Date.now() >=
          Math.min(HORIZON_S, part.end - this.time) * 1000 + RENEW_MARGIN_MS)
    );
  }
  covered() {
    return this.lanes.every((lane) => this.covers(lane));
  }
  // Every segment under the clock has a grant to load, however short.
  granted() {
    return this.lanes.every(
      (lane) =>
        this.part(lane).kind !== "segment" || lane.grant?.index === lane.index,
    );
  }
  // The parts to authorize next: each segment under the clock whose grant
  // would lapse, or before the first grants, each lane's first segment after
  // an opening gap.
  targets() {
    const targets = [];
    this.lanes.forEach((lane, laneIndex) => {
      let index = lane.index;
      if (lane.parts[index].kind !== "segment") {
        if (this.prepared) return;
        index = lane.parts.findIndex(
          (part, at) => at > lane.index && part.kind === "segment",
        );
        if (index < 0) return;
      } else if (this.covers(lane)) return;
      targets.push({ lane: laneIndex, index });
    });
    return targets;
  }
  // Single flight, latest targets win: one request at a time keeps exactly
  // the window's grants live and never authorizes a part the clock already
  // left. Every lane's result must be ready, or nothing plays.
  async requestGrants() {
    if (this.authorizing || this.disposed) return;
    this.authorizing = true;
    try {
      for (;;) {
        const targets = this.targets();
        if (!targets.length) return;
        const parts = targets.map(
          ({ lane, index }) => this.lanes[lane].parts[index],
        );
        let results;
        // Each request consumes the recovery's new-session ask, so finding
        // it set again after the await means recover() ran while this
        // request was out on the refused session.
        const fresh = this.freshSession;
        this.freshSession = false;
        try {
          results = await this.authorize(parts, this.controller.signal, {
            fresh,
          });
        } catch {
          if (this.disposed) return;
          if (this.freshSession) continue;
          this.fail("not_found", true);
          return;
        }
        if (this.disposed) return;
        // Stale: whatever it answered (grants on the refused session, or a
        // refusal) is dropped unread, and this same flight now sends the ONE
        // new-session request; recover() could not, as this flight held it.
        if (this.freshSession) continue;
        const grants = this.accept(parts, results);
        if (!grants) return;
        const now = this.targets();
        if (!sameTargets(targets, now)) {
          // The clock moved meanwhile. A paused seek waits for Play, and a
          // covered window is already playing; otherwise authorize it now.
          if (this.prepared && (!this.wantsPlay || this.covered())) return;
          continue;
        }
        targets.forEach(({ lane, index }, at) => {
          this.lanes[lane].grant = { index, ...grants[at] };
        });
        if (!this.prepared) {
          this.prepared = true;
          this.emit("ready");
        }
        if (this.granted()) {
          this.load();
          if (this.wantsPlay) {
            this.scheduleRenewal();
            void this.transport.play();
          }
        }
        // A seek from a `ready` listener found this flight busy: serve it.
        if (this.wantsPlay && !sameTargets(targets, this.targets())) continue;
        return;
      }
    } finally {
      this.authorizing = false;
    }
  }
  // Never skip a denied, deleted or expired segment, and never play the
  // allowed half of a denied pair: any bad result stops every lane.
  accept(parts, results) {
    if (!Array.isArray(results) || results.length !== parts.length) {
      this.fail("not_found", false);
      return null;
    }
    const denied = results.find((result) => result?.status !== "ready");
    if (denied) {
      this.fail(denied?.status || "not_found", false);
      return null;
    }
    const grants = results.map((result) => ({
      path: result.gateway_path,
      expiresAt: Date.parse(result.expires_at),
    }));
    if (
      results.some(
        (result, at) =>
          result.occurrence_id !== parts[at].occurrence_id ||
          !(grants[at].expiresAt - Date.now() >= MIN_GRANT_MS),
      ) ||
      // Each channel/track is its own grant; one path never serves two.
      new Set(grants.map((grant) => grant.path)).size !== grants.length
    ) {
      this.fail("not_found", false);
      return null;
    }
    return grants;
  }
  load() {
    const paths = this.window();
    // Withheld until Play, as before; a lane's previous source is replaced,
    // an unchanged one keeps its bytes, and a lane in a gap is released.
    this.quietly(() =>
      this.transport.configure(
        paths,
        this.lanes.map((lane) => this.part(lane).start),
        this.windowEnd(),
        this.time,
      ),
    );
    this.loaded = { indexes: this.lanes.map((lane) => lane.index), paths };
  }
  scheduleRenewal() {
    clearTimeout(this.renewal);
    const expiries = this.lanes
      .filter(
        (lane) =>
          this.part(lane).kind === "segment" &&
          lane.grant?.index === lane.index,
      )
      .map((lane) => lane.grant.expiresAt);
    if (!expiries.length || !this.wantsPlay || this.disposed) return;
    this.renewal = setTimeout(
      () => void this.requestGrants(),
      Math.min(
        2147483647,
        Math.max(1000, Math.min(...expiries) - Date.now() - RENEW_MARGIN_MS),
      ),
    );
  }
  // The clock reached the window's end: move every lane that changes part.
  advance() {
    clearTimeout(this.renewal);
    const end = this.windowEnd();
    if (end >= this.duration) {
      this.finish();
      return;
    }
    this.time = end;
    this.lanes.forEach((lane) => {
      lane.index = this.partAt(lane, end);
    });
    this.emit("timeupdate", this.time);
    if (this.wantsPlay) void this.ensure();
  }
  finish() {
    this.wantsPlay = false;
    this.time = this.duration;
    this.quietly(() => this.transport.pause());
    this.emit("intent", false);
    this.emit("timeupdate", this.time);
    this.emit("buffering", false);
    this.emit("pause");
    this.emit("ended");
  }
  // One bounded reauthorization of the whole window, never a provider URL
  // retry. Like the single file player, a media error pauses; Play resumes on
  // the renewed grants. The refused session may have lost its cookie, so ask
  // for a new session.
  recover() {
    if (this.recoveryUsed) {
      this.fail("not_found", true);
      return;
    }
    this.recoveryUsed = true;
    this.freshSession = true;
    this.pause();
    // A refused session refuses every lane on it: the other lanes' errors
    // belong to this same recovery, so nothing loaded is live until the
    // fresh grants replace it.
    this.loaded = null;
    this.lanes.forEach((lane) => {
      lane.grant = null;
    });
    // With a request already out (e.g. a renewal on the refused session),
    // this returns at once; that flight drops its answer when it lands and
    // sends the new-session request itself.
    void this.requestGrants();
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
    this.controller.abort();
    this.transport.destroy();
  }
}

function sameTargets(a, b) {
  return (
    a.length === b.length &&
    a.every(
      (target, at) =>
        target.lane === b[at].lane && target.index === b[at].index,
    )
  );
}
