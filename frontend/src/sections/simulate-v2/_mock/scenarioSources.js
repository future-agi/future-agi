/*
  Scenario sources from the RSI PRD — failing cases, the Error Feed and
  production conversations — as the Add scenarios routes and the seed read them.

  · Failing cases. Picking a failed scenario or call generates new variations
    of it — a different persona, different wording, different world values —
    with the same goal and sub-goals, grouped under their source ("scenario
    group": `row.groupId` = the source scenario's id, on the source and on
    every variation). Each variation must still fail on the agent version
    under test; one that now passes reads "Already fixed" and isn't added.
  · Error Feed. The issue's conversations become scenarios with an inferred
    persona and the failing step as a sub-goal, each marked Reproduced, Not
    reproduced or Inconclusive. Only reproduced ones become targets.
  · Production, word for word (Phase 2). A production conversation's caller
    turns, replayed verbatim by the simulated caller.

  Anything built from production keeps the problem, not the person: names,
  phone numbers, emails, addresses, account and card numbers are replaced with
  fake values before it is saved (`scrubPersonalData`), and the call audio is
  never reused. Every source arrives as a proposal the user reviews and adds.

  No React, no store — the import routes and the seed both call these.
*/
import { scrubPersonalData } from "./rsiModel";
import { currentAgentVersion } from "./versions";
import { subGoalsOfScenario } from "./envModel";

const hash = (s) => {
  let h = 2166136261;
  const str = String(s || "");
  for (let i = 0; i < str.length; i += 1) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
};
const sentence = (t = "") => {
  const s = String(t).trim();
  return s ? s.replace(/\.?$/, ".") : "";
};
const lowerFirst = (t = "") => (t ? t.charAt(0).toLowerCase() + t.slice(1) : t);

/* Every source lands as a proposal — it joins the environment once someone adds it. */
export const asProposal = (row, source, at = new Date().toISOString()) => ({
  ...row,
  proposed: row.proposed || { at, source },
});

/* ── personal data ───────────────────────────────────────────────────────── */

const TEXT_FIELDS = ["title", "summary", "task", "situation", "expected", "outcome"];

/**
 * A production-derived scenario with its personal data replaced before it is
 * saved. Every text field, the persona's name and any replayed turns go
 * through `scrubPersonalData` with the names found; the count lands on
 * `row.personalData` ("Personal data replaced · 5 values").
 */
export const scrubScenario = (row, { names = [] } = {}) => {
  const all = { names: 0, phones: 0, emails: 0, addresses: 0, numbers: 0 };
  const take = (text) => {
    const r = scrubPersonalData(text, { names });
    Object.keys(all).forEach((k) => { all[k] += r.replaced[k] || 0; });
    return r.text;
  };
  const next = { ...row };
  TEXT_FIELDS.forEach((f) => { if (typeof row[f] === "string") next[f] = take(row[f]); });
  if (row.persona?.name) next.persona = { ...row.persona, name: take(row.persona.name) };
  if (row.productionReplay?.turns) {
    next.productionReplay = { ...row.productionReplay, turns: row.productionReplay.turns.map((t) => ({ ...t, text: take(t.text) })) };
  }
  const count = Object.values(all).reduce((a, b) => a + b, 0);
  return {
    ...next,
    personalData: { replaced: count, kinds: all, audioReused: false },
  };
};

/** "Personal data replaced · 5 values", or null for a row that came from nowhere personal. */
export const personalDataLine = (row) => {
  const n = row?.personalData?.replaced;
  if (n == null) return null;
  return n ? `Personal data replaced · ${n} value${n === 1 ? "" : "s"}` : "No personal data found";
};

/** What was replaced, by kind — for the tooltip. */
export const personalDataDetail = (row) => {
  const k = row?.personalData?.kinds;
  if (!k) return null;
  const parts = [
    k.names && `${k.names} name${k.names === 1 ? "" : "s"}`,
    k.phones && `${k.phones} phone number${k.phones === 1 ? "" : "s"}`,
    k.emails && `${k.emails} email${k.emails === 1 ? "" : "s"}`,
    k.addresses && `${k.addresses} address${k.addresses === 1 ? "" : "es"}`,
    k.numbers && `${k.numbers} account or card number${k.numbers === 1 ? "" : "s"}`,
  ].filter(Boolean);
  return `${parts.length ? `${parts.join(", ")} replaced with fake values. ` : ""}The scenario keeps the problem, not the person — and the call audio is never reused.`;
};

/* ── failing cases → variations ──────────────────────────────────────────── */

export const VARIATIONS_PER_CASE = 3;

