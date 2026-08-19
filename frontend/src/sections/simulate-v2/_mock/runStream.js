/**
 * Live-run event stream.
 *
 * Builds a deterministic, seeded timeline for a simulation run so the live view
 * can play back exactly what a real run would look like — per surface. The
 * player (`useRunStream`) walks this timeline on a timer, which is what makes
 * "watch the agent work" possible without a backend.
 *
 * Determinism matters: a stakeholder demo that shuffles its own results on
 * every reload is not a demo. Same seed in, same run out.
 */

/* ── seeded RNG (mulberry32) ─────────────────────────────────────────────── */
export function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a += 0x6d2b79f5;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export const hashSeed = (str) => {
  let h = 2166136261;
  for (let i = 0; i < str.length; i += 1) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
};

/* ── per-surface step vocabularies ───────────────────────────────────────── */

const VOICE_TURNS = [
  { role: "customer", text: "Hi, I'm calling about an order I placed last week." },
  { role: "agent", text: "Of course — I can help with that. Could I take the order number?" },
  { role: "customer", text: "It's A dash one zero two four one." },
  { role: "agent", text: "Thank you. Before I pull that up, can you confirm the email on the account?" },
  { role: "customer", text: "Yeah, it's marcus dot webb at gmail." },
  { role: "agent", text: "Perfect, that matches. Let me check the shipment." },
  { role: "customer", text: "It was supposed to be here Tuesday." },
  { role: "agent", text: "I can see it's with the carrier and out for delivery today before 8pm." },
  { role: "customer", text: "Okay. And if it doesn't turn up?" },
  { role: "agent", text: "If it hasn't arrived by tomorrow, call back and we'll open a lost-parcel claim." },
  { role: "customer", text: "Alright, thanks for your help." },
  { role: "agent", text: "You're very welcome. Anything else I can do today?" },
];

const CHAT_TURNS = [
  { role: "customer", text: "There's a charge on my card I don't recognise." },
  { role: "agent", text: "I can look into that. For security, can you confirm your date of birth?" },
  { role: "customer", text: "14 March 1987." },
  { role: "agent", text: "Thank you. I'll also need the last four digits of the card." },
  { role: "customer", text: "4471." },
  { role: "agent", text: "Verified. I can see a £64.99 charge from ACME Retail on 12 August." },
  { role: "customer", text: "I've never shopped there." },
  { role: "agent", text: "I'll freeze the card now and raise a dispute. Resolution takes up to 10 business days." },
  { role: "customer", text: "Please do." },
  { role: "agent", text: "Card frozen and dispute DSP-99214 raised. A replacement arrives in 3–5 days." },
];

const BROWSER_STEPS = [
  { action: "navigate", target: "app.acme-admin.com/login", thought: "Start at the console login." },
  { action: "type", target: "#email", value: "ops@acme.com", thought: "Fill the operator account." },
  { action: "type", target: "#password", value: "••••••••", thought: "Enter the password." },
  { action: "click", target: "button[type=submit]", thought: "Sign in." },
  { action: "wait", target: "dashboard", thought: "Wait for the dashboard to settle." },
  { action: "click", target: "nav >> Billing", thought: "Billing is where failed payments live." },
  { action: "type", target: "input[name=search]", value: "past due", thought: "Filter to past-due workspaces." },
  { action: "click", target: "tr:has-text('Northwind') >> button", thought: "Open the first affected workspace." },
  { action: "scroll", target: "invoice list", thought: "Find the failed invoice." },
  { action: "click", target: "button:has-text('Retry payment')", thought: "Retry the charge." },
  { action: "wait", target: "confirmation toast", thought: "Confirm the retry succeeded." },
  { action: "click", target: "button:has-text('Export')", thought: "Export the reconciliation list." },
];

const TOOL_STEPS = [
  { tool: "query_metrics", args: { q: "rate(http_errors[5m])", svc: "checkout" }, result: "0.081 (8.1%)", ms: 240 },
  { tool: "read_logs", args: { svc: "checkout", since: "15m" }, result: "412 × NullPointerException", ms: 610 },
  { tool: "query_metrics", args: { q: "deploy_version", svc: "checkout" }, result: "v4.12.0 @ 14:02", ms: 180 },
  { tool: "read_logs", args: { svc: "checkout", grep: "v4.12.0" }, result: "errors begin 14:03", ms: 520 },
  { tool: "rollback_deploy", args: { svc: "checkout", to: "v4.11.3" }, result: "rollback queued", ms: 1400 },
  { tool: "query_metrics", args: { q: "rate(http_errors[5m])", svc: "checkout" }, result: "0.004 (0.4%)", ms: 260 },
  { tool: "page_oncall", args: { team: "payments", sev: 3 }, result: "paged @alex", ms: 320 },
];

const TERMINAL_STEPS = [
  { cmd: "pytest -q", out: "3 failed, 128 passed in 12.4s", ms: 12400 },
  { cmd: "pytest -q tests/test_invoice.py -x", out: "FAILED test_prorate_refund", ms: 3100 },
  { cmd: "cat src/billing/prorate.py", out: "…def prorate(amount, days): return amount * days / 30", ms: 90 },
  { cmd: "git log --oneline -3 src/billing/prorate.py", out: "a91c3f4 fix rounding\n2b7e881 initial", ms: 140 },
  { cmd: "apply_patch prorate.py", out: "patched 1 file, +4 −2", ms: 200 },
  { cmd: "pytest -q", out: "131 passed in 12.9s", ms: 12900 },
];

