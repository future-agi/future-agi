// Fixed attributes of the Omega frame (see EmbeddedSurface).
// - No `sandbox`: an opaque sandbox sends `Origin: null`, which Studio refuses.
// - allow: lets the frame reach servers on this computer (a local Studio).
//   clipboard-write enables Copy actions, scoped by default to the frame's
//   own src origin.
// - The class keeps every session-replay recorder (PostHog, rrweb) out of it.
export const FRAME_ALLOW =
  "local-network-access; loopback-network; clipboard-write";
export const FRAME_CLASS = "ph-no-capture rr-block";