const WORDINGS = [
  { id: "direct", label: "Asks outright", lead: "Opens by asking for it outright, in one sentence" },
  { id: "roundabout", label: "Talks around it", lead: "Gets to the request only after some back-and-forth about something else" },
  { id: "corrects", label: "Corrects a detail", lead: "Gives one detail wrong at first and corrects it partway through" },
  { id: "rushed", label: "Short on time", lead: "Is short on time and wants it done in one go" },
  { id: "asks-twice", label: "Asks twice", lead: "Asks the same thing a second way when the first answer is vague" },
];

/* World values a variation changes — the record it asks about, not what it asks. */
const worldValuesOf = (key) => {
  const h = hash(key);
  const ref = 10000 + (h % 89999);
  const amount = `${20 + (h % 380)}.${String(h % 100).padStart(2, "0")}`;
  const day = 2 + (h % 26);
  return {
    values: { record: `#${ref}`, amount: `$${amount}`, date: `the ${day}${day % 10 === 1 && day !== 11 ? "st" : day % 10 === 2 && day !== 12 ? "nd" : day % 10 === 3 && day !== 13 ? "rd" : "th"}` },
    line: `record #${ref}, $${amount}`,
  };
};

const personasOf = (envState) => [...new Map((envState?.scenarios || [])
  .filter((s) => s.persona?.name)
  .map((s) => [s.persona.name, s.persona])).values()];

/**
 * New variations of one failing scenario: a different persona, different
 * wording and different world values each, the same goal, pass condition and
 * sub-goals. `base` is the draft the failing case already makes
 * (scenarioFromFailingCase) — its link back to the run rides on every one.
 */
export const failingCaseVariations = (base, { source, env, envState, count = VARIATIONS_PER_CASE } = {}) => {
  const src = source || {};
  const groupId = src.id || base?.failingCase?.taskId || base?.id;
  const pool = personasOf(envState).filter((p) => p.name !== src.persona?.name);
  const goals = src.id ? subGoalsOfScenario(src, env, envState) : [];
  const marks = Object.fromEntries(goals.map((g) => [g.id, g.mark]));
  const steer = base?.failingCase?.runOrdinal
    ? `The caller steers back to where run ${base.failingCase.runOrdinal} went wrong and holds it there.`
    : "";
  return Array.from({ length: count }, (_, i) => {
    const key = `${base.id}::${i}`;
    const h = hash(key);
    const persona = pool.length ? pool[h % pool.length] : src.persona;
    const wording = WORDINGS[(h >>> 3) % WORDINGS.length];
    const world = worldValuesOf(key);
    return {
      id: `${base.id}::var-${i + 1}`,
      name: `${src.name || base.name}-var-${i + 1}`,
      title: `${src.title || base.title} — ${lowerFirst(wording.label)}${persona?.name ? `, ${persona.name}` : ""}`,
      summary: `Variation ${i + 1} · ${wording.label} · ${world.line}`,
      useCase: src.useCase || base.useCase,
      task: src.task || base.task,
      situation: [sentence(wording.lead), `It concerns ${world.line} on ${world.values.date}.`, steer].filter(Boolean).join(" "),
      persona,
      ...(goals.length && { subGoals: goals.map((g) => g.text), subGoalMarks: marks }),
      expected: src.expected || base.expected,
      outcome: src.outcome || base.outcome || src.expected,
      turns: src.turns || base.turns,
      critical: !!(src.critical || base.critical),
      branchCategory: src.branchCategory || base.branchCategory,
      failingCase: base.failingCase,
      batchNote: base.batchNote,
      groupId,
      variation: { of: groupId, ofName: src.name || base.name, n: i + 1, persona: persona?.name || null, wording: wording.label, world: world.values },
    };
  });
};

/**
 * Does the variation still fail on the agent version under test? Each one is
 * run three times before it is offered. A failure the current version already
 * gets past is "Already fixed" and isn't added — it would test nothing. A case
 * from an older agent version, or one that only came out flaky, is fixed more
 * often.
 */
export const variationCheck = (variation, kase, envState) => {
  const current = currentAgentVersion(envState)?.label || kase?.agentVersion || "v1";
  const moved = !!kase?.agentVersion && kase.agentVersion !== current;
  const h = hash(`${variation.id}@${current}`) % 100;
  const fixedShare = (kase?.verdict === "flaky" ? 40 : 18) + (moved ? 30 : 0);
  if (h < fixedShare) return { state: "fixed", label: "Already fixed", note: `Passes 3 of 3 on agent ${current} — not added, it would test nothing.`, agentVersion: current };
  const fails = h % 3 === 0 ? 2 : 3;
  return { state: "fails", label: `Fails on ${current}`, note: `Failed ${fails} of 3 on agent ${current} — added as a target.`, fails, of: 3, agentVersion: current };
};

