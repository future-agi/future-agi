/*
  The workspace skills library (clarified plan, skills.md): reusable skills
  kept in one versioned place, attached to agents, tested before they're
  promoted. Skills found in an agent's repository can be added here; those
  repo copies still change only through a pull request.

  Saved in workspaceSettings:
    skillLibrary:     [{ id, name, description, owner, versions: [{ v, at, by, note, text }],
                         tags: { production: v, staging: v }, tests: { [v]: { at, loaded, result } },
                         promotions: [{ v, at, by, reason, tested }] }]
    skillAttachments: { [agentKey]: [{ skillId, ref }] }   — ref is "production", "staging" or a version
  With nothing saved, the library starts from a small seed.
*/
import { currentUser } from "./scenarioProvenance";
import { agentKeyOf, definitionOf, sourceKindOf } from "./agentRegistry";
import { skillRuntimeOf, skillSetOf } from "./skills";
import { CODING_AGENTS } from "./codingAgents";

const daysAgo = (d) => new Date(Date.now() - d * 864e5).toISOString();
const md = (name, when, body) =>
  `---\nname: ${name}\ndescription: ${when}\n---\n\n${body}\n`;

const SEED = () => [
  {
    id: "lib-escalate-to-human",
    name: "escalate-to-human",
    description:
      "Hand the caller to a person when they ask, or when the agent can't finish safely.",
    owner: "Support platform",
    versions: [
      {
        v: 1,
        at: daysAgo(60),
        by: "Priya Nair",
        note: "First version",
        text: md(
          "escalate-to-human",
          "Use when the caller asks for a person.",
          "Offer a transfer and say how long the wait is.",
        ),
      },
      {
        v: 2,
        at: daysAgo(21),
        by: "Priya Nair",
        note: "Also when the agent can't finish safely",
        text: md(
          "escalate-to-human",
          "Use when the caller asks for a person, or the agent can't finish safely.",
          "Offer a transfer and say how long the wait is.\nSummarise the case for the person taking over.",
        ),
        files: [
          {
            path: "references/handover.md",
            text: "# Handover summary\n\n- Who is calling and why\n- What was tried\n- What the caller was promised",
          },
        ],
      },
    ],
    tags: { production: 1, staging: 2 },
    tests: {
      1: { at: daysAgo(55), loaded: "observed", result: "helps" },
      2: seedReport("lib-escalate-to-human", 2, 1, daysAgo(20)),
    },
    promotions: [
      { v: 1, at: daysAgo(55), by: "Priya Nair", reason: null, tested: true },
    ],
  },
  {
    id: "lib-refund-policy",
    name: "refund-policy",
    description:
      "Apply the refund window and the approval cap before promising money back.",
    owner: "Payments",
    versions: [
      {
        v: 1,
        at: daysAgo(30),
        by: "Dan Osei",
        note: "First version",
        text: md(
          "refund-policy",
          "Use before offering or issuing a refund.",
          "Check the order date against the 30-day window.\nRefunds above $200 need a supervisor.",
        ),
        files: [
          {
            path: "scripts/check_window.py",
            text: "from datetime import date\n\ndef in_window(ordered: date, today: date) -> bool:\n    return (today - ordered).days <= 30\n",
          },
          {
            path: "references/policy.md",
            text: "# Refund policy\n\n- 30 days from delivery\n- Above $200: supervisor approval\n- Final sale items: no refund",
          },
        ],
      },
    ],
    tags: { production: 1 },
    tests: { 1: seedReport("lib-refund-policy", 1, 0, daysAgo(29)) },
    promotions: [
      { v: 1, at: daysAgo(29), by: "Dan Osei", reason: null, tested: true },
    ],
  },
  {
    id: "lib-identity-check",
    name: "identity-check",
    description:
      "Verify the caller before reading out or changing account details.",
    owner: "Trust & safety",
    versions: [
      {
        v: 1,
        at: daysAgo(4),
        by: "Mei Tan",
        note: "First version",
        text: md(
          "identity-check",
          "Use before sharing or changing anything on an account.",
          "Ask for the order number and the email on file.\nNever read a full card number back.",
        ),
      },
    ],
    tags: {},
    tests: {},
    promotions: [],
  },
];

export const libraryOf = (settings) => settings?.skillLibrary || SEED();
export const attachmentsOf = (settings) => settings?.skillAttachments || {};
export const latestV = (skill) =>
  skill.versions[skill.versions.length - 1]?.v || 1;
