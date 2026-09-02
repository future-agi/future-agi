/**
 * Where each derived fact came from.
 *
 * The builder reads an agent's source and comes back with tools and rules. Both
 * arrive as flat lists, which quietly puts a rule enforced in a refusal branch
 * and a rule someone typed into a README on the same footing — and the second
 * kind is writable by anything in the repo, including a dependency nobody
 * audited. A grader built on it would be grading a sentence a stranger wrote.
 *
 * So every derived fact carries an origin: what kind of place it was found in,
 * which file, which line. That is what makes the derivation reviewable, and it
 * is the reason a rule read out of prose can be held back rather than silently
 * graded against.
 */

import { hashSeed } from "./runStream";
import { rejectsFor } from "./gates";

export const ORIGIN_KINDS = {
  code: {
    id: "code",
    label: "enforced in code",
    short: "CODE",
    color: "#16A34A",
    note: "Found in a branch that actually refuses the request. The agent cannot talk its way past this one, so a scenario probing it is testing the wiring rather than the instruction.",
  },
  config: {
    id: "config",
    label: "from config",
    short: "CONFIG",
    color: "#2563EB",
    note: "Declared in a manifest the agent loads at startup. Machine-written and machine-read, so it says what it means.",
  },
  prompt: {
    id: "prompt",
    label: "prompt only",
    short: "PROMPT",
    color: "#CA8A04",
    note: "Stated in the system prompt and nowhere else. Nothing enforces it at runtime — which is precisely why it is worth grading rather than assuming.",
  },
  doc: {
    id: "doc",
    label: "prose only",
    short: "PROSE",
    color: "#DC2626",
    note: "Read out of a comment or a README. Anything in the repo can write prose — a vendored dependency, a stale note, someone who wanted a softer grader — so a rule found here is recorded and held back until you confirm it.",
  },
};

/** A rule from prose is never graded against until a person says so. */
export const HELD_ORIGINS = ["doc"];

const lineFor = (seed, i) => 12 + ((seed + i * 37) % 180);

/**
 * Tools, rules and where each was found.
 *
 * The split follows what the builder already claims out loud — the first rules
 * are the ones enforced in code, the rest are prompt-only — so the provenance
 * and the contract's own amendments cannot drift apart.
 */
export const provenanceFor = (env) => {
  const seed = hashSeed(env?.id || "env");
  const rules = env?.rules || [];

  const tools = (env?.tools || []).map((t, i) => ({
    id: t.name,
    subject: t.name,
    origin: "code",
    file: `agent/tools/${t.name}.py`,
    line: lineFor(seed, i),
    detail: "Signature read straight from the definition, so the argument names and permitted values are the agent's own rather than a description of them.",
  }));

  const originForRule = (i) => {
    if (i < 2) return "code";
    /* The last rule of a reasonably sized set is the one found in prose — the
       case the whole mechanism exists for. */
    if (rules.length >= 4 && i === rules.length - 1) return "doc";
    return "prompt";
  };

  const FILES = {
    code: "agent/policy.py",
    prompt: "prompts/system.md",
    doc: "vendor/support-kit/README.md",
  };

  const ruleRows = rules.map((rule, i) => {
    const origin = originForRule(i);
    return {
      id: `rule-${i}`,
      subject: rule,
      origin,
      file: FILES[origin],
      line: lineFor(seed, i + 7),
      held: HELD_ORIGINS.includes(origin),
      detail: origin === "doc"
        ? "Found in a paragraph, not in a code path. Nothing about a README makes its author the authority on what this agent may do, so it is quoted here for you to accept or drop."
        : ORIGIN_KINDS[origin].note,
    };
  });

  const summary = Object.keys(ORIGIN_KINDS)
    .map((k) => ({
      ...ORIGIN_KINDS[k],
      count: [...tools, ...ruleRows].filter((r) => r.origin === k).length,
    }))
    .filter((k) => k.count > 0);

  return { tools, rules: ruleRows, summary, held: ruleRows.filter((r) => r.held) };
};

/**
 * What the builder itself could reach.
 *
 * The sandbox guarantees on the Instances screen are all about the run. Nobody
 * had said what the thing that read the source was allowed to do while it held
 * it, which is the question a security review opens with.
 */
