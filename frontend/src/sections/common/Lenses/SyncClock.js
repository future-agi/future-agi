// One clock for 2–4 call lanes (R4 lane M, MUST 0). Each lane is one
// `MultiTrackAudioPlayer` instance (its documented [caller, agent] pair);
// the clock drives every lane's play / pause / seek / rate and corrects
// drift. No React, no DOM of its own: it only touches the multitrack
// instances and media elements the players hand over through `onInstance`.
//
// Why media elements and not `multitrack.setTime` for drift: the multitrack
// only moves a media element when it is more than 0.3 s away from the asked
// time, so a 0.08–0.3 s drift would never be corrected through it.
//
//   const clock = createSyncClock();
//   clock.register(laneId, {multitrack, wavesurfers});   // from onInstance
//   clock.play(); clock.seek(12); clock.setRate(1.5);
//   clock.seekLanes({a: 24.7, b: 22.4});                  // Align on turns
//   const off = clock.subscribe(() => …); clock.time();

const DEFAULTS = { driftMs: 80, checkMs: 500, tickMs: 100 };

const mediaOf = (ws) => {
  try {
    return ws?.getMediaElement?.() ?? null;
  } catch {
    return null;
  }
};

/**
 * @param {{driftMs?: number, checkMs?: number, tickMs?: number,
 *   now?: () => number, setInterval?: Function, clearInterval?: Function}} [opts]
 */
