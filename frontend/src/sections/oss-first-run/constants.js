// OSS first-run setup flow — static definitions.
//
// Check labels, statuses and `required` all come from GET /api/setup-checks/.
// Nothing about the check list lives here: the endpoint is list-shaped so checks
// can be added or removed server-side without a frontend change.

export const LAUNCH_MODES = [
  {
    id: "live",
    title: "Live implementation",
    description:
      "Production-ready. All security and infrastructure requirements are enforced.",
    icon: "solar:rocket-2-bold",
  },
  {
    id: "experiment",
    title: "Just experimenting",
    description:
      "Explore locally. Some security requirements are relaxed so you can get started fast.",
    icon: "solar:test-tube-bold",
  },
];

export const DEFAULT_LAUNCH_MODE = "live";

export const MODE_NOTE = {
  live: "All security requirements will be enforced for a live implementation.",
  experiment:
    "We will not enforce some security requirements in experimentation mode.",
};

// Per-check status, mirroring the server enum. `skipped` means the check does
// not apply in the selected launch mode and must never block. Presentation
// (icon/colour/label) lives with the component that renders it.
export const CHECK_STATUS = {
  PENDING: "pending",
  PASSED: "passed",
  WARNING: "warning",
  FAILED: "failed",
  SKIPPED: "skipped",
};

// Whether we can reach the server at all, which is a separate axis from what any
// individual check reports. A server cannot tell us it is unreachable, so this is
// derived from the transport and rendered once, above the list.
export const CONNECTION_STATE = {
  CONNECTING: "connecting",
  REACHABLE: "reachable",
  UNREACHABLE: "unreachable",
};

// Reveal delay between rows. Purely a display animation over ONE response, not
// nine separate requests.
export const CHECK_REVEAL_STAGGER_MS = 350;