export const resolveRef = (skill, ref) =>
  typeof ref === "number" ? ref : skill.tags?.[ref] ?? latestV(skill);
export const refLabel = (skill, ref) =>
  typeof ref === "number" ? `v${ref}` : `${ref} (v${resolveRef(skill, ref)})`;

/** Agents a library skill is attached to: [{ agentKey, ref }]. */
export const usedByOf = (skillId, settings) =>
  Object.entries(attachmentsOf(settings)).flatMap(([agentKey, list]) =>
    (list || [])
      .filter((a) => a.skillId === skillId)
      .map((a) => ({ agentKey, ref: a.ref })),
  );

/**
  How a skill change lands on an agent (skills.md SK-2): a skill attached
  from the library saves as that skill's next version; any other skill lives
  in the agent's repository and changes through the pull request.
*/
export const skillLandsOf = (settings, agentKey, skillId) => {
  const attached = new Set(
    (attachmentsOf(settings)[agentKey] || []).map((a) => a.skillId),
  );
  const lib = libraryOf(settings).find(
    (x) => x.name === skillId && attached.has(x.id),
  );
  return lib
    ? { inLibrary: true, line: `Saves as ${lib.name} v${latestV(lib) + 1}` }
    : {
        inLibrary: false,
        line: "In your repo — changes through the pull request",
      };
};

const INSTRUCTIONS_RE = /(^|\/)(AGENTS|CLAUDE)\.md$/i;
/**
  An agent's always-on instructions (skills.md, Agent context): the file, the
  skills it links, and where it lives. A git agent keeps it in its repository
  (read-only here); code with no repository to read (an upload or an adapter)
  keeps it in the library, versioned like a skill. Saved choices in
  workspaceSettings.agentContext[agentKey] = { location, v } win.
  Null for an agent with no code to hold one.
*/
export const agentContextOf = (agent, settings) => {
  const kind = sourceKindOf(agent);
  if (!["repo", "upload", "framework"].includes(kind)) return null;
  const v = agent?.values || {};
  const def = definitionOf(agent);
  const named = [agent?.prompts, v.prompts, def.prompts]
    .flat()
    .filter((x) => typeof x === "string")
    .find((x) => INSTRUCTIONS_RE.test(x));
  const file = named
    ? named.split("/").pop()
    : CODING_AGENTS[v.codingAgent]?.instructions ||
      (v.framework === "claude-agent-sdk" ? "CLAUDE.md" : "AGENTS.md");
  const saved = settings?.agentContext?.[agent.key];
  const location = saved?.location || (kind === "repo" ? "repo" : "library");
  const attached = new Set(
    (attachmentsOf(settings)[agent.key] || []).map((a) => a.skillId),
  );
  const skills = [
    ...skillSetOf(agent, settings).skills.map((x) => x.id),
    ...libraryOf(settings)
      .filter((x) => attached.has(x.id))
      .map((x) => x.name),
  ].filter((n, i, all) => all.indexOf(n) === i);
  return {
    agentKey: agent.key,
    agentId: agent.id,
    agentName: agent.name,
    file,
    skills,
    location,
    v: location === "library" ? saved?.v || 1 : null,
    /* skills.md: agent contexts are versioned items with tags agents pull;
       a repo-resident one is versioned by the agent's own commits. */
    tag: location === "library" ? saved?.tag || "production" : null,
    agentVersion: agent.production || agent.latest || null,
  };
};

/** What a run's agent loaded: "Skills · refund-policy v3 · Observed" (or Unknown). */
/* The version a tag pointed at when the run started — runs record the
   version they used, not whatever production points at today. */
const refAt = (skill, ref, at) => {
  if (typeof ref === "number" || ref !== "production" || !at)
    return resolveRef(skill, ref);
  const t = new Date(at).getTime();
  const before = (skill.promotions || []).filter(
    (p) => new Date(p.at).getTime() <= t,
  );
  return before.length ? before[before.length - 1].v : resolveRef(skill, ref);
};

export const runSkillsLineOf = (settings, agent, run = null) => {
  const startedAt = run?.startedAt || run?.finishedAt || null;
  /* Only skills attached before the run started were loaded by it. */
  const list = (attachmentsOf(settings)[agentKeyOf(agent)] || []).filter(
    (a) => !a.at || !startedAt || new Date(a.at) <= new Date(startedAt),
  );
  const library = libraryOf(settings);
  const named = list
    .map((a) => {
      const sk = library.find((x) => x.id === a.skillId);
      return sk ? `${sk.name} v${refAt(sk, a.ref, startedAt)}` : null;
    })
    .filter(Boolean);
  if (!named.length) return null;
  const shown = named.slice(0, 2).join(", ");
  const more = named.length > 2 ? ` +${named.length - 2}` : "";
  const state = skillRuntimeOf(agent).observed ? "Observed" : "Unknown";
  return `Skills · ${shown}${more} · ${state}`;
};

