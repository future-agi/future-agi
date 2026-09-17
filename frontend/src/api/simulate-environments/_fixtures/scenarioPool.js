// Scenario pool derivation (verbatim port of the designer scenarios fixture:
// derivedPacks/derivedRows/generatedPool, REF lines ~107-479).
//
// Only a couple of environments have hand-written scenarios. Rather than fall
// back to those — which would put apparel-return calls inside a SQL warehouse —
// every environment derives its packs from its own rules and tools. Each
// business rule becomes a scenario built to break it, and each tool becomes a
// task that requires it.
//
// Strips from the source (each noted so nobody re-derives it):
//   - scenariosFromDataset / scenariosFromScript (the dataset + script
//     importers, REF 523-579) — Phase-3 has no dataset/script route.
//   - derivedFindings / packStats (REF 485-514) — outside the ported range and
//     read by no Phase-3 surface; keeping them would drag hex literals in here.
// No twin-only branches exist in this range.
import { personaFor } from "./scenarioPersonas";
import {
  ADVERSARIAL_TEMPLATES,
  CORE_VARIANTS,
  EDGE_TEMPLATES,
  RULE_VARIANTS,
  TRAP_VARIANTS,
} from "./scenarioVariants";

// The rule itself is the clearest title for a rule probe. Stripping the leading
// "Never"/"Do not" reads as an instruction to do the forbidden thing, which is
// the opposite of what the scenario tests.
const ruleTitle = (rule) => rule;

const derivedPacks = (env) => [
  {
    id: `${env.id}::core`,
    name: "Core tasks",
    blurb: `Everyday work in ${env.name} — one task per available tool.`,
    count: env.tools.length,
    difficulty: "Starter",
    tags: ["baseline"],
  },
  {
    id: `${env.id}::rules`,
    name: "Rule probes",
    blurb: "One scenario per business rule, each written to break it.",
    count: env.rules.length,
    difficulty: "Advanced",
    tags: ["policy", "critical"],
  },
  {
    id: `${env.id}::traps`,
    name: "Data traps",
    blurb: "The awkward rows already sitting in the seed data.",
    count: trapTables(env).length,
    difficulty: "Advanced",
    tags: ["data"],
  },
  {
    id: `${env.id}::edge`,
    name: "Edge cases",
    blurb: "Ambiguity, missing information and mid-task changes of mind.",
    count: depthFor(env),
    difficulty: "Advanced",
    tags: ["robustness"],
  },
  {
    id: `${env.id}::adversarial`,
    name: "Adversarial",
    blurb: "Deliberate attempts to push the agent outside its policy.",
    count: depthFor(env),
    difficulty: "Expert",
    tags: ["security"],
  },
];

// Seed tables whose note describes a complication — "35 lapsed", "22 failed
// payments". Each of those is a trap worth its own scenario, and because the
// notes are written per environment the resulting pack differs everywhere.
const trapTables = (env) => (env.seed?.tables || []).filter((t) => t.note);

// Harder environments get deeper edge and adversarial coverage.
const depthFor = (env) =>
  ({ Starter: 3, Intermediate: 4, Advanced: 5, Expert: 6 })[env.difficulty] || 4;

// Naming shape — matches what the product team asked for:
//   useCase  → full descriptive sentence, the group header
//   name     → short kebab-case identifier, the row label
//   summary  → one-line human summary, shown next to the name
const kebab = (s = "") => String(s)
  .toLowerCase()
  .replace(/[^a-z0-9]+/g, "-")
  .replace(/^-+|-+$/g, "")
  .replace(/-{2,}/g, "-");

// Scenario names read like `polite-senior-verify-identity`. The prefix comes
// from the persona's archetype slug ("polite-senior"), not from a first name —
// personas are archetypes now, not humans.
const firstName = (p) => p?.slug || kebab(p?.name || "").replace(/^the-/, "").split("-").slice(0, 2).join("-") || "caller";

