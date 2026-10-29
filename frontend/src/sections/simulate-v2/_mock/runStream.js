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
import { admissionOf } from "./coverage";
import { inferTools, sandboxScenario } from "./toolInference";
import { blockerReason } from "./releaseBlocker";
import { scriptFor } from "./runScripts";
import { callMinutesOf } from "./pricing";

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
  {
    role: "customer",
    text: "Hi, I'm calling about an order I placed last week.",
  },
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
  {
    role: "agent",
    text: "I can look into that. For security, can you confirm your date of birth?",
  },
  { role: "customer", text: "14 March 1987." },
  {
    role: "agent",
    text: "Thank you. I'll also need the last four digits of the card.",
  },
  { role: "customer", text: "4471." },
  {
    role: "agent",
    text: "Verified. I can see a £64.99 charge from ACME Retail on 12 August.",
  },
  { role: "customer", text: "I've never shopped there." },
  {
    role: "agent",
    text: "I'll freeze the card now and raise a dispute. Resolution takes up to 10 business days.",
  },
  { role: "customer", text: "Please do." },
  {
    role: "agent",
    text: "Card frozen and dispute DSP-99214 raised. A replacement arrives in 3–5 days.",
  },
];

/*
  Three browsers, not one.

  A browser environment is whatever app it seeds, and this one seeds a todo list
  and 2048 boards — so an agent driving it through a billing console was a
  script from a different product. The scenario decides which app is on screen,
  and the steps are the ones that app can actually take.
*/
const BROWSER_TODO = [
  {
    action: "navigate",
    target: "app.taskly.dev/lists/inbox",
    thought: "Open the shared inbox list.",
  },
  {
    action: "click",
    target: "tab:has-text('Active')",
    thought: "Hide what is already done before counting.",
  },
  {
    action: "type",
    target: "#new-task",
    value: "Chase Northwind invoice",
    thought: "Add the task that was asked for.",
  },
  {
    action: "click",
    target: "button:has-text('Add')",
    thought: "Commit it to the list.",
  },
  {
    action: "click",
    target: "li:has-text('Renew SSL certificate') >> input[type=checkbox]",
    thought: "Tick the one that is finished.",
  },
  {
    action: "scroll",
    target: "task list",
    thought: "Check nothing below is already complete.",
  },
  {
    action: "click",
    target: "button:has-text('Clear completed')",
    thought: "Tidy the list as instructed.",
  },
  {
    action: "wait",
    target: "counter settles",
    thought: "Confirm the remaining count changed.",
  },
];

const BROWSER_GAME = [
  {
    action: "navigate",
    target: "play.2048.io/seeded/4x4",
    thought: "Load the seeded board.",
  },
  {
    action: "key",
    target: "ArrowLeft",
    thought: "Collapse the row and keep the big tile in a corner.",
  },
  {
    action: "key",
    target: "ArrowUp",
    thought: "Stack the pairs without breaking the corner.",
  },
  { action: "key", target: "ArrowLeft", thought: "Merge the two 32s." },
  {
    action: "scroll",
    target: "score panel",
    thought: "Read the score before deciding again.",
  },
  {
    action: "key",
    target: "ArrowUp",
    thought: "Keep the largest tile pinned.",
  },
  {
    action: "key",
    target: "ArrowLeft",
    thought: "One more merge along the top row.",
  },
  {
    action: "wait",
    target: "board settles",
    thought: "Let the spawned tile land before the next move.",
  },
];

const BROWSER_ADMIN = [
  {
    action: "navigate",
    target: "app.acme-admin.com/login",
    thought: "Start at the console login.",
  },
  {
    action: "type",
    target: "#email",
    value: "ops@acme.com",
    thought: "Fill the operator account.",
  },
  {
    action: "type",
    target: "#password",
    value: "••••••••",
    thought: "Enter the password.",
  },
  { action: "click", target: "button[type=submit]", thought: "Sign in." },
  {
    action: "wait",
    target: "dashboard",
    thought: "Wait for the dashboard to settle.",
  },
  {
    action: "click",
    target: "nav >> Billing",
    thought: "Billing is where failed payments live.",
  },
  {
    action: "type",
    target: "input[name=search]",
    value: "past due",
    thought: "Filter to past-due workspaces.",
  },
  {
    action: "click",
    target: "tr:has-text('Northwind') >> button",
    thought: "Open the first affected workspace.",
  },
  {
    action: "scroll",
    target: "invoice list",
    thought: "Find the failed invoice.",
  },
  {
    action: "click",
    target: "button:has-text('Retry payment')",
    thought: "Retry the charge.",
  },
  {
    action: "wait",
    target: "confirmation toast",
    thought: "Confirm the retry succeeded.",
  },
  {
    action: "click",
    target: "button:has-text('Export')",
    thought: "Export the reconciliation list.",
  },
];

/** Which app this scenario is about. Titles and tasks name it. */
export const browserAppOf = (row) => {
  const t = `${row?.title || ""} ${row?.task || ""}`.toLowerCase();
  if (/2048|game|board|tile/.test(t)) return "game";
  if (/todo|task list|checklist/.test(t)) return "todo";
  return "admin";
};

const BROWSER_VOCAB = {
  admin: BROWSER_ADMIN,
  todo: BROWSER_TODO,
  game: BROWSER_GAME,
};

const TOOL_STEPS = [
  {
    tool: "query_metrics",
    args: { q: "rate(http_errors[5m])", svc: "checkout" },
    result: "0.081 (8.1%)",
    ms: 240,
  },
  {
    tool: "read_logs",
    args: { svc: "checkout", since: "15m" },
    result: "412 × NullPointerException",
    ms: 610,
  },
  {
    tool: "query_metrics",
    args: { q: "deploy_version", svc: "checkout" },
    result: "v4.12.0 @ 14:02",
    ms: 180,
  },
  {
    tool: "read_logs",
    args: { svc: "checkout", grep: "v4.12.0" },
    result: "errors begin 14:03",
    ms: 520,
  },
  {
    tool: "rollback_deploy",
    args: { svc: "checkout", to: "v4.11.3" },
    result: "rollback queued",
    ms: 1400,
  },
  {
    tool: "query_metrics",
    args: { q: "rate(http_errors[5m])", svc: "checkout" },
    result: "0.004 (0.4%)",
    ms: 260,
  },
  {
    tool: "page_oncall",
    args: { team: "payments", sev: 3 },
    result: "paged @alex",
    ms: 320,
  },
];

