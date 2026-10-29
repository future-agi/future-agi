/*
  Sandbox certification (roadmap release.md, Week 2): concurrency, cleanup
  after cancel and timeout, isolation between trials and from graders, and
  benchmark parity — each with what was run and what it showed. A lost
  capability (the sandbox) is kept apart from an agent failure.
*/
export const SANDBOX_CHECKS = [
  {
    id: "concurrency",
    label: "Concurrent trials",
    status: "supported",
    ran: "16 trials of the returns world at once, 3 times",
    showed: "No fixture leaked between trials — every trial started from its own seed and ended with only its own writes.",
  },
  {
    id: "cleanup",
    label: "Cleanup after cancel and timeout",
    status: "supported",
    ran: "Cancelled 8 trials mid-tool-call; let 8 hit the per-trial timeout",
    showed: "Every sandbox was torn down within 30 s; no orphaned process or open connection; partial evidence kept.",
  },
  {
    id: "isolation",
    label: "Grader isolation",
    status: "supported",
    ran: "Probed the agent's tools for grader files, expected outcomes and other trials' data",
    showed: "Nothing hidden was readable from inside the sandbox.",
  },
  {
    id: "lost",
    label: "Lost capability vs agent failure",
    status: "supported",
    ran: "Killed the tool service in 4 trials",
    showed: "All 4 ended as infrastructure errors — not counted against the agent.",
  },
  {
    id: "children",
    label: "Parallel child agents",
    status: "untested",
    ran: "—",
    showed: "Concurrent independent trials don't certify parallel workers inside one agent — sequential only.",
  },
];

/* Our run of a public benchmark against its published reference — parity, within tolerance. */
export const BENCHMARK_PARITY = [
  { id: "tau-retail", name: "τ-bench retail", ours: 0.61, published: 0.62, tolerance: 0.02, model: "gpt-4o (reference agent)" },
  { id: "tau-airline", name: "τ-bench airline", ours: 0.44, published: 0.46, tolerance: 0.03, model: "gpt-4o (reference agent)" },
  { id: "appworld", name: "AppWorld test-normal", ours: null, published: 0.49, tolerance: 0.03, model: "—" },
];

/** "Within ±0.02" | "Off by 0.05" | "Not run yet". */
export const parityOf = (b) => {
  if (b.ours == null) return { status: "untested", line: "Not run yet" };
  const d = Math.abs(b.ours - b.published);
  return d <= b.tolerance
    ? { status: "supported", line: `Within ±${b.tolerance.toFixed(2)}` }
    : { status: "blocked", line: `Off by ${d.toFixed(2)}` };
};