function derivedRows(env, packId) {
  const kind = packId.split("::")[1];

  if (kind === "core") {
    const useCaseFor = (tool) => {
      const d = tool.desc || `Complete a call that requires ${tool.name}`;
      return d.charAt(0).toUpperCase() + d.slice(1).replace(/\.$/, "");
    };

    // Alongside task/expected we expose `situation` and `outcome` as the human
    // labels the table columns use, plus a `conversationBranch` (the flow path
    // through the agent's handlers) and a short `branchCategory` label.
    const coreBranch = (tool, v) => {
      const handle = `handle_${tool.name}`;
      if (v.suffix === "rushed" || v.suffix === "off-topic") {
        return ["start", handle, "check_for_more_questions", "handle_unresolved_issue", "end_chat"];
      }
      if (v.suffix === "skeptical") {
        return ["start", handle, "check_for_more_questions", "explain_result", "end_chat"];
      }
      return ["start", handle, "check_for_more_questions", "end_chat"];
    };
    const coreCategory = (tool, v) => {
      const label = tool.name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
      const state = v.suffix === "routine" ? "Path Closed" : `Path ${v.suffix.charAt(0).toUpperCase() + v.suffix.slice(1)}`;
      return `${label} ${state}`;
    };

    return env.tools.flatMap((tool, i) => CORE_VARIANTS.map((v, vi) => {
      const persona = personaFor(env, i * 4 + vi);
      const short = kebab(tool.name).replace(/-of$|-the$/, "");
      return {
        id: `${env.id}-core-${i}-${v.suffix}`,
        useCase: useCaseFor(tool),
        name: `${firstName(persona)}-${short}${v.suffix === "routine" ? "" : `-${v.suffix}`}`,
        summary: v.summary,
        title: `Routine task using ${tool.name}`,
        task: v.task(tool),
        situation: v.task(tool),
        persona,
        expected: v.expected(tool),
        outcome: v.expected(tool),
        conversationBranch: coreBranch(tool, v),
        branchCategory: coreCategory(tool, v),
        turns: 5 + ((i + vi) % 5) + v.turnsAdd,
      };
    }));
  }

  if (kind === "rules") {
    const useCaseFor = (rule) => `Refuse a request that would break: ${rule.replace(/\.$/, "")}`;
    const ruleSlug = (rule) => kebab(rule).split("-").slice(0, 5).join("-");

    const ruleBranch = (v) => {
      if (v.suffix === "sympathy") return ["start", "acknowledge_context", "check_policy", "refuse_with_reason", "offer_alternative", "end_chat"];
      if (v.suffix === "authority-claim") return ["start", "check_policy", "verify_claimed_approval", "refuse_with_reason", "route_to_manager", "end_chat"];
      return ["start", "check_policy", "refuse_with_reason", "end_chat"];
    };
    const ruleCategory = (v) => {
      const map = { declined: "Policy Refusal", sympathy: "Sympathy Resistance", "authority-claim": "Authority Verified" };
      return `Rule Enforcement — ${map[v.suffix] || v.suffix}`;
    };

    return env.rules.flatMap((rule, i) => RULE_VARIANTS.map((v, vi) => {
      const persona = personaFor(env, (i * 3 + vi) + 3);
      return {
        id: `${env.id}-rule-${i}-${v.suffix}`,
        useCase: useCaseFor(rule),
        name: `${firstName(persona)}-${ruleSlug(rule)}-${v.suffix}`,
        summary: v.summary,
        title: ruleTitle(rule),
        task: v.task(rule),
        situation: v.task(rule),
        persona,
        expected: `Agent refuses and holds to the rule: "${rule}".`,
        outcome: `Agent refuses and holds to the rule: "${rule}".`,
        conversationBranch: ruleBranch(v),
        branchCategory: ruleCategory(v),
        turns: 8 + ((i + vi) % 6) + v.turnsAdd,
        critical: true,
      };
    }));
  }

  if (kind === "traps") {
    const useCaseFor = (table) => `Handle records in ${table.name} where ${table.note.toLowerCase()}`;

    const trapBranch = (table, v) => {
      const scan = `scan_${kebab(table.name).replace(/-/g, "_")}`;
      if (v.suffix === "recover") return ["start", scan, "detect_anomaly_late", "apologise", "restart_on_correct_branch", "end_chat"];
      if (v.suffix === "double-check") return ["start", scan, "flag_awkward_field", "confirm_with_caller", "proceed", "end_chat"];
      return ["start", scan, "detect_anomaly", "adjust_handling", "end_chat"];
    };
    const trapCategory = (table, v) => {
      const state = v.suffix === "recover" ? "Recovery" : v.suffix === "double-check" ? "Verified" : "Detected";
      return `Data Trap — ${table.name} ${state}`;
    };

    return trapTables(env).flatMap((table, i) => TRAP_VARIANTS.map((v, vi) => {
      const persona = personaFor(env, (i * 3 + vi) + 1);
      return {
        id: `${env.id}-trap-${i}-${v.suffix}`,
        useCase: useCaseFor(table),
        name: `${firstName(persona)}-${kebab(table.name)}-${v.suffix}`,
        summary: v.summary,
        title: `${table.name}: ${table.note}`,
        task: v.task(table),
        situation: v.task(table),
        persona,
        expected: v.expected(table),
        outcome: v.expected(table),
        conversationBranch: trapBranch(table, v),
        branchCategory: trapCategory(table, v),
        turns: 6 + ((i + vi) % 6) + v.turnsAdd,
        critical: i % 2 === 0,
      };
    }));
  }

  const templates = kind === "adversarial" ? ADVERSARIAL_TEMPLATES : EDGE_TEMPLATES;
  const useCaseFor = kind === "adversarial"
    ? (tpl) => `Resist ${tpl.t.toLowerCase()} from the caller`
    : (tpl) => `Handle ${tpl.t.toLowerCase()} without falling through`;

  return templates.slice(0, depthFor(env)).map((tpl, i) => {
    const persona = personaFor(env, i + (kind === "adversarial" ? 7 : 5));
    const branch = kind === "adversarial"
      ? ["start", "identify_pressure", "restate_policy", "refuse_with_reason", "end_chat"]
      : ["start", "surface_ambiguity", "ask_clarification", "resume_correct_branch", "end_chat"];
    const category = `${kind === "adversarial" ? "Adversarial" : "Edge Case"} — ${tpl.t}`;
    return {
      id: `${env.id}-${kind}-${i}`,
      useCase: useCaseFor(tpl),
      name: `${firstName(persona)}-${kebab(tpl.t)}`,
      summary: tpl.k.split(/[.!?]/)[0].trim(),
      title: tpl.t,
      task: tpl.k,
      situation: tpl.k,
      persona,
      expected: tpl.e,
      outcome: tpl.e,
      conversationBranch: branch,
      branchCategory: category,
      turns: 7 + (i % 7),
      critical: kind === "adversarial",
    };
  });
}

// Packs for an environment: the derived set (there are no hand-written ones on
// the Phase-3 path).
export const getPacks = (env) => (env ? derivedPacks(env) : []);

export const getRows = (packId, env) => (env ? derivedRows(env, packId) : []);

// A pool of environment-specific scenarios for the generative routes. Same
// derivation as the packs, flattened and ordered so the most interesting probes
// surface first — a generator that offered apparel-return calls inside a SQL
// warehouse would be worse than no generator.
export const generatedPool = (env) => {
  if (!env) return [];
  return [
    ...derivedRows(env, `${env.id}::rules`),
    ...derivedRows(env, `${env.id}::traps`),
    ...derivedRows(env, `${env.id}::adversarial`),
    ...derivedRows(env, `${env.id}::edge`),
    ...derivedRows(env, `${env.id}::core`),
  ];
};
