// The check list itself comes from GET /api/setup-checks/ — nothing about it
// lives here, so checks can change server-side without a frontend release.

// Mirrors the server's launch modes. Sent as the `mode` query param.
export const LAUNCH_MODE = {
  LIVE: "live",
  EXPERIMENT: "experiment",
};

export const LAUNCH_MODES = [
  {
    id: LAUNCH_MODE.LIVE,
    title: "Production",
    description:
      "You're going live for real missions. Every security and infrastructure system is checked before liftoff.",
    icon: "solar:rocket-2-bold",
  },
  {
    id: LAUNCH_MODE.EXPERIMENT,
    title: "Test flight",
    description:
      "You're taking it for a spin locally. A few non-critical systems are eased so you're off the ground in minutes.",
    icon: "solar:test-tube-bold",
  },
];

export const DEFAULT_LAUNCH_MODE = LAUNCH_MODE.LIVE;

export const MODE_NOTE = {
  [LAUNCH_MODE.LIVE]:
    "Every system has to pass pre-flight before you're cleared for launch.",
  [LAUNCH_MODE.EXPERIMENT]:
    "Cautions won't hold you on the ground during a test flight.",
};

// Mirrors the server enum. No status gates the flow.
export const CHECK_STATUS = {
  PENDING: "pending",
  PASSED: "passed",
  WARNING: "warning",
  FAILED: "failed",
  SKIPPED: "skipped",
};

// Derived from the transport, not from any check.
export const CONNECTION_STATE = {
  CONNECTING: "connecting",
  REACHABLE: "reachable",
  UNREACHABLE: "unreachable",
};

export const CHECK_REVEAL_STAGGER_MS = 350;
