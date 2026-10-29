/**
 * "Needs your input".
 *
 * Deriving an environment from an agent's source gets most of the way and then
 * hits things no amount of reading can settle: a tool the sandbox cannot reach,
 * a secret nobody can invent, an objective the code does not state.
 *
 * The rule that makes this usable is the split. A **blocking** gap stops a run
 * and waits for you. A **non-blocking** gap does not — the builder takes its
 * best guess, runs anyway, and flags the guess as low confidence so a result
 * that rests on it is never quietly trusted. Nothing waits on a question that
 * did not have to be asked.
 */
import {
  agentIdOf,
  agentNeedsRuntime,
  envAgentKey,
  needsToolSafety,
  PROVIDER_LABELS,
} from "./agentRegistry";
import { replicationOf } from "./replication";
import {
  needsSafeTarget,
  tierOf,
  toolEnvOf,
  toolEnvPatch,
  unsafeLiveToolNames,
} from "./toolEnv";
import { knownToolsFor, requiredToolsOf } from "./toolFit";
import { isConnectOnly } from "./envModel";
import { envChangePatch } from "./versions";
import {
  proofKindOf,
  needsLiveConsent,
  platformTestingOf,
  numbersPinned,
  agentStatusOf,
} from "./rsiModel";

/**
 * Credentials, as a manifest rather than a question.
 *
 * Reading the source tells you *which* secrets an agent needs and where the
 * requirement came from — an `.env.example` line, a compose file, an imported
 * SDK. That is a structured fact, so it is stated as one: id, provider,
 * purpose, where it was detected, and whether it is already satisfied. The user
 * is then asked only for what is genuinely missing, and never in prose.
 *
 * The value itself never passes through here. A requirement carries a
 * reference to a platform secret, never the secret — which is what lets the
 * manifest be shown, logged and exported without redaction.
 */
export const credentialManifest = (env, envState) => {
  const surface = env?.surface;
  const provider = envState?.agent?.values?.provider;
  const resolved = envState?.gapsResolved || {};

  const items = [
    surface === "voice" && {
      id: `${provider || "livekit"}_api_key`,
      provider: provider || "livekit",
      purpose: "Connect to the agent's room and place the call",
      detectedFrom: ".env.example",
      required: true,
    },
    surface === "voice" && {
      id: "deepgram_api_key",
      provider: "deepgram",
      purpose: "Speech to text for the caller side",
      detectedFrom: "imported SDK",
      required: true,
    },
    surface === "chat" && {
      id: "agent_endpoint_token",
      provider: "customer",
      purpose: "Authenticate to the agent's chat endpoint",
      detectedFrom: "connector configuration",
      required: true,
    },
    surface === "browser" && {
      id: "sandbox_browser_token",
      provider: "future-agi",
      purpose: "Drive the headless browser in the sandbox",
      detectedFrom: "compose manifest",
      required: true,
    },
    {
      id: "model_api_key",
      provider: "vertex-ai",
      purpose: "Run the simulated caller and the judges",
      detectedFrom: "platform connection",
      required: true,
      satisfiedBy: "voice-simulation-prod",
    },
    {
      id: "observability_token",
      provider: "future-agi",
      purpose: "Post traces and recordings back to the platform",
      detectedFrom: "platform connection",
      required: false,
      satisfiedBy: "workspace default",
    },
  ].filter(Boolean);

  /* The reference each one resolves through. An agent connected with a key
     carries its own (scrubbed to a reference by the store); the rest are set
     here or on Settings. */
  const refs = envState?.credentialRefs || {};
  const agentValues = envState?.agent?.values || {};
  const providerKey = `${provider || "livekit"}_api_key`;
  const refOf = (c) =>
    refs[c.id] || (c.id === providerKey ? agentValues.apiKeyRef : null) || null;
  const heldByAgent = (c) => c.id === providerKey && !!agentValues.hasApiKey;

  return items.map((c) => {
    const ref = c.satisfiedBy ? null : refOf(c);
    return {
      ...c,
      ref,
      /* A key pasted before references existed, moved into the vault. */
      migrated:
        !!ref &&
        c.id === providerKey &&
        !refs[c.id] &&
        !!agentValues.apiKeyMigrated,
      check: envState?.secretChecks?.[c.id] || null,
      status:
        c.satisfiedBy || ref || heldByAgent(c)
          ? "configured"
          : resolved.secret
            ? "configured"
            : c.required
              ? "missing"
              : "optional",
    };
  });
};

