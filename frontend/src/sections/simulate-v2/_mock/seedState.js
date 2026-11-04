/**
 * Environments the user already has.
 *
 * A brand-new workspace with an empty "My environments" tab hides half the
 * product — the workspace, the runs, the eval mapping — behind setup nobody has
 * done yet. This seeds a few that look like real work in progress: one built
 * from an agent, one adopted from a template and run a few times, one still
 * half configured.
 *
 * Only used when nothing has been persisted yet, so it never overwrites what
 * someone actually did.
 */

import { getEnvironment } from "./environments";
import { getRows } from "./scenarios";
import { derivedEnvironment } from "./builder";
import { connectionSpecOf, runConnectionCheck } from "./connectionCheck";
import { PRE_VERSIONING_AGENT_NOTE, PRE_VERSIONING_ENV_NOTE, recordedBeforeVersioning, seededEnvHistory } from "./versions";
import { baseRunOf, buildImprovement } from "./improvementStart";
import { DEFAULT_LIMITS, OPT_STATUS, improvementOutcome } from "./optimizationRuns";
import { withDemoFaultRun, withPassingRun } from "./demoRun";
import { withScenarioSetSeed } from "./scenarioSetSeed";
import { withErrorFeedFixes, withAirlineTracing, SEEDED_RETELL_VALUES } from "./seedErrorFeedFixes";

const daysAgo = (n) => new Date(Date.now() - n * 86400000).toISOString();
const hoursAgo = (n) => new Date(Date.now() - n * 3600000).toISOString();

/*
  Credentials, by reference (AC-6.12). Each seeded agent's key sits in the
  team's secret manager; the environment records where, never the value.
  Deepgram on the support line is left unset so the manifest has a "Set
  reference" to show.
*/
export const SEEDED_CREDENTIAL_REFS = {
  "env-voice-support": { vapi_api_key: "vault://acme/sim/vapi_api_key" },
  "env-voice-after-hours": { vapi_api_key: "vault://acme/sim/vapi_api_key", deepgram_api_key: "vault://acme/sim/deepgram_api_key" },
  "env-guest-booking": { retell_api_key: "vault://acme/sim/retell_api_key" },
  "env-clinic-line": { bland_api_key: "vault://acme/sim/bland_api_key" },
  "env-chat-travel": { agent_endpoint_token: "aws-sm://skyline-air/agents/chat-endpoint-token" },
  "env-browser": { sandbox_browser_token: "vault://acme/sim/sandbox_browser_token" },
};
const credentialRefsFor = (id) => SEEDED_CREDENTIAL_REFS[id] || {};

const rowsFor = (env, kinds) =>
  kinds.flatMap((k) => getRows(`${env.id}::${k}`, env));

/*
  A run records which agent version it ran, the seed it ran with, which
  scenarios it covered and how many times it ran each of them. Without the
  version a history is a pile of numbers you cannot scope; without the seed a
  failing scenario cannot be replayed; without the scenario ids a comparison
  will happily line up a 3-scenario smoke run against a full sweep and call the
  difference a regression.
*/
const run = (id, label, finishedAt, scenarios, passed, agentVersion = "v3", seed = 7, ordinal = 1, repeats = 3) => ({
  id,
  label,
  /* Stamped, not counted at render time: deleting run 2 must not turn run 3
     into run 2 in every screenshot anyone has already taken. */
  ordinal,
  finishedAt,
  total: scenarios.length,
  passed,
  agentVersion,
  seed,
  repeats,
  scenarioIds: scenarios.map((sc) => sc.id),
});

/* Agent versions are minted when the agent changes, so a seeded history needs
   them stated — the runs below pin labels out of this list. */
/* `rule` is the instruction the version added — what makes its files differ
   from the one before, so Compare has something to show. */
const agentVersion = (n, daysOld, note, rule) => ({
  id: `agent-v${n}`,
  label: `v${n}`,
  note,
  reach: "endpoint",
  createdAt: daysAgo(daysOld),
  ...(rule && { changes: [{ kind: "instructions", title: "Instructions edited", detail: `Added: "${rule}"`, addendum: `- ${rule}` }] }),
});