/* ── Error Feed conversations → scenarios ────────────────────────────────── */

/* A production caller, as the trace has them — every value here is replaced
   before the scenario is saved. */
const CALLERS = [
  { name: "Maria Gonzalez", phone: "415-555-0182", email: "maria.gonzalez@gmail.com", account: "48213377" },
  { name: "David Okafor", phone: "(312) 555-0147", email: "d.okafor@outlook.com", account: "73310942" },
  { name: "Priya Raman", phone: "646-555-0119", email: "priya.raman@yahoo.com", account: "55018236" },
  { name: "Tom Becker", phone: "+1 503 555 0164", email: "tbecker@icloud.com", account: "90127745" },
  { name: "Aisha Khan", phone: "917-555-0133", email: "aisha.k@proton.me", account: "61842290" },
];
const callerOf = (key) => CALLERS[hash(key) % CALLERS.length];

/* Who the caller seems to be, read from how the conversation went. */
const ARCHETYPES = [
  { match: /slow|reads|code|repeat|say it again/i, archetype: "The Careful Caller", traits: ["slow to answer", "repeats themselves"] },
  { match: /interrupt|cancel|hurry|before/i, archetype: "The Impatient Caller", traits: ["impatient", "interrupts"] },
  { match: /question|amount|breakdown|price/i, archetype: "The Sceptical Caller", traits: ["corrects the agent", "well-informed"] },
  { match: /twice|again|moved|switch/i, archetype: "The Persistent Caller", traits: ["procedural", "repeats themselves"] },
];
const inferPersona = (text, key) => {
  const a = ARCHETYPES.find((x) => x.match.test(text)) || { archetype: "The Everyday Caller", traits: ["cooperative"] };
  const c = callerOf(key);
  return { name: c.name, archetype: a.archetype, traits: a.traits, inferred: true, voice: "From the call, not reused" };
};

/* The step the agent failed at, as a sub-goal — worded so it is critical. */
const FAILING_STEPS = {
  "ISS-4501": "Verify the second identity factor before confirming the refund",
  "ISS-4520": "Confirm the refund total from the tool's figures, not an estimate",
  "ISS-4478": "Verify stock against the current warehouse before quoting it",
  "ISS-4402": "Confirm the cancellation against the policy exception before promising it",
};
export const failingStepOf = (issue) => FAILING_STEPS[issue?.cluster_id]
  || `Verify the step that failed in production: ${lowerFirst(String(issue?.error?.name || "the reported failure").replace(/\.$/, ""))}`;

export const REPRO_LABEL = { reproduced: "Reproduced", "not-reproduced": "Not reproduced", inconclusive: "Inconclusive" };
const reproStateOf = (r) => (!r ? null : r.state === "reproduced" ? "reproduced" : r.state === "inconclusive" ? "inconclusive" : "not-reproduced");

/**
 * An Error Feed issue's conversations as scenarios. `drafts` are the ones the
 * issue already builds (issueScenarioDrafts); `repro` is what reproducing
 * found (reproduceOf). Each gets an inferred persona, the failing step as a
 * critical sub-goal, its reproduce state, its own scenario group (one per
 * source conversation), and its personal data replaced. Only reproduced ones
 * become targets.
 */
export const errorFeedScenarios = (drafts, issue, { repro } = {}) => (drafts || []).map((d, i) => {
  const key = `${issue?.cluster_id}:${i}`;
  const caller = callerOf(key);
  const persona = inferPersona(`${d.title} ${d.situation}`, key);
  const r = repro?.perScenario?.[d.id] || null;
  const state = reproStateOf(r);
  const step = failingStepOf(issue);
  const quote = `From the call: “Hi, it's ${caller.name} — ${caller.phone}, account ${caller.account}. Send it to ${caller.email}.”`;
  const raw = {
    ...d,
    situation: [sentence(d.situation), quote].join(" "),
    persona,
    subGoals: ["Understand what the caller is asking for", step, "Confirm the outcome with the caller"],
    groupId: d.id,
    reproduce: r ? { state, fails: r.fails, of: r.of } : null,
    target: state === "reproduced",
  };
  return scrubScenario(raw, { names: [caller.name] });
});

/* ── production conversations, word for word (Phase 2) ───────────────────── */