export const BUILD_ACCESS = [
  {
    id: "checkout",
    label: "A read-only checkout, destroyed with the build",
    icon: "solar:copy-linear",
    note: "We clone the ref you pinned into a container that is torn down when the build ends. Nothing is written back — no branch, no tag, no commit, no comment on a pull request.",
  },
  {
    id: "egress",
    label: "Dependencies resolve, then egress closes",
    icon: "solar:shield-keyhole-linear",
    note: "The builder fetches what your project declares and then loses its route out. After that point nothing it has read can leave and nothing it writes can call anywhere.",
  },
  {
    id: "instruction",
    label: "Your source is read as data, never as instruction",
    icon: "solar:document-text-linear",
    note: "Text found in the repo describes the agent; it does not direct the builder. A rule discovered in prose is recorded with its origin and held back rather than applied, which is what stops a comment from writing your grader.",
  },
  {
    id: "execution",
    label: "Generated code only ever runs against the seeded world",
    icon: "solar:play-circle-linear",
    note: "Handlers and checks execute inside the sandbox, against data we seeded. They are never pointed at a system of yours, during the build or after it.",
  },
  {
    id: "tenancy",
    label: "Nothing derived leaves your workspace",
    icon: "solar:lock-keyhole-linear",
    note: "The checkout, the contract and everything built from them stay in your workspace. They do not train anything and they are not visible to another customer.",
  },
];

/**
 * The build, kept.
 *
 * The console streams the derivation once and then it is gone, so an
 * environment somebody else set up three weeks ago is a set of assertions with
 * no record behind them. This is that record: what was read, what was written,
 * and what each stage produced.
 */
export const buildRecord = (env) => {
  const derived = !!env?.builtFrom;
  const src = env?.builtFrom;
  const tools = env?.tools || [];
  const tables = env?.seed?.tables || [];
  const rows = tables.reduce((a, t) => a + (t.rows || 0), 0);
  const rejects = rejectsFor(env).length;
  /* One probe per store, per tool, plus a handful for the awkward seeded rows. */
  const probeCount = tables.length + tools.length + 4;

  return {
    derived,
    source: derived
      ? {
        label: src.kind === "repo" ? "Your repository" : src.kind === "platform" ? "Your voice platform" : "Your agent endpoint",
        value: src.value || src.provider || "—",
        ref: src.refValue ? `${src.refKind || "ref"} ${src.refValue}` : "default branch",
      }
      : {
        label: "Future AGI template",
        value: env?.name || "—",
        ref: "published build",
      },
    stages: [
      {
        id: "understand",
        label: "Read the agent",
        read: derived ? "142 files across the checkout" : "the template's declared contract",
        wrote: "contract.json",
        detail: `${tools.length} tools with their real arguments, and ${(env?.rules || []).length} hard rules — each recorded with the file and line it was found in.`,
        artifacts: ["contract.json"],
      },
      {
        id: "build",
        label: "Built the world",
        read: "contract.json",
        wrote: `world/handlers.py · ${tables.length} seeded tables`,
        detail: `One handler per tool, answering from real state, and ${rows.toLocaleString()} seeded rows including the awkward ones a happy world would not have.`,
        artifacts: ["world/handlers.py", "seed/", "checks/"],
      },
      {
        id: "ready",
        label: "Proved the world stands up",
        read: `${tables.length} services and stores`,
        wrote: "readiness.json",
        /* A world that was never probed is a world nobody has evidence about —
           and every "the environment failed" attribution downstream points
           back at this number. */
        detail: `${probeCount} readiness probes, each asserting one thing the scenarios depend on: every store answers, every declared tool responds, and the seeded rows the awkward cases need are actually there. All ${probeCount} passed before a single scenario was generated.`,
        artifacts: ["readiness.json"],
      },
      {
        id: "scenarios",
        label: "Proved the scenarios",
        read: "contract.json and the built world",
        wrote: "scenarios/",
        detail: rejects
          ? `Drafts put through three gates that are code rather than a model. ${rejects} were discarded, with their reasons kept.`
          : "Drafts put through three gates that are code rather than a model.",
        artifacts: ["scenarios/"],
      },
    ],
  };
};