/*
  The Customer Support Line agent is improved on a schedule — every Monday at
  09:00, and it may open drafts on its own. One of those runs has already
  happened, so its Constraints tab and the Improvements list have something
  to show for it: the most recent Monday 09:00, searched against the env's
  newest run and checked in both environments the agent is tested in.
*/
const VOICE_AGENT_KEY = "vapi:asst_v1c2f3";

/*
  The Customer Support Line agent is a Vapi squad of three — one agent with
  one version history, whose Definition lists each member with its own
  instructions, model and tools. It serves voice and chat; chat has no
  environment yet. Live-test consent was given when it was connected.
*/
const SUPPORT_SQUAD = [
  {
    id: "front-desk",
    name: "Front desk",
    role: "Answers every call, identifies the caller and routes it",
    instructions: "Greet the caller, confirm who they are with the order number plus the email or postcode on the order — never the phone number alone — then look the order up. Answer delivery questions yourself; hand refunds and returns to the Refunds specialist, and anything angry, legal or about another person's order to Escalations.",
    model: "gpt-4o",
    tools: ["lookup_order", "shipment_status", "update_shipping_address", "cancel_order"],
  },
  {
    id: "refunds",
    name: "Refunds specialist",
    role: "Returns, refunds and return labels",
    instructions: "Check the return fits the 30-day window with check_return_eligibility before promising anything. Refund to the original payment method only, up to the order total; refunds above $200 need a supervisor — hand those to Escalations. Email a return label once the return is agreed.",
    model: "gpt-4o",
    tools: ["check_return_eligibility", "issue_refund", "generate_return_label", "lookup_order"],
  },
  {
    id: "escalations",
    name: "Escalations",
    role: "Supervisor approvals and warm transfers",
    instructions: "Take over refunds above $200, disputes and upset callers. Summarise what the caller already said so they don't repeat it, then warm-transfer to a supervisor with escalate_to_human. Never share another customer's order details.",
    model: "gpt-4.1",
    tools: ["escalate_to_human", "lookup_order"],
  },
];
const supportLineAgent = () => ({
  typeId: "voice_platform",
  values: {
    provider: "vapi", agentId: "asst_v1c2f3", callDirection: "inbound", hasApiKey: true,
    /* Its real calls feed the Error Feed — the issues Fix starts from. */
    observability: { on: true, since: daysAgo(0), project: "customer-support-line-agent" },
    channels: ["voice", "chat"],
    liveTestConsent: { at: daysAgo(0), replayFlag: false },
  },
  via: "endpoint",
  sourceKind: "platform",
  definition: { kind: "platform", members: SUPPORT_SQUAD },
  connectedAt: daysAgo(0),
});
const VOICE_SCHEDULE = { mode: "weekly", day: "mon", time: "09:00", autonomy: "drafts" };

const lastMondayAt9 = () => {
  const at = new Date();
  at.setHours(9, 0, 0, 0);
  at.setDate(at.getDate() - ((at.getDay() + 6) % 7));
  if (at > new Date()) at.setDate(at.getDate() - 7);
  return at;
};

const scheduledImprovement = (env, es, regressionEnvIds) => {
  const base = baseRunOf(env, es);
  if (!base?.tasks) return null;
  const limits = { ...DEFAULT_LIMITS, schedule: VOICE_SCHEDULE };
  const at = lastMondayAt9();
  const record = {
    ...buildImprovement({
      env, es, tasks: base.tasks, seed: `schedule-${env.id}-0`,
      fromRunId: base.id,
      baseAgentVersion: base.agentVersion || "v1",
      trigger: { kind: "schedule", cadence: "weekly", autonomy: "drafts", goal: { id: "pass-rate", label: "Pass rate" } },
      limits, budget: limits.budget.dollars, regressionEnvIds,
      /* Recorded before Fits the environment existed — kept exactly as seeded. */
      compatCheck: 0,
    }),
    status: OPT_STATUS.COMPLETED,
    createdAt: at.toISOString(),
    completedAt: new Date(at.getTime() + 42 * 60000).toISOString(),
  };
  /* Its autonomy cap is drafts, so a run that found a change opened one on
     Vapi — and stopped there. Nothing went live. */
  const ready = improvementOutcome(record).ending.key === "ready";
  return { record, draft: ready ? { where: "Vapi", at: record.completedAt } : null };
};