const TERMINAL_STEPS = [
  { cmd: "pytest -q", out: "3 failed, 128 passed in 12.4s", ms: 12400 },
  {
    cmd: "pytest -q tests/test_invoice.py -x",
    out: "FAILED test_prorate_refund",
    ms: 3100,
  },
  {
    cmd: "cat src/billing/prorate.py",
    out: "…def prorate(amount, days): return amount * days / 30",
    ms: 90,
  },
  {
    cmd: "git log --oneline -3 src/billing/prorate.py",
    out: "a91c3f4 fix rounding\n2b7e881 initial",
    ms: 140,
  },
  { cmd: "apply_patch prorate.py", out: "patched 1 file, +4 −2", ms: 200 },
  { cmd: "pytest -q", out: "131 passed in 12.9s", ms: 12900 },
];

const SIM_STEPS = [
  {
    action: "reset",
    obs: "episode start · scene loaded",
    reward: 0,
    note: "Fresh episode from the seeded scene.",
  },
  {
    action: "observe",
    obs: "gripper (0.12, 0.04, 0.31) · target (0.38, 0.02, 0.05)",
    reward: 0,
    note: "Reading joint and object state.",
  },
  {
    action: "act",
    obs: "move_to(pre_grasp)",
    reward: 0.04,
    note: "Approaching the object.",
  },
  {
    action: "act",
    obs: "close_gripper()",
    reward: 0.18,
    note: "Contact detected on both pads.",
  },
  {
    action: "observe",
    obs: "object lifted 0.06m",
    reward: 0.31,
    note: "Grasp is holding.",
  },
  {
    action: "act",
    obs: "move_to(goal_pose)",
    reward: 0.52,
    note: "Transporting to the goal.",
  },
  {
    action: "act",
    obs: "open_gripper()",
    reward: 0.74,
    note: "Releasing at the target.",
  },
  {
    action: "observe",
    obs: "success predicate = true",
    reward: 0.91,
    note: "Task predicate satisfied.",
  },
];

