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
      },
    ],
    tags: { production: 1, staging: 2 },
    tests: {
      1: { at: daysAgo(55), loaded: "observed", result: "helps" },
      2: { at: daysAgo(20), loaded: "unknown", result: "helps" },
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
      },
    ],
    tags: { production: 1 },
    tests: { 1: { at: daysAgo(29), loaded: "observed", result: "helps" } },
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
export const newVersion = (settings, id, { text, note }) => ({
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