export function createSyncClock(opts = {}) {
  const cfg = { ...DEFAULTS, ...opts };
  const now =
    cfg.now ??
    (() =>
      typeof performance !== "undefined" ? performance.now() : Date.now());
  const setIv = cfg.setInterval ?? ((fn, ms) => setInterval(fn, ms));
  const clearIv = cfg.clearInterval ?? ((id) => clearInterval(id));

  const lanes = new Map(); // id -> {multitrack, medias, duration, offset, gain, muted}
  const listeners = new Set();
  let playing = false;
  let rate = 1;
  let anchorTime = 0; // master seconds at anchorAt
  let anchorAt = now();
  let tickId = null;
  let checkId = null;
  let solo = null;
  let loop = null; // {start: {laneId: s}, end: s (master)}
  const stats = { corrections: 0, checks: 0 };

  const emit = () => listeners.forEach((fn) => fn());

  /** The lane whose media is the master: the longest one that can play. */
  const anchorLane = () => {
    let best = null;
    lanes.forEach((l, id) => {
      if (!l.medias.length) return;
      if (!best || l.duration > best.l.duration) best = { id, l };
    });
    return best;
  };

  const virtualTime = () =>
    playing ? anchorTime + ((now() - anchorAt) / 1000) * rate : anchorTime;

  /** Master time (seconds). Follows the anchor lane's media while it plays. */
  const time = () => {
    const a = anchorLane();
    if (playing && a) {
      const m = a.l.medias[a.l.medias.length - 1];
      const t = m?.currentTime;
      if (Number.isFinite(t) && !m.paused) return t - a.l.offset;
    }
    return virtualTime();
  };

  const duration = () => {
    let d = 0;
    lanes.forEach((l) => {
      d = Math.max(d, (l.duration || 0) - l.offset);
    });
    return d;
  };

  const laneTime = (l, master) => Math.max(0, master + l.offset);

  const applyVolume = (id, l) => {
    const audible = !l.muted && (solo == null || solo === id);
    l.medias.forEach((m) => {
      m.volume = Math.max(0, Math.min(1, l.gain ?? 1));
      m.muted = !audible || Boolean(m.dataset?.forceMuted);
    });
  };

  const placeLane = (l, master) => {
    const lt = laneTime(l, master);
    l.medias.forEach((m) => {
      if (Number.isFinite(lt)) m.currentTime = Math.min(lt, l.duration || lt);
    });
    try {
      l.multitrack?.setTime?.(Math.min(lt, l.duration || lt));
    } catch {
      /* not ready */
    }
  };

  const playLane = (l, master) => {
    const lt = laneTime(l, master);
    if (l.duration && lt >= l.duration - 0.05) return;
    l.medias.forEach((m) => {
      m.playbackRate = rate;
    });
    try {
      l.multitrack?.play?.();
    } catch {
      /* ignore */
    }
  };

  const stopTimers = () => {
    if (tickId != null) clearIv(tickId);
    if (checkId != null) clearIv(checkId);
    tickId = null;
    checkId = null;
  };

  const check = () => {
    if (!playing) return;
    stats.checks += 1;
    const master = time();
    const a = anchorLane();
    lanes.forEach((l, id) => {
      if (a && id === a.id) return;
      const lt = laneTime(l, master);
      if (!l.medias.length || (l.duration && lt >= l.duration)) return;
      const actual = l.medias[l.medias.length - 1].currentTime;
      if (Math.abs(actual - lt) * 1000 > cfg.driftMs) {
        stats.corrections += 1;
        placeLane(l, master);
      }
    });
  };

  const tick = () => {
    const t = time();
    if (loop && t >= loop.end) {
      api.seekLanes(loop.start);
      return;
    }
    if (t >= duration() - 0.05 && duration() > 0) {
      api.pause();
      anchorTime = duration();
    }
    emit();
  };

  const api = {
    /** Hand over one lane's instance (MultiTrackAudioPlayer `onInstance`). */
    register(id, { multitrack, wavesurfers } = {}) {
      const medias = (wavesurfers ?? []).map(mediaOf).filter(Boolean);
      const duration = Math.max(
        0,
        ...(wavesurfers ?? []).map((w) => {
          try {
            return w?.getDuration?.() || 0;
          } catch {
            return 0;
          }
        }),
      );
      const prev = lanes.get(id);
      const lane = {
        multitrack,
        medias,
        duration,
        offset: prev?.offset ?? 0,
        gain: prev?.gain ?? 1,
        muted: prev?.muted ?? false,
      };
      lanes.set(id, lane);
      applyVolume(id, lane);
      placeLane(lane, anchorTime);
      if (playing) playLane(lane, time());
      emit();
      return () => api.unregister(id, multitrack);
    },
    unregister(id, multitrack) {
      const l = lanes.get(id);
      if (!l || (multitrack && l.multitrack !== multitrack)) return;
      try {
        l.multitrack?.pause?.();
      } catch {
        /* torn down */
      }
      lanes.delete(id);
      emit();
    },
    lanes: () => [...lanes.keys()],
    /** The lane whose media sets master time (the longest one). */
    anchorId: () => anchorLane()?.id ?? null,
    ready: (id) => lanes.has(id),
    isPlaying: () => playing,
    rate: () => rate,
    solo: () => solo,
    time,
    duration,
    offsets: () =>
      Object.fromEntries([...lanes].map(([id, l]) => [id, l.offset])),
    /** Each lane's own position (seconds) right now. */
    laneTimes: () =>
      Object.fromEntries(
        [...lanes].map(([id, l]) => [
          id,
          l.medias.length
            ? l.medias[l.medias.length - 1].currentTime
            : laneTime(l, time()),
        ]),
      ),
    stats: () => ({ ...stats }),
    /** Debug / walk access to each lane's media elements. */
    mediasOf: (id) => lanes.get(id)?.medias ?? [],
    play() {
      if (playing) return;
      const d = duration();
      if (d && anchorTime >= d - 0.05) anchorTime = 0;
      lanes.forEach((l) => placeLane(l, anchorTime));
      playing = true;
      anchorAt = now();
      lanes.forEach((l) => playLane(l, anchorTime));
      stopTimers();
      tickId = setIv(tick, cfg.tickMs);
      checkId = setIv(check, cfg.checkMs);
      emit();
    },
    pause() {
      if (!playing) return;
      anchorTime = time();
      playing = false;
      stopTimers();
      lanes.forEach((l) => {
        try {
          l.multitrack?.pause?.();
        } catch {
          /* ignore */
        }
      });
      emit();
    },
    toggle() {
      if (playing) api.pause();
      else api.play();
    },
    /** Every lane to the same master time (offsets back to 0). */
    seek(t) {
      lanes.forEach((l) => {
        l.offset = 0;
      });
      api.seekMaster(t);
    },
    /** Master time, keeping each lane's offset. */
    seekMaster(t) {
      anchorTime = Math.max(0, Number(t) || 0);
      anchorAt = now();
      lanes.forEach((l) => placeLane(l, anchorTime));
      if (playing) lanes.forEach((l) => playLane(l, anchorTime));
      emit();
    },
    /**
     * Align on turns: each lane to its own time (`{laneId: seconds}`); the
     * lanes keep those offsets while playing.
     */
    seekLanes(byLane) {
      const entries = Object.entries(byLane ?? {}).filter(
        ([id, t]) => lanes.has(id) && Number.isFinite(t),
      );
      if (!entries.length) return;
      const a = anchorLane();
      const ref = entries.find(([id]) => id === a?.id) ?? entries[0];
      const master = ref[1];
      lanes.forEach((l, id) => {
        const t = byLane[id];
        l.offset = Number.isFinite(t) ? t - master : 0;
      });
      // The reference lane is the anchor: its offset is 0 by definition.
      if (lanes.get(ref[0])) lanes.get(ref[0]).offset = 0;
      api.seekMaster(master);
    },
    setRate(r) {
      anchorTime = time();
      anchorAt = now();
      rate = Number(r) || 1;
      lanes.forEach((l) =>
        l.medias.forEach((m) => {
          m.playbackRate = rate;
        }),
      );
      emit();
    },
    setSolo(id) {
      solo = id ?? null;
      lanes.forEach((l, lid) => applyVolume(lid, l));
      emit();
    },
    setGain(id, gain) {
      const l = lanes.get(id);
      if (!l) return;
      l.gain = gain;
      applyVolume(id, l);
      emit();
    },
    /** `{start: {laneId: s}, end: masterSeconds}` or null. */
    setLoop(next) {
      loop = next ?? null;
      emit();
    },
    loop: () => loop,
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    destroy() {
      api.pause();
      stopTimers();
      lanes.clear();
      listeners.clear();
    },
  };
  return api;
}

export default createSyncClock;