/** A run cannot start while a required credential has no reference. */
export const missingCredentials = (env, envState) =>
  credentialManifest(env, envState).filter((c) => c.status === "missing");

/*
  The inbox (PRD v6 Story 3, AC-3.1–3.8).

  Every gap is one card: what is missing, why it matters, the default we
  would use, and what you can do about it —

    Accept default   the suggestion becomes the answer, stamped `assumed`
    Provide value    your answer, stamped `confirmed`
    Skip             optional gaps only; the scenarios that rest on it sit
                     out the run, and the coverage says so

  Some gaps are answered by changing the thing itself (connect an agent,
  confirm a safe target, add an evaluation); those have no stamp — they
  disappear when the fact underneath them changes. The builder takes its own
  best guess on the low-stakes ones as it reads, so those arrive already
  `assumed` and wait only for someone to confirm them.

  Stored on envState.gapsResolved = { [gapId]: { stamp, value, at } }. An
  older answer saved as a plain string reads as `confirmed`.
*/

export const GAP_STATUS = {
  blocking: {
    label: "Blocking",
    color: "#DC2626",
    blurb: "A run cannot start until this is answered",
  },
  optional: {
    label: "Optional",
    color: "#7A7F87",
    blurb: "A run can start — the scenarios that rest on it sit out",
  },
  assumed: {
    label: "Assumed",
    color: "#7A7F87",
    blurb: "The default stands in — confirm when you can",
  },
  confirmed: { label: "Confirmed", color: "#7A7F87", blurb: "Answered by you" },
  skipped: {
    label: "Skipped",
    color: "#7A7F87",
    blurb: "Left out — its scenarios sit out the run",
  },
  /* Older readers asked for "resolved"; it is a confirmed answer. */
  resolved: { label: "Resolved", color: "#7A7F87", blurb: "Answered by you" },
};

/* The catalogue (PRD §3.4) — what each code is about. */
export const GAP_CODES = {
  gap_reachability: { label: "Reachability", severity: "blocking" },
  gap_prod_risk: { label: "Production risk", severity: "blocking" },
  gap_endpoint_consent: { label: "Endpoint consent", severity: "blocking" },
  gap_live_consent: { label: "Live test consent", severity: "blocking" },
  gap_number_pinning: { label: "Number pinning", severity: "optional" },
  gap_tool_link: { label: "Shared state", severity: "optional" },
  gap_clone_scope: { label: "Clone scope", severity: "optional" },
  gap_agent_manifest: { label: "Agent manifest", severity: "optional" },
  gap_action_space: { label: "Action space", severity: "optional" },
  gap_modality: { label: "Modality", severity: "optional" },
  gap_tool_behavior: { label: "Tool behaviour", severity: "optional" },
  gap_tool_fidelity: { label: "Tool fidelity", severity: "optional" },
  gap_objective: { label: "Objective", severity: "optional" },
  gap_world_state: { label: "World state", severity: "optional" },
};

const CLONE = new Set(["vapi", "bland"]);

/** The page that holds the agent's connection — where consent and keys live. */
export const agentConnectionHref = (env, envState) => {
  const key = envAgentKey(envState?.agent, env?.id);
  return key
    ? `/dashboard/agents/connected/${agentIdOf(key)}?tab=connections`
    : null;
};

/* How this environment's agent is copied. A hosted assistant on a provider
   that can't clone runs from its exported definition even when it was read
   from a pasted prompt. */
const copyModeOf = (env, envState) => {
  const agent = envState?.agent;
  if (!agent) return null;
  /* The RSI PRD tests hosted agents live: a Vapi test copy, a pinned Retell
     draft — replication.js says which; no forced "mocked" copy any more. */
  return replicationOf(agent, env);
};