const withSkill = (settings, id, fn) =>
  libraryOf(settings).map((s) => (s.id === id ? fn(s) : s));

/** A new skill, version 1, untested and untagged. */
export const createSkill = (
  settings,
  { name, description, text, files = [] },
) => {
  const slug = String(name || "skill")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "");
  const skill = {
    id: `lib-${slug}-${Date.now().toString(36)}`,
    name: slug,
    description: description || "",
    owner: currentUser()?.name || "You",
    versions: [
      {
        v: 1,
        at: new Date().toISOString(),
        by: currentUser()?.name || "You",
        note: "First version",
        text: text || md(slug, description || "", ""),
        /* skills.md: a SKILL.md with optional supporting files. */
        ...(files.length && { files }),
      },
    ],
    tags: {},
    tests: {},
    promotions: [],
  };
  return { skillLibrary: [skill, ...libraryOf(settings)], skill };
};

/** A skill found in an agent's repository, added to the library as version 1. */
export const addFromRepo = (settings, repoSkill, agentName) => {
  const text =
    repoSkill.versions?.[repoSkill.versions.length - 1]?.files?.[0]?.text || "";
  const description = (text.match(/description:\s*(.*)/) || [])[1] || "";
  const out = createSkill(settings, { name: repoSkill.id, description, text });
  out.skill.versions[0].note = `Added from ${agentName}'s repository`;
  return out;
};

/** A new version: nothing changes in place, tags stay where they were. */
export const newVersion = (settings, id, { text, note, files }) => ({
  skillLibrary: withSkill(settings, id, (s) => ({
    ...s,
    versions: [
      ...s.versions,
      {
        v: latestV(s) + 1,
        at: new Date().toISOString(),
        by: currentUser()?.name || "You",
        note: note || "Edited",
        text,
        ...(files && { files }),
      },
    ],
  })),
});

/*
  Scenarios a skill is about (skills.md: no relevant scenarios → suggest
  adding some, never report a pass on unrelated ones). Matched on the
  skill's own words in the scenario's title and brief.
*/
const STOP = new Set([
  "before",
  "should",
  "about",
  "their",
  "anything",
  "agent",
  "caller",
  "person",
  "where",
  "which",
  "there",
]);
export const relevantScenariosOf = (skill, scenarios) => {
  const keys = [
    ...new Set(
      (
        `${skill.name.replace(/-/g, " ")} ${skill.description || ""}`
          .toLowerCase()
          .match(/[a-z]{5,}/g) || []
      )
        .filter((w) => !STOP.has(w))
        .map((w) => w.slice(0, 6)),
    ),
  ];
  return (scenarios || [])
    .filter((x) => !x.archived && !x.retired && !x.proposed)
    .filter((x) => {
      const text =
        `${x.title || ""} ${x.task || ""} ${x.situation || ""}`.toLowerCase();
      return keys.some((k) => text.includes(k));
    });
};

const hash = (str) => {
  let h = 2166136261;
  for (let i = 0; i < str.length; i += 1)
    h = Math.imul(h ^ str.charCodeAt(i), 16777619);
  return h >>> 0;
};

/**
 * A test of one version in an environment, against the version the agent
 * loads now, on the same scenarios: did the skill load (Observed, when the
 * runtime reports each skill it loads; Unknown otherwise) and did it help.
 */
export const recordTest = (
  settings,
  id,
  v,
  { observed, agentKey, envId, envName, against, scenarios },
) => {
  const h = hash(`${id}:${v}:${envId}:${against}`);
  const passedCurrent = Math.max(
    0,
    Math.round(scenarios * (0.55 + (h % 25) / 100)),
  );
  const passedNew = Math.min(
    scenarios,
    Math.max(0, passedCurrent + ((h >> 5) % 4) - 1),
  );
  const result =
    passedNew === passedCurrent
      ? "same"
      : passedNew > passedCurrent
        ? "helps"
        : "worse";
  return {
    skillLibrary: withSkill(settings, id, (s) => ({
      ...s,
      tests: {
        ...s.tests,
        [v]: {
          at: new Date().toISOString(),
          loaded: observed ? "observed" : "unknown",
          result,
          agentKey,
          envId,
          envName,
          against,
          scenarios,
          passedNew,
          passedCurrent,
        },
      },
    })),
  };
};

