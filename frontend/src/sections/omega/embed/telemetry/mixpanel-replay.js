import { scrubEmbedSecrets } from "./guard";

const guardedConstructors = new WeakMap();

const guardRecorder = (Recorder) => {
  if (Recorder === undefined) return undefined;
  if (typeof Recorder !== "function")
    throw new Error("Unsupported replay recorder");
  if (guardedConstructors.has(Recorder))
    return guardedConstructors.get(Recorder);

  function GuardedRecorder(...args) {
    const recorder = new Recorder(...args);
    if (
      !Array.isArray(recorder.recEvents) ||
      typeof recorder._flushEvents !== "function"
    ) {
      throw new Error("Unsupported replay queue");
    }
    const flush = recorder._flushEvents;
    recorder._flushEvents = function flushSanitizedReplay(...flushArgs) {
      try {
        if (!Array.isArray(this.recEvents))
          throw new Error("Unreadable replay queue");
        // 2.53.0 serializes this queue in _flushEvents, then optionally gzips
        // it. Normalize first so URL/class/toJSON values cannot escape later.
        // This also covers the final flush inside stopRecording/resetRecording.
        this.recEvents = scrubEmbedSecrets(
          JSON.parse(JSON.stringify(this.recEvents)),
        );
      } catch {
        // Discard unreadable events without logging credential-bearing errors.
        this.recEvents = [];
        return undefined;
      }
      return flush.apply(this, flushArgs);
    };
    return recorder;
  }
  GuardedRecorder.prototype = Recorder.prototype;
  guardedConstructors.set(Recorder, GuardedRecorder);
  guardedConstructors.set(GuardedRecorder, GuardedRecorder);
  return GuardedRecorder;
};

// Installed 2.53.0 (including its minified bundle) publishes __mp_recorder
// before the SDK's script onload constructs it. Guard both an already-loaded
// constructor and a later assignment BEFORE recording can begin. Waiting for
// a route change/stop is too late: stop itself uploads the unsent queue.
export function installMixpanelReplayGuard() {
  if (typeof window === "undefined") return false;
  try {
    let Recorder = guardRecorder(window.__mp_recorder);
    Object.defineProperty(window, "__mp_recorder", {
      configurable: true,
      enumerable: true,
      get: () => Recorder,
      set: (next) => {
        Recorder = guardRecorder(next);
      },
    });
    return true;
  } catch {
    // No inspectable recorder boundary: leave product events on, replay off.
    return false;
  }
}