const live = (envState) =>
  (envState?.scenarios || []).filter((r) => !r.retired && !r.archived);
/** Scenarios that rest on these tools — any of them, or with `every`, all of them. */
const scenariosUsing = (env, envState, names, { every = false } = {}) => {
  const known = env?.tools || [];
  return live(envState)
    .filter((r) => {
      const req = requiredToolsOf(r, known);
      return every
        ? names.every((n) => req.includes(n))
        : req.some((n) => names.includes(n));
    })
    .map((r) => r.id);
};

const nounOf = (name) =>
  String(name).replace(
    /^(get|read|check|list|search|fetch|verify|lookup|find|book|create|reschedule|update|cancel|delete|send|post|upsert|issue|generate|charge|refund|transfer|escalate|submit)_/i,
    "",
  );
const READ_VERB = /^(get|read|check|list|search|fetch|verify|lookup|find)_/i;
const WRITE_VERB =
  /^(book|create|reschedule|update|cancel|delete|send|post|upsert|issue|generate|charge|refund|transfer|submit)_/i;

/**
 * Two tools that may touch the same records under different names — a write
 * whose description names what a read looks up ("Refund up to the order
 * total" → lookup_order). Same-noun pairs (cancel_order, lookup_order) are
 * obviously one store and not asked about. The answer is kept on
 * envState.toolLinks; the gap stays listed with its stamp.
 */
const toolLinkCandidate = (env) => {
  const tools = env?.tools || [];
  for (let i = 0; i < tools.length; i += 1) {
    const w = tools[i];
    if (!WRITE_VERB.test(w.name)) continue;
    const reads = tools.filter(
      (t) => READ_VERB.test(t.name) && nounOf(t.name) !== nounOf(w.name),
    );
    const r = reads.find((t) =>
      new RegExp(`\\b${nounOf(t.name).replace(/_/g, " ")}\\b`, "i").test(
        w.desc || "",
      ),
    );
    if (r)
      return { write: w, read: r, noun: nounOf(r.name).replace(/_/g, " ") };
  }
  return null;
};

const LIVE_HINT =
  /\b(live|real[- ]time|tracking|current|availability|price|rate)\b/i;

/**
 * Derived from the environment so the list is never generic: the tool that got
 * stubbed is a tool this agent actually declared.
 */