/** One line for a recorded test; older seed records carry no counts. */
export const testLineOf = (t) =>
  t.scenarios
    ? `${t.passedNew}/${t.scenarios} vs ${t.against ? `v${t.against}` : "without"} ${t.passedCurrent}/${t.scenarios}`
    : { helps: "Helped", same: "No change", worse: "Worse" }[t.result] || "";

/** Promote a version to a tag. Untested needs a reason, recorded with it. */
export const promote = (settings, id, v, tag, reason) => ({
  skillLibrary: withSkill(settings, id, (s) => ({
    ...s,
    tags: { ...s.tags, [tag]: v },
    promotions: [
      ...(s.promotions || []),
      {
        v,
        tag,
        at: new Date().toISOString(),
        by: currentUser()?.name || "You",
        reason: reason || null,
        tested: !!s.tests?.[v],
      },
    ],
  })),
});

export const attach = (settings, agentKey, skillId, ref) => {
  const all = attachmentsOf(settings);
  const list = (all[agentKey] || []).filter((a) => a.skillId !== skillId);
  return {
    skillAttachments: {
      ...all,
      [agentKey]: [...list, { skillId, ref, at: new Date().toISOString() }],
    },
  };
};
export const detach = (settings, agentKey, skillId) => {
  const all = attachmentsOf(settings);
  return {
    skillAttachments: {
      ...all,
      [agentKey]: (all[agentKey] || []).filter((a) => a.skillId !== skillId),
    },
  };
};

/* ── Testing a version (skills.md step 3; 26 Oct plan: positive, negative
   and interaction cases; no-skill vs current vs candidate) ─────────────── */

/** A version's files: SKILL.md first, then its scripts and references. */
export const filesOf = (version) => [
  { path: "SKILL.md", text: version?.text || "" },
  ...(version?.files || []),
];

/** name / description / body of a SKILL.md. */
export const parseSkillMd = (text) => {
  const m = /^---\n([\s\S]*?)\n---\n?([\s\S]*)$/.exec(text || "");
  const front = m ? m[1] : "";
  const field = (k) =>
    (new RegExp(`^${k}:\\s*(.*)$`, "m").exec(front) || [])[1] || "";
  return {
    name: field("name"),
    description: field("description"),
    body: (m ? m[2] : text || "").trim(),
  };
};

/* Requests that should and shouldn't fire the skill — near-misses are the
   useful negatives (agentskills.io: optimizing descriptions). */
const TRIGGER_SEED = {
  "lib-escalate-to-human": [
    { text: "Can I talk to a real person please?", should: true },
    { text: "Get me your manager.", should: true },
    {
      text: "This is the third time — I want someone who can fix it.",
      should: true,
    },
    { text: "I'd like to dispute a charge on my card.", should: true },
    { text: "Can you transfer my order to a new address?", should: false },
    { text: "Is a person going to check my refund?", should: false },
    { text: "What are your opening hours?", should: false },
  ],
  "lib-refund-policy": [
    { text: "I want my money back for this order.", should: true },
    { text: "Can you refund the $240 jacket?", should: true },
    { text: "It arrived broken, what can you do?", should: true },
    { text: "Can I exchange this for a bigger size?", should: false },
    { text: "When will my refund land in my account?", should: false },
    { text: "Cancel my subscription.", should: false },
  ],
  "lib-identity-check": [
    { text: "Change the email on my account.", should: true },
    { text: "Read me my card details.", should: true },
    { text: "What's the status of order 4471?", should: true },
    { text: "What's your returns policy?", should: false },
    { text: "Do you ship to Canada?", should: false },
  ],
};
export const testPlanOf = (skill) =>
  skill.testPlan || {
    trigger: TRIGGER_SEED[skill.id] || [
      {
        text: `${skill.description || skill.name}`.replace(/\.$/, ""),
        should: true,
      },
    ],
  };
export const saveTestPlan = (settings, id, plan) => ({
  skillLibrary: withSkill(settings, id, (s) => ({ ...s, testPlan: plan })),
});

const CASE_TITLES = [
  "Caller asks for a person straight away",
  "Angry caller after a failed refund",
  "Order lookup with a wrong email",
  "Refund above the $200 cap",
  "Return outside the 30-day window",
  "Caller changes the delivery address",
  "Duplicate charge on the card",
  "Caller asks the agent to read a card number",
];