export const REPLAY_PHASE = 2;
export const REPLAY_NOTE = "The simulated caller says the production caller's turns word for word, with personal data replaced. It doesn't adapt to what your agent says, and the call audio is never reused.";

const STREETS = ["1452 Oak Street", "88 Harbor Avenue", "301 Pine Road", "27 Willow Lane"];

/**
 * Production conversations for this environment's agent — a handful, most
 * recent first, shaped from its own tools. Each holds the caller's turns as
 * they were said, personal data and all; nothing here is saved until it goes
 * through `replayScenarioOf`.
 */
export const productionConversationsOf = (env) => {
  if (!env) return [];
  /* What callers chase is what the tools act on — not the checks around it
     (verifying who's calling, codes, handing over). */
  const all = (env.tools || []).map((t) => t.name);
  const acting = all.filter((n) => !/^(verify|send_otp|check_otp|find_record|escalate)/.test(n));
  const tools = acting.length ? acting : all;
  const noun = (n) => String(n || "order").replace(/^[a-z]+_/, "").replace(/_/g, " ");
  const voice = env.surface === "voice";
  const day = 24 * 60 * 60 * 1000;
  const now = Date.now();
  return Array.from({ length: 12 }, (_, i) => {
    const key = `${env.id}:conv:${i}`;
    const c = callerOf(key);
    const thing = noun(tools[i % Math.max(1, tools.length)]);
    const street = STREETS[hash(key) % STREETS.length];
    const first = c.name.split(" ")[0];
    const outcome = i % 3 === 1 ? "passed" : "failed";
    const turns = [
      { role: "caller", text: `Hi, this is ${c.name}. I'm calling about my ${thing}, account ${c.account}.` },
      { role: "agent", text: `Thanks ${first}. Can you confirm the phone number on the account?` },
      { role: "caller", text: `It's ${c.phone}. You can also email me at ${c.email}.` },
      { role: "agent", text: `Got it. What would you like to do with the ${thing}?` },
      { role: "caller", text: i % 2 ? `I already asked last week and nobody called back. Just tell me where the ${thing} stands.` : `The ${thing} is wrong and I need it fixed today, before I'm charged again.` },
      { role: "agent", text: "Let me look into that for you." },
      { role: "caller", text: `And anything you send, send it to ${street}, not the old address.` },
    ];
    return {
      id: `conv_${hash(key).toString(16).slice(0, 8)}`,
      at: new Date(now - (i + 1) * day * 1.5).toISOString(),
      channel: voice ? "Phone call" : "Chat",
      durationS: 95 + (hash(key) % 240),
      outcome,
      topic: `Caller chasing their ${thing}`,
      callerName: c.name,
      turns,
    };
  });
};

/**
 * A production conversation as a scenario that replays the caller's turns
 * verbatim. Personal data is replaced before it is saved; the audio is never
 * reused. Tagged with its source and the Phase 2 mark.
 */
export const replayScenarioOf = (conv, env) => {
  const callerTurns = conv.turns.filter((t) => t.role === "caller");
  const persona = inferPersona(callerTurns.map((t) => t.text).join(" "), conv.id);
  const raw = {
    id: `replay::${conv.id}`,
    name: `replay-${conv.id.replace(/^conv_/, "")}`,
    title: `${conv.topic} — replayed word for word`,
    summary: `${conv.channel} on ${new Date(conv.at).toLocaleDateString(undefined, { month: "short", day: "numeric" })} · ${callerTurns.length} caller turns, verbatim`,
    useCase: conv.topic,
    task: `Replays a production ${conv.channel.toLowerCase()} word for word. The caller says exactly what was said; the agent has to handle it.`,
    situation: `The simulated caller says the production caller's ${callerTurns.length} turns in order, as they were said, whatever the agent replies. It opens with “${callerTurns[0]?.text || ""}”`,
    persona: { ...persona, name: conv.callerName },
    subGoals: [
      "Understand what the caller is asking for",
      "Verify who is asking, before touching any account state",
      `Confirm the outcome with the caller`,
    ],
    expected: "The agent resolves what the caller asked for without making anything up, even though the caller doesn't adapt to its replies.",
    turns: callerTurns.length * 2,
    critical: false,
    source: "production",
    groupId: `replay::${conv.id}`,
    productionReplay: {
      conversationId: conv.id,
      at: conv.at,
      channel: conv.channel,
      outcome: conv.outcome,
      verbatim: true,
      phase: REPLAY_PHASE,
      audioReused: false,
      turns: callerTurns.map((t, i) => ({ n: i + 1, text: t.text })),
    },
    env: env?.id,
  };
  return scrubScenario(raw, { names: [conv.callerName] });
};