export const setupGaps = (env, envState) => {
  if (!env) return [];
  const tools = env.tools || [];
  const connectOnly = isConnectOnly(env);
  const writeTool =
    tools.find((t) => /refund|issue|charge|send|delete/i.test(t.name)) ||
    tools.find((t) => WRITE_VERB.test(t.name));
  const rules = env.rules || [];
  const promptOnly = Math.max(1, Math.round(rules.length * 0.6));
  const agent = envState?.agent || null;
  const copy = copyModeOf(env, envState);
  const href = agentConnectionHref(env, envState);
  const dial = connectOnly ? [] : toolEnvOf(env, envState);
  const surface = env.surface;

  const all = [
    /* ── blocking ── */
    !agent && {
      id: "no-agent",
      code: "gap_reachability",
      severity: "blocking",
      fixedBy: "action",
      area: "Agent",
      title: "Connect an agent",
      why: "A simulation drives the agent on every turn, and this environment doesn't have one yet. Connect it from Overview; the world, scenarios and evaluations stay as they are.",
      ask: { type: "link", label: "Open the Overview tab", to: "overview" },
    },
    agentNeedsRuntime(agent) && {
      id: "no-runtime",
      code: "gap_reachability",
      severity: "blocking",
      fixedBy: "action",
      area: "Agent",
      title: "Add where the agent runs",
      why: "We read this agent from its code, but a simulation calls the agent on every turn — so it needs the URL it's deployed at. Add it on the Overview tab; it applies wherever this agent is tested.",
      ask: { type: "link", label: "Open the Overview tab", to: "overview" },
    },
    copy?.needsConsent && {
      id: "gap_endpoint_consent",
      code: "gap_endpoint_consent",
      severity: "blocking",
      fixedBy: "action",
      area: "Agent",
      title: "Let us call your live agent for tests",
      why: "We only have a URL for this agent, so it can't be copied. Simulator traffic drives your real agent and its real tools run — someone has to agree to that before the first run. Acknowledge it on the agent's connection; adding the webhook also lets us see its tool calls.",
      suggested: null,
      ask: href
        ? { type: "link", label: "Open the agent's connection", href }
        : { type: "link", label: "Open the Overview tab", to: "overview" },
    },
    /* A live environment: the agent runs in the customer's account and its
       tools reach their real systems, so a person agrees before the first
       run (RSI PRD). The endpoint consent above already covers an agent we
       only have a URL for. */
    agent &&
      !copy?.needsConsent &&
      proofKindOf(agent).kind === "live" &&
      needsLiveConsent(agent) && {
        id: "gap_live_consent",
        code: "gap_live_consent",
        severity: "blocking",
        fixedBy: "action",
        area: "Agent",
        title: "Let us call your agent for tests",
        why: "Tests call your real agent, so anything it does — like issuing a refund — really happens. Approve this once before the first run, and say whether its tools are safe to call or stubbed in tests.",
        suggested: null,
        ask: href
          ? { type: "link", label: "Open the agent's connections", href }
          : { type: "link", label: "Open the Overview tab", to: "overview" },
      },
    ...dial.filter(needsSafeTarget).map((t) => ({
      id: `gap_prod_risk:${t.name}`,
      code: "gap_prod_risk",
      severity: "blocking",
      fixedBy: "action",
      area: "Tools",
      scope: { tool: t.name },
      title: `${t.name} is set to Live with no safe target`,
      why: `Live calls the real ${t.name}. Nothing records a sandbox for it, so a run would reach whatever it is pointed at — possibly production. Confirm a safe target, or take it back to ${tierOf(t.defaultTier).word}.`,
      suggested: `Take it back to ${tierOf(t.defaultTier).word}`,
      ask: {
        type: "safeTarget",
        label: "Sandbox URL",
        placeholder: "https://sandbox.example.com",
      },
      defaultTier: t.defaultTier,
    })),
    /* Advisory, not blocking (PRD v7): sub-goals decide pass or fail, so a
       run without evaluations still has a verdict — it just isn't scored on
       anything else. */
    !envState?.evals?.length && {
      id: "no-evals",
      code: "gap_objective",
      severity: "optional",
      fixedBy: "action",
      area: "Grading",
      title: "No evaluations added",
      why: "Runs can start — sub-goals decide pass or fail. Evaluations score each conversation on top (tone, policy, data handling), and one marked required can fail it. Suggested ones are ready on the Evaluations tab.",
      ask: { type: "link", label: "Open the Evaluations tab", to: "evals" },
    },

    /* ── optional, waiting on you ── */
    (() => {
      const pair = connectOnly ? null : toolLinkCandidate(env);
      if (!pair) return null;
      return {
        id: `gap_tool_link:${pair.write.name}+${pair.read.name}`,
        code: "gap_tool_link",
        severity: "optional",
        area: "Tools",
        scope: { tools: [pair.write.name, pair.read.name] },
        title: `Do ${pair.write.name} and ${pair.read.name} share a store?`,
        why: `${pair.write.name} works on the ${pair.noun} that ${pair.read.name} looks up, but nothing in the source says they read the same records. If they do, the world keeps them consistent — what one writes, the other sees within the run. If not, each answers from its own data.`,
        suggested: "Yes — one store, kept consistent across both tools",
        options: [
          "Yes — one store, kept consistent across both tools",
          "No — separate systems",
        ],
        coverage: (() => {
          const both = scenariosUsing(
            env,
            envState,
            [pair.write.name, pair.read.name],
            { every: true },
          );
          return both.length
            ? both
            : scenariosUsing(env, envState, [pair.write.name]);
        })(),
        coverageWhat: `scenarios that call both`,
      };
    })(),
    ...dial
      /* Still listed once answered, so the answer stays visible and undoable. */
      .filter(
        (t) =>
          envState?.gapsResolved?.[`gap_tool_fidelity:${t.name}`] ||
          (t.tier === "replay" &&
            !t.confirmed &&
            (t.effect === "fresh" ||
              LIVE_HINT.test(
                tools.find((x) => x.name === t.name)?.desc || "",
              ))),
      )
      .slice(0, 2)
      .map((t) => {
        const tool = tools.find((x) => x.name === t.name);
        return {
          id: `gap_tool_fidelity:${t.name}`,
          code: "gap_tool_fidelity",
          severity: "optional",
          area: "Tools",
          scope: { tool: t.name },
          title: `${t.name} replays one recorded answer`,
          why: `${tool?.desc || t.name} — but Replay answers with a response captured once, so a scenario that needs it to change (a parcel moving, a slot filling) sees the same answer every time. Stateful answers from the world's data and changes as the run does.`,
          suggested: "Keep Replay — deterministic, may be stale",
          options: [
            "Keep Replay — deterministic, may be stale",
            "Switch to Stateful",
          ],
          coverage: scenariosUsing(env, envState, [t.name]),
          coverageWhat: `scenarios that call ${t.name}`,
        };
      }),
    /* Only a key without create scope on a provider that can make test
       copies is a gap: tests fall back to settings overridden for one call. */
    copy?.mode === "exported_mocked" &&
      CLONE.has(agent?.values?.provider) &&
      (() => {
        const provider = agent?.values?.provider;
        const name = PROVIDER_LABELS[provider] || "This provider";
        const scoped = true;
        return {
          id: "gap_clone_scope",
          code: "gap_clone_scope",
          severity: "optional",
          area: "Agent",
          title: `The ${name} key can't create a test copy`,
          why: "The key on file reads the assistant but can't create one, so tests override its settings for one call instead — your live assistant is never written to, and its tools still reach your real systems (Live). Re-issue the key with create scope for a test copy.",
          suggested: "Test with settings overridden for one call",
          options: [
            "Test with settings overridden for one call",
            "I'll re-issue the key with create scope",
          ],
          ask: href
            ? {
                type: "link",
                label: scoped
                  ? "Re-issue the key"
                  : "Open the agent's connection",
                href,
              }
            : null,
        };
      })(),

    /* Retell: a new draft becomes the newest version, so a number that
       follows the newest version would take real calls on it. Until the
       numbers are pinned, tests override settings for one call. */
    agent &&
      platformTestingOf(agent)?.needsPinnedNumbers &&
      !numbersPinned(agent) && {
        id: "gap_number_pinning",
        code: "gap_number_pinning",
        severity: "optional",
        area: "Agent",
        title: "Your Retell numbers aren't pinned to a version",
        why: "A new Retell draft becomes the newest version, and a live number that follows the newest version would start taking real calls on it. Until your numbers are pinned to a released version, tests use settings overridden for one call instead of a draft.",
        suggested: "Test with settings overridden for one call",
        options: ["Test with settings overridden for one call"],
        ask: href
          ? { type: "link", label: "Open the agent's connections", href }
          : null,
      },

    /* ── the builder's own guesses, already assumed ── */
    envState?.agent?.seeded && {
      id: "template-agent",
      code: "gap_reachability",
      severity: "optional",
      auto: true,
      area: "Agent",
      title: "Runs test the template's agent",
      confidence: "low",
      why: "This environment came from a template, so the agent under test is the template's baseline — a placeholder. Runs work, but they tell you how the template does, not your agent. Connect yours from Overview.",
      suggested: "Runs test the template's baseline agent.",
      ask: { type: "link", label: "Open the Overview tab", to: "overview" },
    },
    tools.length > 0 && {
      id: "manifest",
      code: "gap_agent_manifest",
      severity: "optional",
      auto: true,
      area: "Tools",
      title: `${tools.length} tools read, argument types inferred for 2`,
      confidence: "medium",
      why: "Most arguments came back with exact names and permitted values. Two are untyped in the source, so we inferred them from how they are used at the call sites. Worth a glance — an inferred type that is wrong shows up as a scenario the agent cannot pass.",
      suggested: "Inferred from call sites.",
      ask: { type: "link", label: "Review the tools", to: "world" },
    },
    !connectOnly &&
      writeTool && {
        id: "stub",
        code: "gap_tool_behavior",
        severity: "optional",
        auto: true,
        area: "Tools",
        scope: { tool: writeTool.name },
        title: `How ${writeTool.name} fails is inferred`,
        confidence: "low",
        why: `${writeTool.desc || writeTool.name}. The source shows how it succeeds but not how it fails, so the world's handler fails the way the call sites expect it to: an unknown id, and a value over its limit.`,
        suggested: "Errors on an unknown id; rejects a value over its limit",
        options: [
          "Errors on an unknown id; rejects a value over its limit",
          "Always succeeds",
        ],
        input: { label: "Or describe how it fails" },
      },
    surface === "voice" && {
      id: "gap_modality",
      code: "gap_modality",
      severity: "optional",
      auto: true,
      area: "Contract",
      title: "Caller audio quality",
      why: "The source says the agent takes phone calls, not what the line sounds like. Background noise changes how often speech-to-text mishears an order number.",
      suggested: "Clean line; 1 call in 5 with background noise",
      options: [
        "Clean line on every call",
        "Clean line; 1 call in 5 with background noise",
        "Background noise on every call",
      ],
    },
    (surface === "browser" || surface === "computer") && {
      id: "gap_action_space",
      code: "gap_action_space",
      severity: "optional",
      auto: true,
      area: "Contract",
      title: "Where the agent may navigate",
      why: "A browser agent can go anywhere a link leads. Nothing in the source limits it, so the sandbox allows only the seeded app's domain and blocks the rest.",
      suggested: "Only the seeded app's domain",
      options: [
        "Only the seeded app's domain",
        "Any site — every visit logged",
      ],
    },
    rules.length > 0 && {
      id: "promptonly",
      code: "gap_objective",
      severity: "optional",
      auto: true,
      area: "Grading",
      title: `${promptOnly} of ${rules.length} rules are prompt-only`,
      confidence: "medium",
      why: "These rules are stated in the prompt but not enforced in code, so the world cannot prevent a breach — it can only notice one. They are graded rather than guaranteed, which is a weaker claim and worth knowing before you read a pass rate.",
      suggested: "Graded by judge, not enforced by the environment.",
      ask: { type: "link", label: "See which rules", to: "overview" },
    },
    !connectOnly &&
      env.builtFrom &&
      (env.seed?.tables || []).length > 0 && {
        id: "gap_world_state",
        code: "gap_world_state",
        severity: "optional",
        auto: true,
        area: "World",
        title: `${(env.seed.tables || []).reduce((a, t) => a + (t.rows || 0), 0).toLocaleString()} seed rows generated`,
        why: "No fixtures were found in the source, so the world's rows were generated from the schema and the prompt's examples. Point us at real fixtures if a scenario needs a specific record.",
        suggested: "Generated from the schema and the prompt's examples",
        input: {
          label: "Fixtures path in the repository",
          placeholder: "db/fixtures/",
        },
      },
  ].filter(Boolean);

  const resolved = envState?.gapsResolved || {};
  return all.map((g) => {
    const saved = resolved[g.id];
    const answer =
      typeof saved === "string" ? { stamp: "confirmed", value: saved } : saved;
    /* Legacy readers read `assumed` as the guess. */
    const base = {
      ...g,
      assumed: g.suggested || null,
      answered: null,
      stamp: null,
    };
    if (answer && g.fixedBy !== "action") {
      return {
        ...base,
        status: answer.stamp === "skipped" ? "skipped" : answer.stamp,
        stamp: answer.stamp,
        answered: answer.value || null,
        by: "you",
        at: answer.at || null,
      };
    }
    if (g.auto)
      return {
        ...base,
        status: "assumed",
        stamp: "assumed",
        answered: g.suggested,
        by: "builder",
      };
    return { ...base, status: g.severity };
  });
};

