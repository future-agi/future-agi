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

/*
  Agent lines carry alternates; the caller's do not.

  A new version of an agent says the same things differently — that is most of
  what changing a prompt does — and a comparison where every turn is
  word-for-word identical can only ever report "2 turns added", which is the
  least interesting thing that happened. The counterpart keeps its script so the
  difference stays attributable: when the caller says the same sentence and the
  agent answers it differently, the agent is the variable.
*/
const VOICE_TURNS = [
  { role: "customer", text: "Hi, I'm calling about an order I placed last week." },
  {
    role: "agent",
    text: "Of course — I can help with that. Could I take the order number?",
    alts: [
      "Happy to help. What's the order number?",
      "Sure thing. Can you read me the order number when you have it?",
    ],
  },
  { role: "customer", text: "It's A dash one zero two four one." },
  {
    role: "agent",
    text: "Thank you. Before I pull that up, can you confirm the email on the account?",
    alts: [
      "Got it. For security, what's the email on the account?",
      "Thanks. One quick check first — the email we have on file?",
    ],
  },
  { role: "customer", text: "Yeah, it's marcus dot webb at gmail." },
  {
    role: "agent",
    text: "Perfect, that matches. Let me check the shipment.",
    alts: [
      "That matches what I have. Checking the shipment now.",
      "Verified. Pulling up the delivery status.",
    ],
  },
  { role: "customer", text: "It was supposed to be here Tuesday." },
  {
    role: "agent",
    text: "I can see it's with the carrier and out for delivery today before 8pm.",
    alts: [
      "It's with the carrier and due today before 8pm.",
      "The carrier has it — delivery is scheduled for this evening.",
    ],
  },
  { role: "customer", text: "Okay. And if it doesn't turn up?" },
  {
    role: "agent",
    text: "If it hasn't arrived by tomorrow, call back and we'll open a lost-parcel claim.",
    alts: [
      "If nothing arrives by tomorrow, we'll raise a lost-parcel claim for you.",
      "Not arrived by tomorrow? Call us and we'll start a claim straight away.",
    ],
  },
  { role: "customer", text: "Alright, thanks for your help." },
  {
    role: "agent",
    text: "You're very welcome. Anything else I can do today?",
    alts: [
      "You're welcome. Anything else while I have you?",
      "Glad to help. Was there anything else today?",
    ],
  },
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

/*
  Three browsers, not one.

  A browser environment is whatever app it seeds, and this one seeds a todo list
  and 2048 boards — so an agent driving it through a billing console was a
  script from a different product. The scenario decides which app is on screen,
  and the steps are the ones that app can actually take.
*/
const BROWSER_TODO = [
  { action: "navigate", target: "app.taskly.dev/lists/inbox", thought: "Open the shared inbox list." },
  { action: "click", target: "tab:has-text('Active')", thought: "Hide what is already done before counting." },
  { action: "type", target: "#new-task", value: "Chase Northwind invoice", thought: "Add the task that was asked for." },
  { action: "click", target: "button:has-text('Add')", thought: "Commit it to the list." },
  { action: "click", target: "li:has-text('Renew SSL certificate') >> input[type=checkbox]", thought: "Tick the one that is finished." },
  { action: "scroll", target: "task list", thought: "Check nothing below is already complete." },
  { action: "click", target: "button:has-text('Clear completed')", thought: "Tidy the list as instructed." },
  { action: "wait", target: "counter settles", thought: "Confirm the remaining count changed." },
];

const BROWSER_GAME = [
  { action: "navigate", target: "play.2048.io/seeded/4x4", thought: "Load the seeded board." },
  { action: "key", target: "ArrowLeft", thought: "Collapse the row and keep the big tile in a corner." },
  { action: "key", target: "ArrowUp", thought: "Stack the pairs without breaking the corner." },
  { action: "key", target: "ArrowLeft", thought: "Merge the two 32s." },
  { action: "scroll", target: "score panel", thought: "Read the score before deciding again." },
  { action: "key", target: "ArrowUp", thought: "Keep the largest tile pinned." },
  { action: "key", target: "ArrowLeft", thought: "One more merge along the top row." },
  { action: "wait", target: "board settles", thought: "Let the spawned tile land before the next move." },
];

const BROWSER_ADMIN = [
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

/** Which app this scenario is about. Titles and tasks name it. */
export const browserAppOf = (row) => {
  const t = `${row?.title || ""} ${row?.task || ""}`.toLowerCase();
  if (/2048|game|board|tile/.test(t)) return "game";
  if (/todo|task list|checklist/.test(t)) return "todo";
  return "admin";
};

const BROWSER_VOCAB = { admin: BROWSER_ADMIN, todo: BROWSER_TODO, game: BROWSER_GAME };

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
  browser: BROWSER_ADMIN,
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
/**
 * What the agent did to the world, alongside what it said.
 *
 * The whole argument for an environment over a transcript test is that the
 * world knows what was touched. An agent can say "I've issued your refund",
 * never call issue_refund, and score perfectly on every grader that reads
 * words — which is the failure people buy this to catch.
 *
 * So a task carries a call log: which tools were invoked, with what, what came
 * back, and — the important half — which tools the scenario needed and the
 * agent never called at all.
 */
function buildCallLog(scenario, tools, r, failed) {
  const named = (scenario.task || "").toLowerCase();
  /* Tools the scenario is actually about: the ones it names, or the first
     couple as a fallback so every task has something to be judged on. */
  const required = tools.filter((t) => named.includes(t.name)).slice(0, 3);
  /* Fallback picks from the scenario's own hash rather than the head of the
     list. Taking the first two made every failing task in a run skip the same
     tool, which reads as one bug repeated seven times rather than seven
     scenarios — and it is only visible once two runs sit side by side. */
  const pick = hashSeed(scenario.id || "s");
  const fallback = tools.length
    ? [tools[pick % tools.length], tools[(pick + 1) % tools.length]].filter(
      (t, i, arr) => arr.indexOf(t) === i,
    )
    : [];
  const expected = required.length ? required : fallback;

  /* A failing run skips one of the tools it needed — that skip is the finding,
     and it is what the claims-vs-actions check reads. */
  const skipped = failed && expected.length ? expected[expected.length - 1] : null;

  const calls = expected
    .filter((t) => t !== skipped)
    .map((t, i) => ({
      id: `${scenario.id}-call-${i}`,
      name: t.name,
      args: { id: `A-${1000 + Math.floor(r() * 8999)}` },
      status: "ok",
      rows: 1 + Math.floor(r() * 3),
      ms: 40 + Math.floor(r() * 260),
      wrote: /refund|issue|create|update|cancel|book/.test(t.name),
    }));

  /* Extra reads happen — they are not failures, but they are worth seeing. */
  const extras = tools
    .filter((t) => !expected.includes(t))
    .slice(0, failed ? 0 : 1)
    .map((t, i) => ({
      id: `${scenario.id}-extra-${i}`,
      name: t.name,
      args: {},
      status: "ok",
      rows: 1,
      ms: 30 + Math.floor(r() * 120),
      wrote: false,
    }));

  return {
    calls: [...calls, ...extras],
    /* The tools this scenario is about, named whether or not they were called.
       Derived from the scenario rather than from the run, so every version of
       the agent is judged against the same list — which is what makes a
       checklist comparable across columns at all. */
    expected: expected.map((t) => t.name),
    missing: skipped ? [skipped.name] : [],
    /* The claim the transcript makes that the call log does not support.
       A skipped write was claimed as done; a skipped read was claimed as
       looked up — "the shipment status was done" is not a sentence. */
    unsupportedClaim: skipped
      ? (/refund|issue|create|update|cancel|book|escalate/.test(skipped.name)
        ? `The agent told the caller the ${skipped.name.replace(/_/g, " ")} was done, but never called ${skipped.name}.`
        : `The agent told the caller it had checked the ${skipped.name.replace(/_/g, " ")}, but never called ${skipped.name}.`)
      : null,
  };
}

/**
 * A scenario, run more than once.
 *
 * One sample per scenario and a boolean verdict is the single most misleading
 * thing an eval builder can do: the other side of the conversation is a
 * sampled model, so two runs of the *same* agent disagree, and a screen that
 * reports 43% against 86% invites someone to ship on the strength of two coin
 * flips. Every scenario is therefore run `repeats` times, and its result is a
 * proportion — passed, failed, or flaky when the samples cannot agree with
 * each other, which is a finding about the scenario rather than the agent.
 */
export function buildRun({
  seed = "default",
  scenarios = [],
  stage = "voice",
  evals = [],
  concurrency = 4,
  failRate = 0.22,
  tools = [],
  repeats = 3,
  phrasing = 0,
}) {
  const r = rng(hashSeed(seed));
  const vocabFor = (sc) => (stage === "browser"
    ? BROWSER_VOCAB[browserAppOf(sc)]
    : STAGE_STEPS[stage] || VOICE_TURNS);

  const tasks = scenarios.map((sc, i) => {
    const vocab = vocabFor(sc);
    const stepCount = 5 + Math.floor(r() * Math.min(vocab.length - 4, 8));
    /* Phrasing belongs to the agent version, not to the run: one prompt means
       one way of opening the call, every time it runs. Two runs of the same
       version therefore read identically — which is what makes a wording
       difference between columns attributable to the version change. */
    const voiceIdx = phrasing;
    const steps = Array.from({ length: stepCount }, (_, s) => {
      const base = vocab[s % vocab.length];
      const phrasings = base.alts ? [base.text, ...base.alts] : null;
      return {
        id: `${sc.id}-s${s}`,
        index: s,
        ...base,
        ...(phrasings ? { text: phrasings[voiceIdx % phrasings.length] } : {}),
        // Per-step dwell time, in ms of simulated wall clock.
        duration: 600 + Math.floor(r() * 1400),
      };
    });

    /*
      Faults that are not the agent's.

      Rare on purpose — a builder that drops one call in three is not a builder
      — but present, because a product that has never seen an infrastructure
      failure will report the next one as an agent regression. Drawn before the
      verdict and independently of it: whether the environment came up has
      nothing to do with how good the agent is.
    */
    const fault = {};
    const faultDraw = r();
    if (faultDraw < 0.035) {
      fault.environment = `${tools[0]?.name || "The seeded world"} never passed its readiness probe — the scenario could not be staged.`;
    } else if (faultDraw < 0.07) {
      fault.transport = "The session dropped before the agent answered; no terminal state was reached.";
    } else if (faultDraw < 0.115) {
      fault.simulator = "The simulated caller contradicted its own scenario facts and the run was abandoned.";
    } else if (faultDraw < 0.13) {
      fault.grading = "Evidence was captured but the grader returned no verdict.";
    }
    const unmeasured = !!(fault.environment || fault.transport || fault.simulator || fault.grading);

    /* Sample-to-sample drift in the *caller* rather than the agent. A scenario
       whose caller wanders is flaky because of the scenario. */
    if (!unmeasured && r() < 0.08) fault.simulatorDrift = true;

    // Critical scenarios fail more often — that is the point of marking them.
    const failChance = sc.critical ? failRate * 1.9 : failRate;
    const draw = r();
    const failed = draw < failChance;
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
      const passedIt = score >= (ev.threshold ?? 0.8);
      return {
        id: ev.id,
        name: ev.name,
        color: ev.color,
        score,
        passed: passedIt,
        reason: isCulprit
          ? culpritReason(ev.id, sc)
          : passedIt
            ? "Met the configured threshold with no violations detected."
            : `Scored ${Math.round(score * 100)} against a threshold of ${Math.round((ev.threshold ?? 0.8) * 100)}.`,
      };
    });

    const callLog = buildCallLog(sc, tools, r, failed);

    /*
      The remaining samples. The first one is the episode kept in full — its
      transcript, its call log, its graders — because a drawer showing three
      near-identical conversations helps nobody. The rest contribute their
      verdict, which is what the proportion is made of.
    */
    const samples = [failed ? "failed" : "passed"];
    /*
      Only scenarios near their own threshold disagree with themselves. Drawing
      each sample independently would make roughly half of every suite flaky,
      which is both wrong about real agents — most scenarios land the same way
      every time — and useless: a flag that fires on half the rows is not a
      finding. So how close this scenario landed to its threshold decides how
      likely a later sample is to come out the other way.
    */
    const margin = Math.abs(draw - failChance);
    const flipChance = Math.max(0, 0.55 - margin * 4);
    for (let k = 1; k < Math.max(1, repeats); k += 1) {
      const flipped = r() < flipChance ? !failed : failed;
      samples.push(flipped ? "failed" : "passed");
    }
    const passes = samples.filter((v) => v === "passed").length;
    /*
      A scenario nothing could be measured on has no verdict — not a failed
      one. Calling it "failed" is the single most common way an eval product
      reports its own outage as the agent's fault.
    */
    const verdict = unmeasured
      ? "unmeasured"
      : passes === samples.length
        ? "passed"
        : passes === 0 ? "failed" : "flaky";

    return {
      id: sc.id,
      callLog,
      title: sc.title,
      task: sc.task,
      persona: sc.persona,
      expected: sc.expected,
      critical: sc.critical,
      worker: i % concurrency,
      steps,
      failStep,
      status: "queued",
      verdict,
      fault,
      measured: !unmeasured,
      samples: unmeasured ? [] : samples,
      repeats: unmeasured ? 0 : samples.length,
      passes: unmeasured ? null : passes,
      /* The share of samples that passed — the number every rate on every
         screen above this one is an average of. Null when there is nothing to
         average, so it can never be silently read as zero. */
      passShare: unmeasured ? null : passes / samples.length,
      evalResults,
      durationMs: steps.reduce((a, s) => a + s.duration, 0),
      cost: Math.round((0.02 + r() * 0.14) * 1000) / 1000,
      tokens: 1200 + Math.floor(r() * 6400),
    };
  });

  return { tasks, stage, concurrency, repeats };
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