/*
  A code fix, proven. The Airline agent lives in a repository and may take
  code changes, so its last improvement — towards the pass rate — found one
  and verified it: the Phase 1 "code fix as a draft PR" path, ready to open
  its pull request from the improvement page.
*/
const AIRLINE_LIMITS = { ...DEFAULT_LIMITS, allowed: ["prompt", "tools", "code", "structure"] };
export const AIRLINE_AGENT_KEY = "repo:https://github.com/skyline-air/rebooking-agent";
const codeFixImprovement = (env, es) => {
  const base = baseRunOf(env, es);
  if (!base?.tasks) return null;
  return {
    ...buildImprovement({
      env, es, tasks: base.tasks, seed: "code-demo-22",
      fromRunId: base.id,
      baseAgentVersion: base.agentVersion || "v1",
      trigger: { kind: "goal", goal: { id: "pass-rate", label: "Pass rate" } },
      limits: AIRLINE_LIMITS, budget: AIRLINE_LIMITS.budget.dollars, regressionEnvIds: [env.id],
      /* Recorded before Fits the environment existed — kept exactly as seeded. */
      compatCheck: 0,
    }),
    status: OPT_STATUS.COMPLETED,
    createdAt: daysAgo(1),
    completedAt: daysAgo(1),
    seededAs: "code-fix",
  };
};

const REFUNDS_DESK_REPO = "https://github.com/acme-inc/refunds-desk";
/* Ten scenarios, four of which refund: routine, two window rules, an expired card. */
const REFUNDS_DESK_CASES = [
  "core-0-routine", "core-4-routine", "core-5-routine", "core-6-routine", "core-7-routine",
  "rule-0-declined", "rule-0-sympathy", "rule-2-declined", "trap-1-anomaly", "trap-3-recover",
];
const refundsDeskAgent = (env) => {
  const tools = env.tools.map((t) => t.name);
  const definition = {
    kind: "repo",
    repoUrl: REFUNDS_DESK_REPO,
    ref: "main",
    entry: "app/main.py",
    runCommand: "uvicorn app.main:app --port 8080",
    tools,
    /* What reading the code found: refunds post to a production URL. */
    hardCodedTools: ["issue_refund"],
  };
  const agent = {
    name: "Refunds desk",
    typeId: "chat_webhook",
    values: {
      repoUrl: REFUNDS_DESK_REPO,
      /* The session field was confirmed when it was connected. */
      connection: { sessionField: "session_id" },
    },
    via: "endpoint",
    sourceKind: "repo",
    definition,
    connectedAt: daysAgo(3),
  };
  const spec = connectionSpecOf(agent, { runCommand: definition.runCommand });
  return {
    ...agent,
    values: {
      ...agent.values,
      connectionTest: { at: daysAgo(1), ...runConnectionCheck(agent, spec, tools) },
    },
  };
};