const SIM_STEPS = [
  { action: "reset", obs: "episode start · scene loaded", reward: 0, note: "Fresh episode from the seeded scene." },
  { action: "observe", obs: "gripper (0.12, 0.04, 0.31) · target (0.38, 0.02, 0.05)", reward: 0, note: "Reading joint and object state." },
  { action: "act", obs: "move_to(pre_grasp)", reward: 0.04, note: "Approaching the object." },
  { action: "act", obs: "close_gripper()", reward: 0.18, note: "Contact detected on both pads." },
  { action: "observe", obs: "object lifted 0.06m", reward: 0.31, note: "Grasp is holding." },
  { action: "act", obs: "move_to(goal_pose)", reward: 0.52, note: "Transporting to the goal." },
  { action: "act", obs: "open_gripper()", reward: 0.74, note: "Releasing at the target." },
  { action: "observe", obs: "success predicate = true", reward: 0.91, note: "Task predicate satisfied." },
];

const EMAIL_STEPS = [
  { kind: "read", subject: "Claim #CLM-4471 — water damage", note: "Thread has 4 messages, 2 attachments." },
  { kind: "parse", subject: "surveyor_report.pdf", note: "Extracted: incident date, policy no., estimate £8,400." },
  { kind: "check", subject: "Policy P-88213", note: "Active. Excess £250. Adjuster review needed above £5,000." },
  { kind: "compose", subject: "Re: Claim #CLM-4471 — documents needed", note: "Listed 3 missing documents in one message." },
  { kind: "send", subject: "Re: Claim #CLM-4471", note: "Sent to claimant, adjuster CC'd." },
];

/* ── run construction ────────────────────────────────────────────────────── */

const STAGE_STEPS = {
  voice: VOICE_TURNS,
  chat: CHAT_TURNS,
  browser: BROWSER_STEPS,
  tools: TOOL_STEPS,
  terminal: TERMINAL_STEPS,
  email: EMAIL_STEPS,
  sim: SIM_STEPS,
  multi: CHAT_TURNS,
};

/**
 * Build a full run: a list of tasks, each with its own step timeline and eval
 * verdicts. Tasks are spread across a few parallel "workers" so the live view
 * shows real concurrency rather than a single queue.
 */
export function buildRun({
  seed = "default",
  scenarios = [],
  stage = "voice",
  evals = [],
  concurrency = 4,
  failRate = 0.22,
}) {
  const r = rng(hashSeed(seed));
  const vocab = STAGE_STEPS[stage] || VOICE_TURNS;

  const tasks = scenarios.map((sc, i) => {
    const stepCount = 5 + Math.floor(r() * Math.min(vocab.length - 4, 8));
    const steps = Array.from({ length: stepCount }, (_, s) => ({
      id: `${sc.id}-s${s}`,
      index: s,
      ...vocab[s % vocab.length],
      // Per-step dwell time, in ms of simulated wall clock.
      duration: 600 + Math.floor(r() * 1400),
    }));

    // Critical scenarios fail more often — that is the point of marking them.
    const failChance = sc.critical ? failRate * 1.9 : failRate;
    const failed = r() < failChance;
    const failStep = failed
      ? Math.max(2, Math.floor(steps.length * (0.45 + r() * 0.45)))
      : null;

    const evalResults = evals.map((ev) => {
      // The failing task should fail the eval that explains *why* it failed.
      const isCulprit =
        failed &&
        (ev.id === "policy_adherence" ||
          ev.id === "task_success" ||
          ev.id === "pii_leakage");
      const base = isCulprit ? 0.18 + r() * 0.34 : 0.72 + r() * 0.28;
      const score = Math.round(base * 100) / 100;
      return {
        id: ev.id,
        name: ev.name,
        color: ev.color,
        score,
        passed: score >= (ev.threshold ?? 0.8),
        reason: isCulprit
          ? culpritReason(ev.id, sc)
          : "Met the configured threshold with no violations detected.",
      };
    });

    return {
      id: sc.id,
      title: sc.title,
      task: sc.task,
      persona: sc.persona,
      expected: sc.expected,
      critical: sc.critical,
      worker: i % concurrency,
      steps,
      failStep,
      status: "queued",
      verdict: failed ? "failed" : "passed",
      evalResults,
      durationMs: steps.reduce((a, s) => a + s.duration, 0),
      cost: Math.round((0.02 + r() * 0.14) * 1000) / 1000,
      tokens: 1200 + Math.floor(r() * 6400),
    };
  });

  return { tasks, stage, concurrency };
}

function culpritReason(evalId, sc) {
  switch (evalId) {
    case "policy_adherence":
      return `Agent bypassed a rule this scenario targets. Expected: ${sc.expected}`;
    case "pii_leakage":
      return "Account details were read out before the second identity factor was confirmed.";
    case "task_success":
      return `Task was not completed. Expected: ${sc.expected}`;
    default:
      return "Scored below the configured threshold.";
  }
}

/** Provisioning steps shown while the environment boots, per surface. */
export const BOOT_STEPS = {
  voice: ["Allocating media servers", "Reserving test numbers", "Warming TTS voices", "Dialling the agent"],
  chat: ["Spinning up session pool", "Opening webhook channel", "Priming conversation state", "Sending handshake"],
  browser: ["Provisioning sandbox VMs", "Restoring seeded database", "Launching Chromium", "Attaching screen recorder"],
  tools: ["Publishing tool manifest", "Restoring seeded database", "Opening MCP channel", "Awaiting agent connect"],
  terminal: ["Building container image", "Cloning seed repositories", "Installing dependencies", "Attaching to pty"],
  email: ["Provisioning mailboxes", "Seeding claim threads", "Starting SMTP relay", "Awaiting first poll"],
  sim: ["Starting physics engine", "Loading scene assets", "Warming the renderer", "Sending first observation"],
  multi: ["Allocating media servers", "Provisioning mailboxes", "Linking channel state", "Awaiting first contact"],
};