/** Blocking first, then what waits on you, then what is settled. */
const ORDER = {
  blocking: 0,
  optional: 1,
  assumed: 2,
  skipped: 3,
  confirmed: 4,
  resolved: 4,
};
export const sortGaps = (gaps) =>
  [...gaps].sort((a, b) => (ORDER[a.status] ?? 5) - (ORDER[b.status] ?? 5));

/**
 * How much of the suite a run covers with what is still open. A scenario that
 * rests on an open or skipped optional gap sits the run out, and is
 * attributed to that gap.
 */
export const gapCoverage = (env, envState, gaps = setupGaps(env, envState)) => {
  const rows = live(envState);
  const open = gaps.filter(
    (g) =>
      (g.status === "optional" || g.status === "skipped") && g.coverage?.length,
  );
  const out = new Map();
  open.forEach((g) =>
    g.coverage.forEach((id) => {
      if (!out.has(id)) out.set(id, g.code);
    }),
  );
  const total = rows.length;
  const affected = rows.filter((r) => out.has(r.id)).length;
  return {
    total,
    affected,
    pct: total ? Math.round(((total - affected) / total) * 100) : 100,
    skippedIds: [...out.keys()],
    /* Which gap each sitting-out scenario is attributed to. */
    attribution: Object.fromEntries(out),
    byGap: open.map((g) => ({
      id: g.id,
      code: g.code,
      title: g.title,
      n: g.coverage.filter((id) => out.get(id) === g.code).length,
    })),
  };
};

