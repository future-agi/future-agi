// One audio at a time, app-wide. Every media player (cell
// players, the Call / Audio lens clocks, CallView) claims the bus before it
// plays; the previous owner's `stop` runs. Leaving a page unmounts its
// players, whose cleanup releases the bus. Pure module state, no React.
//
//   const release = claimAudio(id, () => pause());   // before play()
//   releaseAudio(id);                                 // on pause / unmount
//   onAudioChange(fn) → unsubscribe                   // for "which plays"

let current = null; // {id, stop}
const listeners = new Set();

const emit = () => listeners.forEach((fn) => fn(current?.id ?? null));

/** Become the one playing source; stops whoever played before. */
export function claimAudio(id, stop) {
  if (current && current.id !== id) {
    const prev = current;
    current = null;
    try {
      prev.stop?.();
    } catch {
      /* a torn-down player */
    }
  }
  current = { id, stop };
  emit();
  return () => releaseAudio(id);
}

/** Give the bus up (only if `id` still holds it). */
export function releaseAudio(id) {
  if (current?.id !== id) return;
  current = null;
  emit();
}

/** Stop whatever plays (route change, `Esc` on a lightbox). */
export function stopAllAudio() {
  if (!current) return;
  const prev = current;
  current = null;
  try {
    prev.stop?.();
  } catch {
    /* ignore */
  }
  emit();
}

export const playingAudioId = () => current?.id ?? null;

export function onAudioChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