export function seededState() {
  const built = {
    ...derivedEnvironment({ kind: "repo", value: "https://github.com/acme/returns-line-agent" }),
    adoptedAt: daysAgo(2),
  };
  /*
    The capability report's reference agent (TH-8877): a refunds service
    from GitHub whose issue_refund URL is hard-coded to production. Its last
    connection check says so — Controlled tool routing is Blocked for the 4
    of its 10 scenarios that refund. Fix → Point it at the test world, then
    Re-check, clears it.
  */
  const refundsDesk = {
    ...derivedEnvironment(
      { kind: "repo", value: REFUNDS_DESK_REPO, channel: "chat", envName: "Refunds desk environment" },
      { id: "env-refunds-desk" },
    ),
    adoptedAt: daysAgo(3),
  };
  const browser = { ...getEnvironment("env-browser"), adoptedAt: daysAgo(6) };
  const banking = { ...getEnvironment("env-chat-banking"), adoptedAt: daysAgo(1) };
  const travel = { ...getEnvironment("env-chat-travel"), adoptedAt: daysAgo(4) };
  const voice = { ...getEnvironment("env-voice-support"), adoptedAt: daysAgo(0) };
  /*
    The same Customer Support Line agent, tested in a second world: calls
    after the supervisors have gone home. One agent in two environments is
    what "Also run in" and an improvement's regression check need to show.
  */
  const afterHours = {
    ...voice,
    id: "env-voice-after-hours",
    name: "Customer Support Line · After hours",
    tagline: "The same phone line after 8pm, with no supervisor to transfer to",
    description:
      "Calls that arrive after the support floor has closed. Refunds over the limit and disputes can't be escalated live, so the agent has to take a callback instead of promising one now.",
    official: false,
    seed: {
      tables: [
        { name: "orders", rows: 300, note: "18% delayed, placed in the last 48 hours" },
        { name: "customers", rows: 150, note: "25 with an open callback" },
        { name: "callbacks", rows: 60, note: "booked for the next morning" },
      ],
    },
    rules: [
      ...voice.rules,
      "After 8pm there is no live supervisor — book a callback instead of transferring",
    ],
    adoptedAt: daysAgo(0),
  };
  /*
    The second archetype: connect-only. A restaurant's phone agent, hosted on
    Retell, that calls its own booking system — there is no world of ours to
    seed or reset, so the contract lists the agent's tools and nothing answers
    them but the agent's own systems. Every sub-goal is judged from the call.
  */
  const guestBooking = {
    ...getEnvironment("env-voice-support"),
    id: "env-guest-booking",
    name: "Guest booking line",
    domain: "travel",
    tagline: "A restaurant's phone line that takes, moves and cancels bookings",
    description:
      "Guests call to book a table, move a booking or cancel one. The agent runs on Retell and books straight into the restaurant's own system, so a test call lands on the same calendar a real one would.",
    official: false,
    worldMode: "connect-only",
    seed: { tables: [] },
    /* The agent's tools as its prompt names them — the contract, not a world. */
    tools: [
      { name: "check_availability", desc: "Find open tables for a party size and time" },
      { name: "create_booking", desc: "Book a table under the guest's name" },
      { name: "modify_booking", desc: "Move a booking to a new time or party size" },
      { name: "cancel_booking", desc: "Cancel a booking and release the table" },
    ],
    rules: [
      "Greet with the restaurant's name before anything else",
      "Read the booking back — date, time, party size, name — before confirming it",
      "Parties over 8 go to the events team; never book them directly",
    ],
    /* One caller account every scenario books under; its credentials are the
       agent's configuration, supplied per run. */
    fixtures: { callerAccounts: 1 },
    evalPreset: ["task_success", "policy_adherence"],
    adoptedAt: daysAgo(5),
  };

  /*
    A Bland agent, connected with its call logs on — so its Observe project
    is linked from the start, and its Error Feed issues read Fix with no
    connect step. A clinic's phone line that books into the clinic's own
    scheduler: connect-only like the guest booking line, Live, every
    sub-goal judged from the call.
  */
  const clinicLine = {
    ...getEnvironment("env-voice-support"),
    id: "env-clinic-line",
    name: "Clinic appointment line",
    domain: "healthcare",
    tagline: "A clinic's phone line that books, moves and cancels appointments",
    description:
      "Patients call to book an appointment, move one or cancel it. The agent runs on Bland and books straight into the clinic's own scheduler, so a test call lands on the same calendar a real one would.",
    official: false,
    worldMode: "connect-only",
    seed: { tables: [] },
    tools: [
      { name: "find_slots", desc: "Find open slots for a clinician and visit type" },
      { name: "book_appointment", desc: "Book a slot under the patient's name and date of birth" },
      { name: "reschedule_appointment", desc: "Move an appointment to a new slot" },
      { name: "cancel_appointment", desc: "Cancel an appointment and release the slot" },
    ],
    rules: [
      "Confirm the patient's name and date of birth before changing any appointment",
      "Never give medical advice — offer a nurse callback instead",
      "Urgent symptoms get the emergency message; never book them as a routine visit",
    ],
    fixtures: { callerAccounts: 1 },
    evalPreset: ["task_success", "policy_adherence"],
    adoptedAt: daysAgo(3),
  };

  /*
    Seed each env with the full scenario spread — core tool coverage,
    rule enforcement, data traps, and adversarial + edge probes. A
    two-kind slice ("core" + "rules" only) collapsed a run into two
    use-case buckets on the traces table, which is not what a real
    sweep looks like. Every kind here contributes at least one group.
  */
  const builtScenarios = rowsFor(built, ["core", "rules", "traps", "adversarial", "edge"]);
  /* The earlier run was a rule-probe pass, not a full sweep. */
  const builtRuleProbes = rowsFor(built, ["rules"]).slice(0, 3);
  /* Proved on the current version, so the only thing holding any back is
     the capability report — what this environment demonstrates. */
  const refundsScenarios = rowsFor(refundsDesk, ["core", "rules", "traps"])
    .filter((r) => REFUNDS_DESK_CASES.some((c) => r.id === `${refundsDesk.id}-${c}`))
    .map((r) => ({ ...r, provedAgainst: "v3", provedOn: ["v3"], provedAt: daysAgo(1) }));
  const browserScenarios = rowsFor(browser, ["core", "traps", "rules", "adversarial", "edge"]);
  const bankingScenarios = rowsFor(banking, ["core", "rules", "traps"]);
  const travelScenarios = rowsFor(travel, ["core", "rules", "traps", "adversarial", "edge"]);
  /* The first run only probed the entitlement rules — the expensive ones. */
  const travelRuleProbes = rowsFor(travel, ["rules"]).slice(0, 4);
  const voiceScenarios = rowsFor(voice, ["core", "rules", "traps", "adversarial", "edge"]);
  const afterHoursScenarios = rowsFor(afterHours, ["core", "rules", "traps"]);
  /* No data, so no traps: two core calls per tool, every rule probed, the edges. */
  const guestScenarios = [
    ...rowsFor(guestBooking, ["core"]).filter((r) => /-(routine|rushed)$/.test(r.id)),
    ...rowsFor(guestBooking, ["rules", "edge"]),
  ];
  const clinicScenarios = [
    ...rowsFor(clinicLine, ["core"]).filter((r) => /-(routine|rushed)$/.test(r.id)),
    ...rowsFor(clinicLine, ["rules", "edge"]),
  ];

  const withCodeFixRun = (es) => {
    const record = codeFixImprovement(travel, es);
    return record ? { ...es, optimizations: [record] } : es;
  };

  const withScheduledRun = (es) => {
    const seeded = scheduledImprovement(voice, es, [voice.id, afterHours.id]);
    if (!seeded) return es;
    return {
      ...es,
      optimizations: [seeded.record],
      ...(seeded.draft && { handoffDrafts: { [seeded.record.id]: seeded.draft } }),
    };
  };

  const seeded = {
    myEnvironments: [voice, afterHours, guestBooking, clinicLine, built, refundsDesk, travel, banking, browser],
    byEnv: {
      // built from an agent, fully set up, run twice
      [built.id]: {
        agent: {
          typeId: "voice_platform",
          /* Keys must match voice_platform's field keys — `direction` was not
             one, so the seeded agent rendered a single row. */
          values: {
            provider: "livekit", agentId: "asst_9f2c1188", callDirection: "inbound", hasApiKey: true,
            /* Watched in production since before the environment was built —
               the source of its Error Feed clusters and From production scenarios. */
            observability: { on: true, since: daysAgo(14), project: "returns-refunds-line-agent" },
            /* Runs live in their LiveKit project — consented when connected. */
            liveTestConsent: { at: daysAgo(2), replayFlag: true },
          },
          via: "endpoint",
          /* Anchor for the version-lock: every new version added to this
             agent has to come from the same source origin. */
          sourceKind: "platform",
          connectedAt: daysAgo(2),
        },
        scenarios: builtScenarios,
        /* A real history for the demo — stated here, not invented for
           every environment that has none. */
        /* The returns line was running before versioning started, so its
           first versions are recorded, not built or connected. */
        envVersions: seededEnvHistory(built, builtScenarios.length).map((v, i) => (i ? v : recordedBeforeVersioning(v, PRE_VERSIONING_ENV_NOTE))),
        scenarioSource: "agent",
        /*
          Every seeded env has at least Task success + Policy adherence
          applied — a run can't happen without a grader, and leaving
          this empty meant historical runs had no evalResults and the
          traces table rendered no Evals columns. These two are the
          catch-all pair every surface supports.
        */
        evals: ["task_success", "policy_adherence"],
        agentVersions: [
          recordedBeforeVersioning(agentVersion(1, 12), PRE_VERSIONING_AGENT_NOTE),
          agentVersion(2, 6, "Goodwill cap moved out of the prompt and into code.", "Never state a goodwill credit amount — apply_goodwill_credit decides it."),
          agentVersion(3, 2, "Refund quoting moved before the promise.", "Quote the refund with get_refund_quote before promising any amount."),
        ],
        runs: [
          run("run-r2", "All scenarios", daysAgo(1), builtScenarios, builtScenarios.length - 1, "v3", 7, 2),
          run("run-r1", "Rule probes", daysAgo(2), builtRuleProbes, 2, "v2", 41, 1),
        ],
      },

      /*
        A chat environment that is actually wired up. The other chat one is
        deliberately half-configured — it shows what setup looks like before
        anyone has done it — which left nothing to demonstrate the chat run,
        the chat transcript or a chat comparison against.
      */
      [refundsDesk.id]: {
        agent: refundsDeskAgent(refundsDesk),
        scenarios: refundsScenarios,
        /* Its release checks passed on v3: reset and hidden checks are proved,
           so the only thing in the way is the agent's production tool. */
        releaseChecks: {
          version: "v3",
          at: daysAgo(2),
          results: { starts: "passed", resets: "passed", reference: "passed", wrong: "passed", leaks: "passed" },
        },
        envVersions: seededEnvHistory(refundsDesk, refundsScenarios.length),
        scenarioSource: "agent",
        evals: ["task_success", "policy_adherence"],
        agentVersions: [agentVersion(1, 3, "First version connected.")],
        runs: [],
      },

      [travel.id]: withCodeFixRun({
        agent: {
          typeId: "chat_webhook",
          values: {
            endpoint: "https://api.skyline-air.com/agent/chat",
            auth: "bearer",
            messagePath: "$.choices[0].message.content",
            sessionPath: "$.session_id",
            streaming: true,
          },
          via: "endpoint",
          sourceKind: "repo",
          /* Read from its repository, reached at its endpoint. */
          definition: { kind: "repo", repoUrl: "https://github.com/skyline-air/rebooking-agent", ref: "main" },
          connectedAt: daysAgo(9),
        },
        scenarios: travelScenarios,
        /* A real history for the demo — stated here, not invented for
           every environment that has none. */
        envVersions: seededEnvHistory(travel, travelScenarios.length),
        scenarioSource: "templates",
        /*
          Every seeded env has at least Task success + Policy adherence
          applied — a run can't happen without a grader, and leaving
          this empty meant historical runs had no evalResults and the
          traces table rendered no Evals columns. These two are the
          catch-all pair every surface supports.
        */
        evals: ["task_success", "policy_adherence"],
        agentVersions: [
          agentVersion(1, 9, "First version connected."),
          agentVersion(2, 4, "Entitlement check moved ahead of the compensation offer.", "Run check_compensation before offering any voucher or rebooking credit."),
        ],
        runs: [
          run("run-t2", "All scenarios", daysAgo(1), travelScenarios, travelScenarios.length - 2, "v2", 23, 2),
          run("run-t1", "Entitlement probes", daysAgo(4), travelRuleProbes, 2, "v1", 88, 1),
        ],
      }),

      // adopted template, agent reached at its endpoint, one run
      [browser.id]: {
        agent: {
          typeId: "browser_agent",
          values: { framework: "playwright", viewport: "1280x800", recordVideo: true, endpoint: "https://agent.browser-ops.dev/v1/act",
            /* Consented when it was connected; no webhook yet, so it runs
               observed-only — the caveat the demo shows. */
            endpointConsent: { acknowledgedAt: daysAgo(9), webhookAt: null } },
          via: "endpoint",
          sourceKind: "endpoint",
          connectedAt: daysAgo(9),
        },
        scenarios: browserScenarios,
        /* A real history for the demo — stated here, not invented for
           every environment that has none. */
        envVersions: seededEnvHistory(browser, browserScenarios.length),
        scenarioSource: "templates",
        /*
          Every seeded env has at least Task success + Policy adherence
          applied — a run can't happen without a grader, and leaving
          this empty meant historical runs had no evalResults and the
          traces table rendered no Evals columns. These two are the
          catch-all pair every surface supports.
        */
        evals: ["task_success", "policy_adherence"],
        agentVersions: [
          agentVersion(1, 9, "First version connected."),
          /* An endpoint's instructions aren't visible, so its change is a note only. */
          agentVersion(2, 5, "Selector strategy switched to role-based queries."),
        ],
        runs: [run("run-b1", "Core tasks", daysAgo(4), browserScenarios, browserScenarios.length, "v2", 12, 1)],
      },

      // half set up — scenarios chosen, no agent yet
      [banking.id]: {
        agent: null,
        scenarios: bankingScenarios,
        /* A real history for the demo — stated here, not invented for
           every environment that has none. */
        envVersions: seededEnvHistory(banking, bankingScenarios.length),
        scenarioSource: "templates",
        /*
          Every seeded env has at least Task success + Policy adherence
          applied — a run can't happen without a grader, and leaving
          this empty meant historical runs had no evalResults and the
          traces table rendered no Evals columns. These two are the
          catch-all pair every surface supports.
        */
        evals: ["task_success", "policy_adherence"],
        agentVersions: [],
        runs: [],
      },

      /*
        Voice support template — adopted today. Full scenario spread
        (8 tools × 4 variants + 6 rules × 3 variants + traps + adversarial
        + edge) so the traces table has ~14 use-case groups from the
        first run and demonstrates the group-header aggregates cleanly.
      */
      [voice.id]: withScheduledRun({
        agent: supportLineAgent(),
        scenarios: voiceScenarios,
        /* A real history for the demo — stated here, not invented for
           every environment that has none. */
        envVersions: seededEnvHistory(voice, voiceScenarios.length),
        scenarioSource: "templates",
        evals: ["task_success", "policy_adherence"],
        agentVersions: [agentVersion(1, 0, "First version connected today.")],
        /* Seed with a single completed full-sweep run so a first-time
           visit lands on a populated summary (chart + traces) rather than
           an empty "0 runs" state. */
        runs: [
          /* A few hours back, so the demo fault run after it is the newer one. */
          run("run-voice-1", "All scenarios", hoursAgo(5), voiceScenarios, voiceScenarios.length - 2, "v1", 7, 1),
        ],
      }),

      /* The same agent — same provider and assistant, so the same identity —
         in the after-hours world. One baseline run. */
      [afterHours.id]: {
        agent: supportLineAgent(),
        scenarios: afterHoursScenarios,
        envVersions: seededEnvHistory(afterHours, afterHoursScenarios.length),
        scenarioSource: "templates",
        evals: ["task_success", "policy_adherence"],
        agentVersions: [agentVersion(1, 0, "First version connected today.")],
        runs: [
          run("run-after-hours-1", "All scenarios", daysAgo(0), afterHoursScenarios, afterHoursScenarios.length - 4, "v1", 7, 1),
        ],
      },

      /*
        A Retell agent, its configuration imported through the API (its
        instructions below). Live: its tools reach the restaurant's own
        calendar, so there's no sealed world. Two
        sweeps of the same agent version over the same suite, three days
        apart — and the later one passed fewer. Nothing on our side moved, so
        Overview points at the caller account in their system, not at a
        regression.
      */
      [guestBooking.id]: {
        agent: {
          typeId: "voice_platform",
          values: {
            provider: "retell", agentId: "asst_gb_7k2m", callDirection: "inbound", hasApiKey: true,
            /* Seed 19: live numbers pinned (so a Retell draft can't take real
               calls), its calls watched — ISS-4529 came from them — and
               consent for live tests given when it was connected. */
            ...SEEDED_RETELL_VALUES(daysAgo(4)),
          },
          via: "endpoint",
          sourceKind: "platform",
          definition: {
            kind: "platform",
            prompt: [
              "You answer the phone for Marlow & Vine. Greet every caller with the restaurant's name before anything else.",
              "Use check_availability before offering a time, and create_booking, modify_booking or cancel_booking to act on the calendar.",
              "Read the booking back — date, time, party size, name — and confirm only once the guest agrees.",
              "Parties over 8 go to the events team. Take their number; never book them directly.",
            ].join("\n"),
          },
          connectedAt: daysAgo(5),
        },
        scenarios: guestScenarios,
        /* One version: the contract read from the imported instructions. No
           seed — the agent calls its own systems, so there's no world to build. */
        envVersions: [{
          id: `${guestBooking.id}-v1`, label: "v1", createdAt: daysAgo(5),
          note: "First build, read from the imported Retell instructions — the contract only; this agent calls its own systems.",
          scenarios: guestScenarios.length, changed: ["contract"], builtFor: "v1",
        }],
        scenarioSource: "templates",
        evals: ["task_success", "policy_adherence"],
        agentVersions: [agentVersion(1, 5, "First version connected — the prompt pasted from Retell.")],
        runs: [
          /* Outcomes are rebuilt from the run id; these two read 77% and 64%. */
          run("run-guest-later", "All scenarios", daysAgo(0), guestScenarios, 9, "v1", 19, 2),
          run("run-guest-1", "All scenarios", daysAgo(3), guestScenarios, 15, "v1", 7, 1),
        ],
      },

      /*
        A Bland agent, connected through the API with call logs on: its
        calls land in the clinic-appointment-line Observe project, where
        ISS-4557 came from. Consent for live tests given when it was
        connected — its tools reach the clinic's own scheduler.
      */
      [clinicLine.id]: {
        agent: {
          typeId: "voice_platform",
          values: {
            provider: "bland", agentId: "agt_clinic_4h2p", callDirection: "inbound", hasApiKey: true,
            observability: { on: true, since: daysAgo(3), project: "clinic-appointment-line" },
            liveTestConsent: { at: daysAgo(3), replayFlag: false },
          },
          via: "endpoint",
          sourceKind: "platform",
          definition: {
            kind: "platform",
            prompt: [
              "You answer the phone for Harbourside Family Clinic.",
              "Confirm the patient's full name and date of birth before you look up, book, move or cancel anything.",
              "Use find_slots before offering a time, then book_appointment, reschedule_appointment or cancel_appointment.",
              "Never give medical advice. Offer a nurse callback instead.",
              "If the caller describes urgent symptoms, read the emergency message and do not book a routine visit.",
            ].join("\n"),
          },
          connectedAt: daysAgo(3),
        },
        scenarios: clinicScenarios,
        envVersions: [{
          id: `${clinicLine.id}-v1`, label: "v1", createdAt: daysAgo(3),
          note: "First build, read from the imported Bland agent — the contract only; this agent calls the clinic's own scheduler.",
          scenarios: clinicScenarios.length, changed: ["contract"], builtFor: "v1",
        }],
        scenarioSource: "templates",
        evals: ["task_success", "policy_adherence"],
        agentVersions: [agentVersion(1, 3, "First version connected — imported from Bland.")],
        runs: [
          run("run-clinic-1", "All scenarios", daysAgo(1), clinicScenarios, Math.max(1, clinicScenarios.length - 5), "v1", 11, 1),
        ],
      },
    },
    /* What improvements may change on the Customer Support Line agent — the
       safe defaults, plus its weekly schedule. */
    agentLimits: {
      [VOICE_AGENT_KEY]: { ...DEFAULT_LIMITS, schedule: VOICE_SCHEDULE },
      [AIRLINE_AGENT_KEY]: AIRLINE_LIMITS,
    },
    /* Agent improvement is a design partner preview — on in this workspace. */
    flags: { agentImprovement: true },
    activeRun: null,
  };

  /* Where each agent's key lives — a reference, never the key. */
  Object.keys(seeded.byEnv).forEach((id) => {
    const refs = id === built.id ? { livekit_api_key: "vault://acme/sim/livekit_api_key" } : credentialRefsFor(id);
    if (Object.keys(refs).length) seeded.byEnv[id] = { ...seeded.byEnv[id], credentialRefs: refs };
  });
  /* The finished fault-injection run on the support line, so Compare and
     Insights have something to show before anyone has run anything. Added
     after the scheduled improvement, which was searched against run 1. */
  seeded.byEnv[voice.id] = withDemoFaultRun(voice, seeded.byEnv[voice.id]);
  /* …and one run that passes the release gate, on the returns line. */
  seeded.byEnv[built.id] = withPassingRun(built, seeded.byEnv[built.id]);
  /* The scenario set read through its migration (retired → archived). */
  Object.keys(seeded.byEnv).forEach((id) => { seeded.byEnv[id] = withScenarioSetSeed(id, seeded.byEnv[id]); });
  /* Seed 19 (RSI PRD exit criteria): Error Feed issues that reach a reviewed
     proposal — a code fix, a Vapi copy, a Retell draft, one that couldn't be
     reproduced and one with no safe improvement. Added last, after every run
     and every other improvement, so none of those change. */
  const fixed = withErrorFeedFixes(seeded, seeded.myEnvironments);
  return { ...fixed, byEnv: withAirlineTracing(fixed.byEnv, daysAgo(9)) };
}