/** "2 blocking · 5 optional · 9 resolved" — the meter. */
export const gapMeter = (gaps) => {
  const blocking = gaps.filter((g) => g.status === "blocking").length;
  const optional = gaps.filter((g) => g.status === "optional").length;
  const resolved = gaps.length - blocking - optional;
  return {
    blocking,
    optional,
    resolved,
    total: gaps.length,
    line: `${blocking} blocking · ${optional} optional · ${resolved} resolved`,
  };
};

/**
 * The patch for answering a gap. `stamp` is "assumed" (Accept default),
 * "confirmed" (Provide value) or "skipped". An answer that changes how a tool
 * behaves is also a world edit.
 */
export const answerGapPatch = (gap, stamp, value, envState) => {
  const resolvedNext = {
    ...(envState?.gapsResolved || {}),
    [gap.id]: {
      stamp,
      ...(value != null && { value }),
      at: new Date().toISOString(),
    },
  };
  const patch = { gapsResolved: resolvedNext };
  if (stamp === "skipped") return patch;
  if (gap.code === "gap_tool_fidelity" && /stateful/i.test(value || "")) {
    const tool = gap.scope.tool;
    return {
      ...toolEnvPatch(
        envState,
        tool,
        { tier: "stateful" },
        `${tool} set to Stateful`,
      ),
      ...patch,
    };
  }
  if (gap.code === "gap_tool_link") {
    const shared = /^yes/i.test(value || "");
    const [w, r] = gap.scope.tools;
    const links = (envState?.toolLinks || []).filter(
      (l) => l.tools.join("+") !== `${w}+${r}`,
    );
    return {
      toolLinks: [...links, { tools: [w, r], shared }],
      ...envChangePatch(
        envState,
        "toolbehaviour",
        `${w} and ${r} ${shared ? "share a store" : "keep separate data"}`,
      ),
      ...patch,
    };
  }
  if (
    gap.code === "gap_tool_behavior" &&
    gap.scope?.tool &&
    stamp === "confirmed"
  ) {
    return {
      ...envChangePatch(
        envState,
        "toolbehaviour",
        `${gap.scope.tool} failure cases: ${value}`,
      ),
      ...patch,
    };
  }
  return patch;
};