/**
 * A test of version v: the same scenarios run three ways — this version,
 * without the skill, and the version in production — and the trigger
 * requests run 3 times each. Deterministic for a version and its inputs.
 */
const reportOf = (
  id,
  v,
  prodV,
  { at, envs, observed, agentKey, agentName, trigger },
) => {
  const seed = hash(`${id}:${v}:${prodV}:${envs.map((e) => e.id).join(",")}`);
  const cases = envs.flatMap((e, ei) =>
    (e.scenarios.length
      ? e.scenarios
      : CASE_TITLES.map((t, i) => ({ id: `${e.id}-${i}`, title: t }))
    )
      .slice(0, 8)
      .map((sc, i) => {
        const h = hash(`${seed}:${ei}:${i}`);
        const without = h % 3 === 0 ? 1 : 0;
        const prod = prodV ? Math.min(1, without + ((h >> 3) % 2)) : null;
        const base = prod ?? without;
        const cand =
          (h >> 6) % 9 === 0
            ? 0
            : Math.min(1, base + ((h >> 4) % 3 === 0 ? 1 : 0));
        return { id: sc.id, title: sc.title, env: e.name, without, prod, cand };
      }),
  );
  const pct = (k) =>
    cases.length
      ? Math.round((100 * cases.filter((c) => c[k]).length) / cases.length)
      : 0;
  const triggerRuns = (trigger || []).map((t, i) => {
    const h = hash(`${seed}:t:${i}`);
    const fired = t.should ? 3 - (h % 5 === 0 ? 1 : 0) : h % 6 === 0 ? 1 : 0;
    return { ...t, fired };
  });
  const passedNew = cases.filter((c) => c.cand).length;
  const passedCurrent = cases.filter((c) =>
    prodV ? c.prod : c.without,
  ).length;
  return {
    at,
    loaded: observed ? "observed" : "unknown",
    result:
      passedNew === passedCurrent
        ? "same"
        : passedNew > passedCurrent
          ? "helps"
          : "worse",
    agentKey,
    agentName,
    envId: envs[0]?.id,
    envName: envs.map((e) => e.name).join(", "),
    envNames: envs.map((e) => e.name),
    against: prodV,
    scenarios: cases.length,
    passedNew,
    passedCurrent,
    rates: {
      cand: pct("cand"),
      without: pct("without"),
      prod: prodV ? pct("prod") : null,
    },
    cases,
    trigger: triggerRuns,
    cost: { prod: 0.042, cand: 0.042 + ((seed % 7) - 2) / 1000 },
  };
};

function seedReport(id, v, prodV, at) {
  return reportOf(id, v, prodV, {
    at,
    envs: [
      { id: "env-voice-support", name: "Customer Support Line", scenarios: [] },
    ],
    observed: v === 1,
    agentName: "Customer Support Line agent",
    trigger: TRIGGER_SEED[id],
  });
}

export const runSkillTest = (settings, id, v, opts) => {
  const skill = libraryOf(settings).find((x) => x.id === id);
  const prodV =
    skill?.tags?.production && skill.tags.production !== v
      ? skill.tags.production
      : 0;
  return {
    skillLibrary: withSkill(settings, id, (s) => ({
      ...s,
      tests: {
        ...s.tests,
        [v]: reportOf(id, v, prodV, {
          ...opts,
          at: new Date().toISOString(),
          trigger: testPlanOf(s).trigger,
        }),
      },
    })),
  };
};

/** Did the version beat production (or no skill), in a word. */
export const verdictOf = (t) => {
  if (!t) return { word: "Not tested", tone: "neutral" };
  if (!t.cases)
    return {
      word:
        t.result === "worse"
          ? "Regression"
          : t.result === "same"
            ? "No change"
            : "Improvement",
      tone: t.result === "worse" ? "bad" : "neutral",
    };
  const regressed = t.cases.filter(
    (c) => (t.against ? c.prod : c.without) && !c.cand,
  ).length;
  const improved = t.cases.filter(
    (c) => !(t.against ? c.prod : c.without) && c.cand,
  ).length;
  if (regressed && improved)
    return { word: "Tradeoff", tone: "warn", improved, regressed };
  if (regressed)
    return { word: "Regression", tone: "bad", improved, regressed };
  if (improved)
    return { word: "Improvement", tone: "good", improved, regressed };
  return { word: "No change", tone: "neutral", improved, regressed };
};
