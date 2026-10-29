// What the fix page says about a fix, derived from one contract read
// (GET /simulate/api/agent-fix-runs/{id}/). Only simple differences are
// computed here; every number comes from the platform or the engine.

export const STATE_LABEL = {
  queued: "Queued",
  running: "Running",
  stopping: "Stopping",
  ready: "Ready for review",
  no_improvement: "No safe improvement",
  not_reproduced: "Could not reproduce",
  out_of_budget: "Stopped at budget",
  stopped: "Stopped",
  failed: "Failed",
};

export const OUTCOME_LABEL = {
  winner: "Winner",
  finalist: "Passed, not picked",
  rejected: "Rejected",
  pending: "Running",
};

const SURFACE_LABEL = {
  prompt: "Instructions",
  tools: "Tools",
  settings: "Settings",
  code: "Code",
};

const MUST_PASS_RULE =
  "A case that passed every time before must not drop beyond normal noise. A case that fails every run is run again to confirm before it counts.";

const STEPS = [
  { id: "reproduce", label: "Reproduce" },
  { id: "search", label: "Search" },
  { id: "verify", label: "Verify" },
  { id: "done", label: "Ready for review" },
];

export const pct = (x) => Math.round(x * 100);
export const pts = (rate) => Math.round((rate[1] - rate[0]) * 100);

export const formatCredits = (n) =>
  `${Number(n ?? 0).toLocaleString("en-US")} credits`;

export function formatMinutes(total) {
  const h = Math.floor(total / 60);
  const m = total % 60;
  if (!h) return `${m} min`;
  return m ? `${h} h ${m} min` : `${h} h`;
}

export const fixName = (fix) =>
  fix.number == null ? "Improvement" : `Improvement ${fix.number}`;

export function identityLine(fix) {
  const agent = [`agent v${fix.agent.version_number}`, fix.agent.ref]
    .filter(Boolean)
    .join(" · ");
  const version = fix.environment?.version;
  const left = version == null ? agent : `${agent} × environment v${version}`;
  return [left, fix.environment?.name].filter(Boolean).join(" · ");
}

export const winnerOf = (fix) =>
  (fix.candidates || []).find((c) => c.outcome === "winner");

export const isShipped = (fix, candidate) =>
  !!candidate &&
  candidate.agent_version_id === fix.environment?.default_agent_version_id;

const brokeText = (n) => (n ? String(n) : "none");

export function headline(fix) {
  const winner = winnerOf(fix);
  if (fix.state !== "ready" || !winner || !fix.plan) {
    return fix.report?.summary || fix.failure?.message || null;
  }
  const { verdict } = winner;
  const broke = brokeText(verdict.must_pass_regressions.length);
  if (verdict.held_back_pass_rate) {
    const n = fix.plan.held_back;
    const k = Math.round(verdict.held_back_pass_rate[1] * n);
    return `Fixed ${k} of ${n} failing scenarios on the held-back set, broke ${broke}.`;
  }
  const n = fix.plan.target;
  const k = Math.round(verdict.target_pass_rate[1] * n);
  return `Fixed ${k} of ${n} target scenarios, broke ${broke}.`;
}

const rateCard = (id, label, rate, note) => ({
  id,
  label,
  from: pct(rate[0]),
  to: pct(rate[1]),
  delta: pts(rate),
  note,
});

export function verdictCards(fix) {
  const winner = winnerOf(fix);
  if (!winner) return [];
  const { target_pass_rate: target, held_back_pass_rate: held } =
    winner.verdict;
  const targetCard = rateCard(
    "target",
    "Target",
    target,
    "on the scenarios it was tuned on",
  );
  if (!held) return [targetCard];
  return [
    rateCard("held", "Held back", held, "re-run after the pick"),
    targetCard,
    {
      id: "gap",
      label: "Overfit gap",
      value: pct(target[1]) - pct(held[1]),
      note: "between target and held back",
    },
  ];
}

export function checksLine(tiers = []) {
  const counted = tiers.filter((t) => t.status !== "skipped");
  const passed = counted.filter((t) => t.status === "passed").length;
  return `Checks · ${passed} of ${counted.length} passed`;
}

const bestTargetPct = (candidates) =>
  Math.max(...candidates.map((c) => pct(c.verdict.target_pass_rate[1])));

function stepLines(id, fix) {
  const { plan, diagnosis } = fix;
  const candidates = fix.candidates || [];
  if (id === "reproduce") {
    return [
      fix.source_run_number != null && fix.agent?.version_number != null
        ? `Starts from Run ${fix.source_run_number} on v${fix.agent.version_number}`
        : null,
      plan
        ? `The current version varied by ${plan.baseline_spread_pts} pts between repeats.`
        : null,
    ].filter(Boolean);
  }
  if (id === "search") {
    const count = plan
      ? candidates.length
        ? `${candidates.length} of up to ${plan.max_candidates} trials · best so far ${bestTargetPct(candidates)}% on ${plan.target} target scenarios`
        : `0 of up to ${plan.max_candidates} trials`
      : null;
    const cause = diagnosis?.causes?.[0]
      ? `Starts from its diagnosis: ${diagnosis.causes[0]}`
      : null;
    return [count, cause].filter(Boolean);
  }
  if (id === "verify") {
    return plan
      ? [
          `${plan.held_back} held-back scenarios it never saw and ${plan.must_pass} must-pass scenarios`,
        ]
      : [];
  }
  return ["You choose the trial to keep and ship"];
}

export function stepRows(fix) {
  const current = STEPS.findIndex((s) => s.id === fix.step);
  const finished = fix.step === "done";
  return STEPS.map((s, i) => {
    let state = "todo";
    if (finished || i < current) state = "done";
    else if (i === current) state = "now";
    return { ...s, state, lines: stepLines(s.id, fix) };
  });
}

export function heldToRows(fix) {
  const { request } = fix;
  const rows = [
    {
      label: "Allowed changes",
      value: request.surfaces.map((s) => SURFACE_LABEL[s] || s).join(", "),
    },
    { label: "Time cap", value: formatMinutes(request.max_minutes) },
    { label: "Budget", value: formatCredits(request.budget_credits) },
    {
      label: "Protected cases",
      value: `${request.protected_scenario_keys.length} protected`,
    },
  ];
  if (request.locked_paths?.length) {
    rows.push({
      label: "Locked files",
      value: request.locked_paths.join(", "),
    });
  }
  rows.push({ label: "Must-pass", value: MUST_PASS_RULE });
  rows.push({ label: "Model", value: "unchanged" });
  return rows;
}

// The list's Result cell: the held-back result first, as the design shows it.
export function listResult(fix) {
  const winner = winnerOf(fix);
  if (fix.state !== "ready" || !winner || !fix.plan) return null;
  const { verdict } = winner;
  const broke = verdict.must_pass_regressions.length;
  if (verdict.held_back_pass_rate) {
    const n = fix.plan.held_back;
    const k = Math.round(verdict.held_back_pass_rate[1] * n);
    return {
      text: `Fixed ${k} of ${n} held back`,
      note: broke ? `broke ${broke} held back` : "broke none held back",
      broke,
    };
  }
  const n = fix.plan.target;
  const k = Math.round(verdict.target_pass_rate[1] * n);
  return {
    text: `Fixed ${k} of ${n} targets`,
    note: broke ? `broke ${broke}` : "broke none",
    broke,
  };
}
