import { create } from "src/utils/zustand";

/**
 * Voice drawer audio sync store.
 *
 * The audio player captures the wavesurfer-multitrack instance on ready and
 * publishes time updates here. The transcript reads `currentTime` + calls
 * `seekTo` to jump playback. One store per mounted voice drawer — `reset` is
 * called on drawer close/open to clear stale refs.
 */
const useVoiceAudioStore = create((set, get) => ({
  multitrack: null,
  wavesurfers: [],
  currentTime: 0,
  duration: 0,
  isPlaying: false,
  // A seek requested before the audio bridge has mounted (e.g. user
  // clicked ▶ on a checklist step while on the Graph tab). The audio
  // bridge consumes this on first ready so the seek still lands.
  pendingSeek: null,

  setInstance: ({ multitrack, wavesurfers }) => {
    set({ multitrack, wavesurfers });
    const pending = get().pendingSeek;
    if (pending != null) {
      // Clear first so a re-entry doesn't loop.
      set({ pendingSeek: null });
      setTimeout(() => get().seekTo(pending), 0);
    }
  },

  setCurrentTime: (time) => {
    const prev = get().currentTime;
    // 30ms tolerance — tighter than 50ms so highlight transitions feel
    // responsive at ~33fps; looser than 16ms to avoid re-render churn.
    if (Math.abs((prev || 0) - (time || 0)) < 0.03) return;
    set({ currentTime: time });
  },

  setDuration: (duration) => set({ duration }),

  setIsPlaying: (playing) => set({ isPlaying: playing }),

  seekTo: (time) => {
    const { multitrack, wavesurfers } = get();
    // If the bridge hasn't mounted yet (e.g. user is on a path tab and
    // the Transcript tab has never been visited), queue the seek.
    if (!multitrack && !(wavesurfers?.length > 0)) {
      set({ pendingSeek: time });
      return;
    }
    let seeked = false;
    // Try multitrack.setTime(seconds) first — the public API.
    if (multitrack?.setTime) {
      try {
        multitrack.setTime(time);
        seeked = true;
      } catch {
        /* fall through */
      }
    }
    // Try multitrack.seekTo(0..1 progress) as secondary.
    if (!seeked && multitrack?.seekTo) {
      try {
        const duration = get().duration;
        if (duration > 0) {
          multitrack.seekTo(Math.max(0, Math.min(1, time / duration)));
          seeked = true;
        }
      } catch {
        /* fall through */
      }
    }
    // Last resort: set time on each sub-wavesurfer individually.
    if (!seeked) {
      (wavesurfers || []).forEach((ws) => {
        try {
          ws?.setTime?.(time);
        } catch {
          /* ignore */
        }
      });
    }
    set({ currentTime: time });
  },

  reset: () =>
    set({
      multitrack: null,
      wavesurfers: [],
      currentTime: 0,
      duration: 0,
      isPlaying: false,
      pendingSeek: null,
    }),
}));

export default useVoiceAudioStore;