/** Undo an answer — the gap opens again (a builder's guess goes back to assumed). */
export const reopenGapPatch = (gap, envState) => {
  const rest = { ...(envState?.gapsResolved || {}) };
  delete rest[gap.id];
  return { gapsResolved: rest };
};

/**
 * Whether a run can start, and if not the one thing in the way — the same
 * condition as the store's `canRun`, readable for any environment (a table
 * row has no hook). `step` is the workspace tab that answers it; `href`,
 * when set, is the agent page that does instead — consent and connection
 * live on the agent, not the environment.
 */
/** Runs answer every tool from the environment's world — nothing real is reached. */
export const runsSealed = (envState) =>
  !!envState?.twinBacking?.services?.length ||
  proofKindOf(envState?.agent).kind === "sealed";

export const runReadiness = (envState, env = null) => {
  const agentHref = agentConnectionHref(env, envState);
  if (!envState?.agent)
    return {
      ready: false,
      step: "overview",
      reason: "Connect an agent on Overview",
    };
  if (agentNeedsRuntime(envState.agent))
    return {
      ready: false,
      step: "overview",
      reason: "Add where the agent runs on Overview",
    };
  /* What we read from a newly connected code agent's code is confirmed on
     "Check what we found" (RSI PRD Flow 1). A sealed run answers every tool
     from the world, so it runs on what we read; a live run waits — the
     review is where tools that must never run for real are marked. */
  if (needsToolSafety(envState.agent) && !runsSealed(envState))
    return {
      ready: false,
      step: "overview",
      href: agentHref?.replace("tab=connections", "tab=overview"),
      foundCheck: true,
      reason: "Mark the tools that must never run for real before a live run",
    };
  /* A disconnected or archived agent can't be run — the agent page says why. */
  const status =
    typeof agentStatusOf === "function" ? agentStatusOf(envState.agent) : null;
  if (status && status.status !== "connected")
    return {
      ready: false,
      step: "overview",
      href: agentHref,
      gap: "gap_agent_status",
      reason: `${status.label || "Not connected"}${status.reason ? ` — ${status.reason}` : ""}. Open the agent's Connections tab.`,
    };
  if (replicationOf(envState.agent)?.needsConsent)
    return {
      ready: false,
      step: "overview",
      href: agentHref,
      gap: "gap_endpoint_consent",
      reason:
        "Let us call your live agent for tests first — it's reached at a URL we can't copy, so a run drives the real one",
    };
  /* Its last connection check found a tool still reaching production: the
     controlled cases can't run until it's pointed at the test world or
     observed only (roadmap connect.md, Week 1). Only the cases that need
     it are blocked (release.md): while others don't, the run plan offers
     them as a reviewed subset — this holds the run only when none can. */
  const prodTools =
    envState.agent?.values?.connectionTest?.productionTools || [];
  const known = env ? knownToolsFor(env, envState) : [];
  const unaffected = (envState.scenarios || []).filter(
    (s) =>
      !s.archived &&
      !s.retired &&
      !requiredToolsOf(s, known).some((t) => prodTools.includes(t)),
  ).length;
  if (prodTools.length && (!env || !unaffected))
    return {
      ready: false,
      step: "overview",
      href: agentHref,
      gap: "gap_production_tool",
      reason: `${prodTools[0]} still reaches production — point it at the test world or observe it only, on the agent's Connections tab`,
    };
  /* A Live agent runs in the customer's account with real tools — every way
     to start a run (header, table row, Runs tab, run page) waits for consent. */
  if (needsLiveConsent(envState.agent))
    return {
      ready: false,
      step: "overview",
      href: agentHref,
      gap: "gap_live_consent",
      reason:
        "Let us call your agent for tests first — on the agent's Connections tab",
    };
  const unsafe = unsafeLiveToolNames(envState);
  if (unsafe.length)
    return {
      ready: false,
      step: "world",
      gap: "gap_prod_risk",
      reason: `${unsafe[0]} is set to Live with no safe target — confirm one or lower its tier on the World tab`,
    };
  if (!(envState.scenarios || []).length)
    return {
      ready: false,
      step: "scenarios",
      reason: "Add scenarios on the Scenarios tab",
    };
  return { ready: true, step: null, reason: "" };
};

export const gapCounts = (gaps) => ({
  blocking: gaps.filter((g) => g.status === "blocking").length,
  optional: gaps.filter((g) => g.status === "optional").length,
  assumed: gaps.filter((g) => g.status === "assumed").length,
  skipped: gaps.filter((g) => g.status === "skipped").length,
  resolved: gaps.filter(
    (g) => g.status === "confirmed" || g.status === "resolved",
  ).length,
});