const EMAIL_STEPS = [
  {
    kind: "read",
    subject: "Claim #CLM-4471 — water damage",
    note: "Thread has 4 messages, 2 attachments.",
  },
  {
    kind: "parse",
    subject: "surveyor_report.pdf",
    note: "Extracted: incident date, policy no., estimate £8,400.",
  },
  {
    kind: "check",
    subject: "Policy P-88213",
    note: "Active. Excess £250. Adjuster review needed above £5,000.",
  },
  {
    kind: "compose",
    subject: "Re: Claim #CLM-4471 — documents needed",
    note: "Listed 3 missing documents in one message.",
  },
  {
    kind: "send",
    subject: "Re: Claim #CLM-4471",
    note: "Sent to claimant, adjuster CC'd.",
  },
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
  /* Tools the scenario is actually about: the ones it records it needs, else
     the ones it names, or the first couple as a fallback so every task has
     something to be judged on. */
  const recorded =
    Array.isArray(scenario.requiredTools) && scenario.requiredTools.length
      ? tools.filter((t) => scenario.requiredTools.includes(t.name))
      : [];
  /* Recorded, else named, else read from its wording — the same inference
     the Scenarios tab shows, so a run exercises the tools the row lists. */
  const inferred =
    recorded.length || sandboxScenario(scenario)
      ? []
      : inferTools(scenario, tools);
  const required = (
    recorded.length
      ? recorded
      : tools.filter((t) => named.includes(t.name) || inferred.includes(t.name))
  ).slice(0, 3);
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
  const skipped =
    failed && expected.length ? expected[expected.length - 1] : null;

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
      ? /refund|issue|create|update|cancel|book|escalate/.test(skipped.name)
        ? `The agent told the caller the ${skipped.name.replace(/_/g, " ")} was done, but never called ${skipped.name}.`
        : `The agent told the caller it had checked the ${skipped.name.replace(/_/g, " ")}, but never called ${skipped.name}.`
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
  /* What the world answers at the run's environment version. */
  tools = [],
  /* What the agent version calls. Defaults to the world's tools — the agent
     the environment was built from. Anything here the world lacks is a call
     that goes unanswered. */
  agentTools = null,
  /* Third parties in the world ({ id, name, goal }). One joins some episodes
     and pulls them off course — they are part of the environment's dynamics. */
  actors = [],
  repeats = 3,
  phrasing = 0,
  /* The environment version the run is on. */
  envVersion = null,
  /* Fault profiles by tool ({ name: { timeoutPct, errorPct, rateLimitPct,
     latencyMs, malformedPct } }) — the World tab's fault dial, as the run
     pinned it. Null or empty: no injected faults, and the run is exactly what
     it was before faults existed. */
  toolFaults = null,
  /* Which generation of admission gates the run was judged by — a stored
     run from before the extra gates replays with the original three. */
  admissionGates = 2,
  /* The updated RSI PRD's run rules, as the run pinned them (0 for runs from
     before them — those replay exactly as they always did):
       outcomes      — a conversation that ends test invalid or error is
                       retried once; a scenario test invalid twice is pulled
                       and flagged for review; cost and response times are
                       recorded only when reported (missing = unverified).
       requiredEvals — an evaluation marked `required` fails the conversation
                       when it fails, and is named as the cause. */
  outcomes = 0,
  requiredEvals = 0,
  /* "Environments, defined" — how the run grades and when it stops, as the
     run pinned them (0 / missing for runs from before; those replay exactly
     as they always did):
       gradingRule 2 — sub-goals decide pass or fail: a conversation passes
                       when every critical sub-goal holds; a required
                       evaluation that fails also fails it; every other
                       evaluation only scores. Supporting sub-goals are
                       reported and never fail it. No evaluations at all is
                       still a verdict. (Older runs: evaluations decided.)
       turnLimits 1  — a conversation stops at its scenario's turn limit
                       (`maxTurns`; a voice scenario's `turns`) or the
                       environment's (`turnLimit`), and a cut-off
                       conversation fails: "Hit the turn limit (12)". */
  gradingRule = 0,
  turnLimits = 0,
  turnLimit = DEFAULT_TURN_LIMIT,
}) {
  const subGoalsDecide = gradingRule >= 2;
  const answers = new Set(tools.map((t) => t.name));
  const gapTools = (agentTools || []).filter((t) => !answers.has(t.name));
  const r = rng(hashSeed(seed));
  /* The scenario's own conversation where one can be written — same length
     as the stock script it replaces, so the run draws the same numbers. */
  const vocabFor = (sc) => {
    if (stage === "browser")
      return { vocab: BROWSER_VOCAB[browserAppOf(sc)], scripted: false };
    const stock = STAGE_STEPS[stage] || VOICE_TURNS;
    const own = scriptFor(sc, stage);
    return own && own.length === stock.length
      ? { vocab: own, scripted: true }
      : { vocab: stock, scripted: false };
  };

  const tasks = scenarios.map((sc, i) => {
    const { vocab, scripted } = vocabFor(sc);
    const stepCount = 5 + Math.floor(r() * Math.min(vocab.length - 4, 8));
    /* Dwell times are drawn for the stock length exactly as before, so every
       seeded run keeps its verdicts. A scenario's own script is played in
       full — cutting a rule probe off before the agent answers leaves nothing
       to watch — and its extra turns take their timing from a hash. */
    const drawn = Array.from(
      { length: stepCount },
      () => 600 + Math.floor(r() * 1400),
    );
    const length = scripted ? vocab.length : stepCount;
    /* Phrasing belongs to the agent version, not to the run: one prompt means
       one way of opening the call, every time it runs. Two runs of the same
       version therefore read identically — which is what makes a wording
       difference between columns attributable to the version change. */
    const voiceIdx = phrasing;
    const steps = Array.from({ length }, (_, s) => {
      const base = vocab[s % vocab.length];
      const phrasings = base.alts ? [base.text, ...base.alts] : null;
      return {
        id: `${sc.id}-s${s}`,
        index: s,
        ...base,
        ...(phrasings ? { text: phrasings[voiceIdx % phrasings.length] } : {}),
        // Per-step dwell time, in ms of simulated wall clock.
        duration: drawn[s] ?? 600 + (hashSeed(`${sc.id}:d${s}`) % 1400),
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
    /* Who else is in this episode: the scenario's own cast if it names one,
       otherwise one of the environment's actors joins about one in four.
       Picked by hash, not by the run's random stream, so adding actors never
       reshuffles the rest of the run. */
    const ownCast = (sc.actors || [])
      .map((id) => actors.find((a) => a.id === id))
      .filter(Boolean);
    const actorHash = hashSeed(`${sc.id}:actor`);
    const actor =
      ownCast[0] ||
      (actors.length && actorHash % 4 === 0
        ? actors[actorHash % actors.length]
        : null);
    if (actor) {
      /* When they come in is who they are: someone present speaks from the
         start, someone who joins arrives once the agent hands over, and an
         interruption lands wherever it lands. */
      const handover = steps.findIndex((st) => st.meets?.includes("close"));
      const at =
        actor.entry === "present"
          ? 1
          : actor.entry === "joins"
            ? handover >= 0
              ? handover + 1
              : steps.length - 2
            : Math.min(
                steps.length - 1,
                2 + (actorHash % Math.max(1, steps.length - 3)),
              );
      const where = stage === "voice" ? "call" : "conversation";
      const lead =
        { present: `Also on the ${where}`, joins: `Joins the ${where}` }[
          actor.entry
        ] || "Cuts in";
      steps.splice(at, 0, {
        id: `${sc.id}-actor`,
        index: at,
        /* A third party, not the caller — the stage and the transcript
           label it as the actor it is. */
        role: "actor",
        actorId: actor.id,
        actorName: actor.name,
        text: `${lead} — ${actor.goal.charAt(0).toLowerCase()}${actor.goal.slice(1)}`,
        duration: 900,
      });
      steps.forEach((st, k) => {
        st.index = k;
      });
    }

    const fault = {};
    const faultDraw = r();
    /* A scenario the world can no longer stage (its proof broke, or it was
       never admitted) has no verdict to give — it is the environment's to fix. */
    const admission = admissionOf(sc, null, { gates: admissionGates });
    /* The agent reached for a tool this world has no answer for. Scenarios
       that need it always do; elsewhere the agent reaches for it now and then,
       the way a new tool gets used. Deterministic per scenario and tool. */
    const reached = gapTools.find(
      (t) =>
        (sc.requiredTools || []).includes(t.name) ||
        hashSeed(`${sc.id}:${t.name}`) % 6 === 0,
    );
    if (reached) {
      fault.environment = `The agent called ${reached.name}, which this world can't answer — nothing came back, so the scenario can't be scored.`;
      fault.toolGap = reached.name;
    } else if (
      sc.provedBroke &&
      (!sc.brokeAgainst || !envVersion || sc.brokeAgainst === envVersion)
    ) {
      fault.environment =
        "This scenario no longer stages on this world — its proof broke when the environment changed. Re-prove it before reading a verdict.";
    } else if (!admission.admitted) {
      fault.environment = `Quarantined — ${admission.reason}`;
    } else if (faultDraw < 0.035) {
      fault.environment = `${tools[0]?.name || "The seeded world"} never passed its readiness probe — the scenario could not be staged.`;
    } else if (faultDraw < 0.07) {
      fault.transport =
        "The session dropped before the agent answered; no terminal state was reached.";
    } else if (faultDraw < 0.115) {
      fault.simulator =
        "The simulated caller contradicted its own scenario facts and the run was abandoned.";
    } else if (faultDraw < 0.13) {
      fault.grading =
        "Evidence was captured but the grader returned no verdict.";
    }
    /*
      Retry once (updated RSI PRD). A conversation that ended test invalid or
      error is run again before anything is read from it. Drawn by hash, never
      from the run's stream: most drawn faults clear on the second try; a
      fault fixed in the scenario or the world (a tool it can't answer, a
      proof that broke) comes back the same. Test invalid twice and the
      scenario is pulled from the run and flagged for review.
    */
    let retried = null;
    let pulled = null;
    const retryKind = outcomes ? retryKindOf(fault) : null;
    if (retryKind) {
      const first =
        fault.environment ||
        fault.transport ||
        fault.simulator ||
        fault.grading;
      const fixed = !!(
        fault.toolGap || /no longer stages/i.test(fault.environment || "")
      );
      const recovered = !fixed && unit(`${seed}:${sc.id}:retry`) < 0.8;
      retried = { kind: retryKind, first, recovered };
      if (recovered)
        Object.keys(fault).forEach((k) => {
          delete fault[k];
        });
      else if (retryKind === "test-invalid") {
        pulled = {
          reason: `Test invalid twice — ${String(first).replace(/\.$/, "")}. Pulled from this run and flagged for review.`,
        };
      }
    }

    /* Older runs: evaluations decide pass or fail, so with none applied
       there is no verdict to give. Under the sub-goal rule the sub-goals
       decide, and a run with no evaluations still has one. */
    if (
      !subGoalsDecide &&
      !evals.length &&
      !fault.environment &&
      !fault.transport &&
      !fault.simulator
    ) {
      fault.grading =
        "No evaluations were applied, so nothing decided pass or fail.";
    }
    const unmeasured = !!(
      fault.environment ||
      fault.transport ||
      fault.simulator ||
      fault.grading
    );

    /* Sample-to-sample drift in the *caller* rather than the agent. A scenario
       whose caller wanders is flaky because of the scenario. */
    if (!unmeasured && r() < 0.08) fault.simulatorDrift = true;

    // Critical scenarios fail more often — that is the point of marking them.
    // An actor pulling the other way makes the episode harder, too.
    const failChance =
      (sc.critical ? failRate * 1.9 : failRate) + (actor ? 0.06 : 0);
    const draw = r();
    /* A fix's issue scenarios fail as often as its Reproduce step found
       (`forcedSamples`, one per repeat). They run last, so every other
       scenario draws exactly what it always did. */
    const forced = Array.isArray(sc.forcedSamples) ? sc.forcedSamples : null;
    const failed = forced ? forced[0] === "failed" : draw < failChance;
    const drawnFail = failed
      ? Math.max(2, Math.floor(steps.length * (0.45 + r() * 0.45)))
      : null;
    /* A scripted scenario fails where it turns — the agent gives in, skips the
       check, or claims it done — and says so, rather than going quiet at a
       random turn. */
    const cruxAt = steps.findIndex((st) => st.crux);
    const failStep = failed && !unmeasured && cruxAt >= 0 ? cruxAt : drawnFail;
    if (failStep === cruxAt && cruxAt >= 0 && steps[cruxAt].failText) {
      steps[cruxAt] = {
        ...steps[cruxAt],
        text: steps[cruxAt].failText,
        failedHere: true,
      };
    }

    /*
      The graders decide. How well the agent did is drawn above; what the
      scenario's verdict *is* comes from the applied evals against their own
      thresholds. A sample passes only when every grader clears its bar, so a
      strict grader (a PII check at 1.0) is honoured rather than decorative.
      The eval that explains a failure is the culprit: the rule, then the goal.
    */
    const CULPRIT_ORDER = ["policy_adherence", "task_success", "pii_leakage"];
    const culpritId =
      failed && evals.length
        ? CULPRIT_ORDER.find((id) => evals.some((ev) => ev.id === id)) ||
          evals[0].id
        : null;
    const evalResults = evals.map((ev) => {
      const threshold = ev.threshold ?? 0.8;
      const isCulprit = ev.id === culpritId;
      const base = isCulprit
        ? threshold * (0.2 + r() * 0.6)
        : threshold + (1 - threshold) * (0.25 + r() * 0.75);
      const score = Math.min(1, Math.round(base * 100) / 100);
      const passedIt = score >= threshold;
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

    /*
      Sub-goal rule: `failed` is a critical sub-goal that didn't hold. An
      evaluation can still score a conversation low while every critical
      sub-goal held — it is reported, and fails the conversation only when
      it is marked required. Drawn by hash, never from the run's stream, so
      every other number is the one the run always drew.
    */
    let requiredHit = null;
    if (subGoalsDecide && !failed && !unmeasured) {
      evalResults.forEach((e, k) => {
        const ev = evals[k];
        if (hashSeed(`${seed}:${sc.id}:${ev.id}:low`) % 9 !== 0) return;
        const threshold = ev.threshold ?? 0.8;
        const score =
          Math.round(
            threshold *
              (0.55 + unit(`${seed}:${sc.id}:${ev.id}:lowv`) * 0.35) *
              100,
          ) / 100;
        const required = !!(ev.required && requiredEvals);
        const line = `Scored ${Math.round(score * 100)} against a threshold of ${Math.round(threshold * 100)}`;
        evalResults[k] = {
          ...e,
          score,
          passed: false,
          ...(!required && { reportedOnly: true }),
          reason: required
            ? `${line} — a required evaluation, so it fails the conversation.`
            : `${line} — reported only; every critical sub-goal held, so the conversation still passes.`,
        };
        if (required && !requiredHit) requiredHit = evalResults[k];
      });
    }

    /* The verdict of the kept episode. Older runs: the graders' — with none
       applied the scenario is not measured (above). Sub-goal rule: the
       critical sub-goals', or a required evaluation's. */
    const gradedFail = subGoalsDecide
      ? failed || !!requiredHit
      : evalResults.length
        ? evalResults.some((e) => !e.passed)
        : failed;
    const callLog = buildCallLog(sc, tools, r, gradedFail);

    /*
      The remaining samples. The first one is the episode kept in full — its
      transcript, its call log, its graders — because a drawer showing three
      near-identical conversations helps nobody. The rest contribute their
      verdict, which is what the proportion is made of.
    */
    /* Injected tool faults — drawn by hash, never from the run's stream, so a
       run with no faults draws exactly the numbers it always did. */
    const injected =
      !unmeasured && hasFaults(toolFaults)
        ? injectToolFaults({ seed, sc, callLog, toolFaults, stage })
        : null;
    if (injected?.failed && !gradedFail) {
      faultFailEvals(evalResults, injected.failed);
    }
    const samples = [
      forced ? forced[0] : gradedFail || injected?.failed ? "failed" : "passed",
    ];
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
      const flipped = r() < flipChance ? !gradedFail : gradedFail;
      /* A later sample meets the same fault profile, drawn afresh. */
      const faultFailed = injected
        ? sampleFaultFails({ seed, sc, k, callLog, toolFaults })
        : false;
      samples.push(
        forced
          ? forced[k] || forced[forced.length - 1]
          : flipped || faultFailed
            ? "failed"
            : "passed",
      );
    }
    const passes = samples.filter((v) => v === "passed").length;
    /*
      A scenario nothing could be measured on has no verdict — not a failed
      one. Calling it "failed" is the single most common way an eval product
      reports its own outage as the agent's fault.
    */
    let verdict = unmeasured
      ? "unmeasured"
      : passes === samples.length
        ? "passed"
        : passes === 0
          ? "failed"
          : "flaky";

    const finalSamples = samples;
    const finalPasses = passes;

    /* A required evaluation that failed fails the conversation, and is the
       cause the row names — "failed a required evaluation · Policy adherence". */
    const requiredById = new Set(
      evals.filter((ev) => ev.required).map((ev) => ev.id),
    );
    /* Under the sub-goal rule a required evaluation is the cause only when
       it is what failed the conversation — a missed critical sub-goal is the
       agent's failure, named by the sub-goal. */
    const requiredMiss = subGoalsDecide
      ? requiredHit && !unmeasured && verdict !== "passed"
        ? requiredHit
        : null
      : requiredEvals && !unmeasured && verdict !== "passed"
        ? evalResults.find((e) => !e.passed && requiredById.has(e.id))
        : null;

    /*
      The turn limit (pinned `turnLimits`). The conversation is played to
      its scenario's limit — or the environment's — and stopped there; a
      cut-off conversation fails, whatever it would have gone on to do.
      Applied after every draw, so it moves nothing else in the run.
    */
    const limit = turnLimits ? turnLimitOf(sc, stage, turnLimit) : null;
    let cutOff = null;
    if (limit && !unmeasured) {
      let n = 0;
      const at = steps.findIndex((st) => {
        if (isTurn(st)) n += 1;
        return n > limit;
      });
      if (at > 0) {
        steps.splice(at);
        cutOff = { limit, at: at - 1 };
      }
    }
    if (cutOff) verdict = "failed";
    const keptSamples = cutOff
      ? finalSamples.map(() => "failed")
      : finalSamples;
    const keptPasses = cutOff ? 0 : finalPasses;
    /* What failed it, under the sub-goal rule — read by the sub-goal results
       so a required evaluation's failure doesn't invent a missed sub-goal. */
    const failedBy =
      !subGoalsDecide || unmeasured || verdict === "passed"
        ? null
        : cutOff
          ? "turn-limit"
          : requiredMiss && !failed && !injected?.failed
            ? "required-evaluation"
            : "sub-goal";
    /* Cost and response times are recorded when the agent's side reports
       them. A missing number is unverified — never read as a pass or as $0. */
    const reported =
      outcomes && !unmeasured
        ? {
            cost: hashSeed(`${seed}:${sc.id}:cost`) % 9 !== 0,
            response: hashSeed(`${seed}:${sc.id}:response`) % 11 !== 0,
          }
        : null;

    return {
      id: sc.id,
      callLog,
      /* The row label every other screen leads with. */
      name: sc.name,
      title: sc.title,
      task: sc.task,
      /* One-line human summary of what this scenario puts the agent through
         ("Caller is in a hurry, pushes to skip steps") — the context the
         Run details cell leads with. */
      summary: sc.summary,
      persona: sc.persona,
      expected: sc.expected,
      /* An issue's scenario riding on a fix's first run — shown with the run,
         never counted in it or in the environment's history. */
      ...((sc.fixScenario ||
        (Array.isArray(sc.forcedSamples) && sc.fromIssue)) && {
        fixScenario: true,
      }),
      /* What the runner watches for — the same sub-goals the scenario table lists. */
      subTasks: sc.subTasks,
      critical: sc.critical,
      /* Why it's marked critical, as it stood when the run started. v7 has
         no blocker scenarios — the reason is information only. */
      blockerReason:
        (blockerReason(sc) || null)
          ?.replace(
            /(was )?an? release blocker/gi,
            (m, was) => `${was || ""}critical`,
          )
          .replace(/release blocker/gi, "critical scenario") || null,
      /* Carry the scenario's use-case sentence through so downstream
         grouping in the traces table matches the labels the env
         creation Scenarios tab shows. Without this a task falls back
         to a coarse id-derived label ("Routine tasks") and the two
         screens name the same buckets differently. */
      useCase: sc.useCase,
      actor: actor ? { id: actor.id, name: actor.name } : null,
      worker: i % concurrency,
      steps,
      failStep: cutOff ? cutOff.at : failStep,
      status: "queued",
      verdict,
      fault,
      /* The rule that graded it, what failed it, and where the turn limit
         stopped it — only on runs that pinned them. */
      ...(subGoalsDecide && { gradingRule: 2 }),
      ...(failedBy && { failedBy }),
      ...(cutOff && { turnLimit: cutOff }),
      /* What the fault dial did to this episode: each injected fault, and
         whether the agent recovered, escalated or lost the scenario to it. */
      ...(injected?.hits.length
        ? { toolFaults: injected.hits, faultFailed: !!injected.failed }
        : {}),
      measured: !unmeasured,
      samples: unmeasured ? [] : keptSamples,
      repeats: unmeasured ? 0 : keptSamples.length,
      passes: unmeasured ? null : keptPasses,
      /* The share of samples that passed — the number every rate on every
         screen above this one is an average of. Null when there is nothing to
         average, so it can never be silently read as zero. */
      passShare: unmeasured ? null : keptPasses / keptSamples.length,
      ...(retried && { retried }),
      ...(pulled && { pulled }),
      ...(requiredMiss && {
        requiredFail: {
          id: requiredMiss.id,
          name: requiredMiss.name || requiredMiss.id,
        },
      }),
      evalResults,
      durationMs: steps.reduce((a, s) => a + s.duration, 0),
      cost: Math.round((0.02 + r() * 0.14) * 1000) / 1000,
      tokens: 1200 + Math.floor(r() * 6400),
      ...(reported && {
        costUnverified: !reported.cost,
        /* The agent's side didn't report a cost: there is no number to show
           or sum — never a made-up one. */
        ...(!reported.cost && { cost: null }),
        responseMs: reported.response
          ? 280 + (hashSeed(`${sc.id}:response-ms`) % 320)
          : null,
      }),
    };
  });

  /* A voice call lasts minutes, not the seconds its playback takes: every
     duration a reader sees is the real call length, the one that's billed. */
  if (stage === "voice") {
    tasks.forEach((t) => {
      /* Latency is the agent's responsiveness, not the call's length —
         Analytics keeps reading what it always did. */
      if (t.latencyMs == null) t.latencyMs = t.durationMs;
      t.durationMs = Math.round(callMinutesOf(t) * 60000);
    });
  }
  return { tasks, stage, concurrency, repeats };
}

/*
  Which retry a fault earns: "test-invalid" (the simulated user, the starting
  state, the judge), "error" (our infrastructure, the connection) or none
  (not enough evidence — inconclusive is read, not retried). Mirrors
  `causeOf` in decoupled.js, which can't be imported here (it imports
  envModel, which imports this file).
*/
const retryKindOf = (f) => {
  if (f.simulator) return "test-invalid";
  if (f.grading)
    return /no evaluations|no ground truth|not enough evidence|coverage skipped/i.test(
      f.grading,
    )
      ? null
      : "test-invalid";
  if (f.environment) {
    if (!f.toolGap && /quarantin/i.test(f.environment)) return null;
    if (
      !f.toolGap &&
      /never passed its readiness|no longer stages|could not be staged/i.test(
        f.environment,
      )
    )
      return "test-invalid";
    return "error";
  }
  if (f.transport) return "error";
  return null;
};

/* ── run size (updated RSI PRD) ──────────────────────────────────────────── */

/*
  1–20 repeats per scenario, at most 200 conversations per run, and up to 4
  conversations at once where parallel runs are on. Voice runs in real time,
  so a voice run quotes how long it will take before it starts.
*/
export const RUN_LIMITS = {
  minRepeats: 1,
  maxRepeats: 20,
  maxConversations: 200,
  maxParallel: 4,
};

/* Minutes one simulated call takes — the same 2.4 the metering estimate uses. */
const VOICE_CALL_MIN = 2.4;

/** "2 h 10 min", "35 min". */
export const durationLabel = (min) => {
  const m = Math.max(1, Math.round(min));
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const rest = m % 60;
  return rest ? `${h} h ${rest} min` : `${h} h`;
};

/**
 * How many conversations run at once: the environment's "Conversations at
 * once" setting (2–4, default 4) while parallel runs are on, else 1. Only
 * how the run is played out — verdicts never depend on it.
 */
export const parallelOf = (envState) => {
  if (envState?.parallelRuns === false) return 1;
  const saved = Math.round(Number(envState?.runDefaults?.conversationsAtOnce));
  return Number.isFinite(saved) && saved >= 2
    ? Math.min(RUN_LIMITS.maxParallel, saved)
    : RUN_LIMITS.maxParallel;
};

/* ── turn limit (pinned `turnLimits`) ────────────────────────────────────── */

/** The environment's turn limit when nothing else sets one. */
export const DEFAULT_TURN_LIMIT = 12;
export const TURN_LIMIT_RANGE = { min: 4, max: 40 };

/** A turn is one agent reply — or, on surfaces with no dialogue, one action. */
const isTurn = (st) => (st?.role ? st.role === "agent" : true);

/** How many turns a built conversation took. */
export const turnsOf = (steps = []) => steps.filter(isTurn).length;

/**
 * Where a conversation is stopped: the scenario's own `maxTurns`, else — on
 * voice, where the editor's "Max turns" is the scenario's `turns` — that, else
 * the environment's limit.
 */
export const turnLimitOf = (sc, stage, envLimit = DEFAULT_TURN_LIMIT) => {
  const own =
    Number(sc?.maxTurns) || (stage === "voice" ? Number(sc?.turns) : 0);
  return Math.max(1, Math.round(own || Number(envLimit) || DEFAULT_TURN_LIMIT));
};

/** The environment's limit, as Settings saved it. */
export const envTurnLimitOf = (envState) => {
  const v = Math.round(Number(envState?.runDefaults?.turnLimit));
  return Number.isFinite(v) && v >= TURN_LIMIT_RANGE.min
    ? Math.min(TURN_LIMIT_RANGE.max, v)
    : DEFAULT_TURN_LIMIT;
};

/** "Hit the turn limit (12)" — the cause a cut-off conversation fails with. */
export const turnLimitLine = (cut) =>
  cut ? `Hit the turn limit (${cut.limit})` : null;

/**
 * The size a run would be, and whether it fits.
 * { conversations, repeats, over, reason, fit: { scenarios, repeats } | null,
 *   parallel, voice: { serial, parallel, line } | null }
 */
export const runSizeOf = ({
  scenarios = 0,
  repeats = 3,
  surface = null,
  parallel = RUN_LIMITS.maxParallel,
} = {}) => {
  const k = Math.min(
    RUN_LIMITS.maxRepeats,
    Math.max(RUN_LIMITS.minRepeats, Math.floor(Number(repeats) || 3)),
  );
  const conversations = scenarios * k;
  const cap = RUN_LIMITS.maxConversations;
  const over = conversations > cap;
  const fitScenarios = Math.floor(cap / k);
  const fitRepeats = scenarios ? Math.floor(cap / scenarios) : k;
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  const reason = over
    ? `${plural(scenarios, "scenario")} × ${plural(k, "repeat")} = ${conversations} conversations — over the ${cap} limit; run ${plural(fitScenarios, "scenario")}${fitRepeats >= 1 ? ` or ${plural(fitRepeats, "repeat")}` : ""}`
    : null;
  const shown = Math.min(conversations, cap);
  const voice =
    ["voice", "multi"].includes(surface) && shown
      ? (() => {
          const serial = shown * VOICE_CALL_MIN;
          /* Rounded up to 5 minutes — calls in a batch don't end together. */
          const atOnce = Math.ceil(serial / parallel / 5) * 5;
          return {
            serial,
            parallel: atOnce,
            line:
              parallel > 1
                ? `≈ ${durationLabel(serial)} at 1 call at a time · ≈ ${durationLabel(atOnce)} at ${parallel}`
                : `≈ ${durationLabel(serial)} at 1 call at a time`,
          };
        })()
      : null;
  return {
    conversations,
    repeats: k,
    cap,
    over,
    reason,
    fit: over
      ? {
          scenarios: fitScenarios,
          repeats: fitRepeats >= 1 ? fitRepeats : null,
        }
      : null,
    parallel,
    voice,
  };
};

/* ── injected tool faults (PRD v6 Story 5) ───────────────────────────────── */

const unit = (str) => hashSeed(str) / 4294967296;

const hasFaults = (toolFaults) =>
  !!toolFaults &&
  Object.values(toolFaults).some(
    (f) =>
      f &&
      Object.entries(f).some(
        ([k, v]) => /Pct$|latencyMs/.test(k) && Number(v),
      ),
  );

/*
  What the agent does when a call goes wrong, per kind. Retries and backoff
  recover; an escalation hands the caller on (the scenario still ends
  properly); the rest lose the scenario — the agent gave up, or carried on as
  if the call had worked.
*/
const RECOVERY = {
  timeout: [
    ["retried", 0.55],
    ["escalated", 0.15],
    ["failed", 0.3],
  ],
  error: [
    ["retried", 0.5],
    ["escalated", 0.2],
    ["failed", 0.3],
  ],
  rate_limited: [
    ["retried", 0.7],
    ["failed", 0.3],
  ],
  malformed: [
    ["retried", 0.4],
    ["failed", 0.6],
  ],
  slow: [
    ["waited", 0.85],
    ["failed", 0.15],
  ],
  /* The write landed but its answer didn't: a good agent looks it up and
     reuses it; a bad one writes it twice. */
  after_commit: [
    ["reused", 0.5],
    ["failed", 0.5],
  ],
  permission: [
    ["escalated", 0.6],
    ["failed", 0.4],
  ],
  missing: [
    ["asked", 0.6],
    ["failed", 0.4],
  ],
  duplicate: [
    ["ignored", 0.7],
    ["failed", 0.3],
  ],
  stale: [
    ["retried", 0.5],
    ["failed", 0.5],
  ],
  partial: [
    ["retried", 0.4],
    ["failed", 0.6],
  ],
};
const FAIL_SHARE = Object.fromEntries(
  Object.entries(RECOVERY).map(([k, rows]) => [
    k,
    rows.find(([o]) => o === "failed")?.[1] || 0,
  ]),
);

const KIND_LABEL = {
  timeout: "timed out",
  error: "returned a 5xx",
  rate_limited: "was rate-limited",
  malformed: "returned a malformed response",
  slow: "answered slowly",
  after_commit: "committed, then timed out before answering",
  permission: "was denied permission",
  missing: "found no such record",
  duplicate: "delivered the same event twice",
  stale: "returned an out-of-date read",
  partial: "only partly succeeded",
};

/** Which fault, if any, one call draws — by its share of the profile. */
const drawKind = (f, u) => {
  let cum = 0;
  const order = [
    ["timeout", f.timeoutPct],
    ["error", f.errorPct],
    ["rate_limited", f.rateLimitPct],
    ["malformed", f.malformedPct],
    ["after_commit", f.afterCommitPct],
    ["permission", f.permissionPct],
    ["missing", f.missingPct],
    ["duplicate", f.duplicatePct],
    ["stale", f.stalePct],
    ["partial", f.partialPct],
  ];
  for (let i = 0; i < order.length; i += 1) {
    cum += Number(order[i][1] || 0) / 100;
    if (u < cum) return order[i][0];
  }
  return null;
};

const faultNote = (name, kind, outcome, stage, wrote) => {
  const what = `${name} ${KIND_LABEL[kind]}`;
  const party = stage === "voice" ? "the caller" : "the user";
  if (outcome === "retried")
    return kind === "rate_limited"
      ? `${what}; the agent backed off and retried, and the call went through.`
      : kind === "malformed"
        ? `${what}; the agent asked again and got a usable answer.`
        : `${what}; the agent retried and the call went through.`;
  if (outcome === "escalated")
    return `${what}; the agent handed ${party} to a person instead of guessing.`;
  if (outcome === "waited")
    return `${what}; the agent held ${party} and carried on.`;
  if (outcome === "reused")
    return `${what}; the agent looked it up, found the write had landed and reused it — no second one.`;
  if (outcome === "asked")
    return `${what}; the agent asked ${party} to check the details instead of inventing them.`;
  if (outcome === "ignored")
    return `${what}; the agent recognised the repeat and applied it once.`;
  if (kind === "after_commit")
    return `${what}; the agent retried and made a second one — two now exist.`;
  if (kind === "duplicate")
    return `${what}; the agent applied it twice.`;
  if (kind === "stale")
    return `${what}; the agent acted on the old value.`;
  if (kind === "malformed")
    return `${what}; the agent read it as valid and acted on bad data.`;
  if (kind === "slow") return `${what}; ${party} gave up before it came back.`;
  return wrote
    ? `${what}; the agent didn't retry and told ${party} it was done anyway.`
    : `${what}; the agent didn't retry and answered ${party} without it.`;
};

/**
 * The kept episode's calls, with faults applied. Each call to a tool with a
 * profile draws whether it fails (by the profile's shares) and, if it does,
 * how the agent handled it. Extra latency lands on every call to that tool;
 * past a second it counts as a slow answer. Mutates the call log in place —
 * it was built for this task alone.
 */
function injectToolFaults({ seed, sc, callLog, toolFaults, stage }) {
  const hits = [];
  let failed = null;
  callLog.calls.forEach((c) => {
    const f = toolFaults[c.name];
    if (!f) return;
    const key = `${seed}:${sc.id}:${c.id}`;
    const extra = f.latencyMs
      ? Math.round(Number(f.latencyMs) * (0.5 + unit(`${key}:lat`)))
      : 0;
    if (extra) c.ms += extra;
    const kind =
      drawKind(f, unit(`${key}:fault`)) || (extra >= 1000 ? "slow" : null);
    if (!kind) return;
    const v = unit(`${key}:outcome`);
    let cum = 0;
    const outcome = (RECOVERY[kind].find(([, p]) => {
      cum += p;
      return v < cum;
    }) || RECOVERY[kind][RECOVERY[kind].length - 1])[0];
    const note = faultNote(c.name, kind, outcome, stage, c.wrote);
    c.status = kind;
    c.fault = { kind, outcome, note };
    if (outcome === "retried") c.retries = 1;
    hits.push({
      callId: c.id,
      tool: c.name,
      kind,
      outcome,
      note,
      ...(extra && { latencyMs: extra }),
    });
    if (outcome === "failed" && !failed) failed = note;
  });
  return { hits, failed };
}

/** Whether a later sample loses the scenario to a fault — one draw per sample. */
function sampleFaultFails({ seed, sc, k, callLog, toolFaults }) {
  return callLog.calls.some((c) => {
    const f = toolFaults[c.name];
    if (!f) return false;
    const pFail =
      (Number(f.timeoutPct || 0) * FAIL_SHARE.timeout +
        Number(f.errorPct || 0) * FAIL_SHARE.error +
        Number(f.rateLimitPct || 0) * FAIL_SHARE.rate_limited +
        Number(f.malformedPct || 0) * FAIL_SHARE.malformed +
        Number(f.afterCommitPct || 0) * FAIL_SHARE.after_commit +
        Number(f.permissionPct || 0) * FAIL_SHARE.permission +
        Number(f.missingPct || 0) * FAIL_SHARE.missing +
        Number(f.duplicatePct || 0) * FAIL_SHARE.duplicate +
        Number(f.stalePct || 0) * FAIL_SHARE.stale +
        Number(f.partialPct || 0) * FAIL_SHARE.partial) /
        100 +
      (Number(f.latencyMs || 0) >= 1000 ? 0.5 * FAIL_SHARE.slow : 0);
    return unit(`${seed}:${sc.id}:${c.id}:k${k}`) < pFail;
  });
}

/* The scenario was lost to a fault: the goal grader carries it. */
function faultFailEvals(evalResults, note) {
  if (!evalResults.length) return;
  const i = Math.max(
    0,
    evalResults.findIndex((e) => e.id === "task_success"),
  );
  evalResults[i] = {
    ...evalResults[i],
    score: 0.32,
    passed: false,
    reason: `Lost to an injected fault: ${note}`,
  };
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
  voice: [
    "Allocating media servers",
    "Reserving test numbers",
    "Warming TTS voices",
    "Dialling the agent",
  ],
  chat: [
    "Spinning up session pool",
    "Opening webhook channel",
    "Priming conversation state",
    "Sending handshake",
  ],
  browser: [
    "Provisioning sandbox VMs",
    "Restoring seeded database",
    "Launching Chromium",
    "Attaching screen recorder",
  ],
  tools: [
    "Publishing tool manifest",
    "Restoring seeded database",
    "Opening MCP channel",
    "Awaiting agent connect",
  ],
  terminal: [
    "Building container image",
    "Cloning seed repositories",
    "Installing dependencies",
    "Attaching to pty",
  ],
  email: [
    "Provisioning mailboxes",
    "Seeding claim threads",
    "Starting SMTP relay",
    "Awaiting first poll",
  ],
  sim: [
    "Starting physics engine",
    "Loading scene assets",
    "Warming the renderer",
    "Sending first observation",
  ],
  multi: [
    "Allocating media servers",
    "Provisioning mailboxes",
    "Linking channel state",
    "Awaiting first contact",
  ],
};
