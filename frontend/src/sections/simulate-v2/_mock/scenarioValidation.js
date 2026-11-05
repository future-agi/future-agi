/*
  Validate scenario against the environment (scenarios.md, "What grounding
  means and why it matters"): a test is checked against the records, tools
  and rules of the environment version it runs in, before anyone relies on
  it. It passes when

    data      every record it mentions is in the environment
    tools     every action it needs is available
    rules     the outcome it expects is one the environment's rules allow
    can-fail  a deliberately wrong answer fails it (the negative control)

  plus "a correct answer can pass" (the reference solution). A failing check
  names what's wrong and offers the fix the plan describes: pick a real
  record or approve a synthetic one, accept the environment's rule, add the
  tool, or add the check that catches the wrong answer. Grounding is never
  about getting a judge to agree with the expected answer.
*/
import { proveOnAdd } from "./addScenarios";
import { declineKindOf } from "./admissionGates";
import { rulesOf } from "./envModel";
import { knownToolsFor, requiredToolsOf, worldToolsFor } from "./toolFit";
import { currentEnvVersion } from "./versions";

export const VALIDATION_CHECKS = [
  { id: "data", label: "The data exists" },
  { id: "tools", label: "The tools exist" },
  { id: "rules", label: "The rules agree" },
  { id: "can-fail", label: "A wrong answer fails" },
  { id: "can-pass", label: "A correct answer can pass" },
];

const TEXT_FIELDS = [
  "title",
  "summary",
  "name",
  "task",
  "brief",
  "expected",
  "outcome",
  "situation",
];
const textOf = (row) =>
  TEXT_FIELDS.map((k) => (typeof row?.[k] === "string" ? row[k] : "")).join(
    " ",
  );

/* Record ids a scenario names: order O-999, booking BK-2041, case #4471. */
const RECORD_ID = /\b([A-Z]{1,3}-\d{3,6})\b/g;
const idsIn = (s) => [
  ...new Set([...String(s).matchAll(RECORD_ID)].map((m) => m[1])),
];

/** The record ids this environment version holds (its seed, imports and starting state). */
export const knownRecordsOf = (env, envState) =>
  idsIn(
    JSON.stringify([
      env?.world || null,
      env?.seed || null,
      envState?.dataImports || [],
      envState?.startingState || null,
      envState?.worldEdits || null,
    ]),
  );

/* A day window in text: "within 30 days", "a 14-day window". */
const DAYS = /(\d{1,3})[\s-]*days?/i;
const windowOf = (s) => {
  const m = String(s || "").match(DAYS);
  return m ? Number(m[1]) : null;
};
const ABOUT_REFUNDS = /refund|return|cancel/i;

/* The wrong answer the negative control tries, from what the test is about. */
export const wrongAnswerOf = (row, tools) => {
  const t = textOf(row).toLowerCase();
  if (declineKindOf(row))
    return /refund/.test(t)
      ? "gives the refund anyway"
      : "goes ahead with what it should refuse";
  if (/refund/.test(t)) return "issues two refunds for the same order";
  if (/rebook|book/.test(t)) return "books the wrong flight";
  if (tools[0]) return `skips ${tools[0]}`;
  return "says it's done without doing it";
};

/**
 * Runs the checks for one scenario on the environment's current version.
 * → { pass, at, envVersion, checks: [{ id, label, pass, reason?, fix? }] }
 */
export function validateScenario(row, env, envState) {
  const envVersion = currentEnvVersion(env, envState).label;
  const gates = proveOnAdd(row, env, envVersion).gates;
  const gate = (id) => gates.find((g) => g.id === id);
  const text = textOf(row);
  const fixes = row?.validationFixes || {};

  /* data — every record it names is in the world, or a synthetic one was approved */
  const known = knownRecordsOf(env, envState);
  const synthetic = new Set(row?.syntheticRecords || []);
  const named = idsIn(text);
  const missing = known.length
    ? named.filter((id) => !known.includes(id) && !synthetic.has(id))
    : named.filter((id) => /9{3}/.test(id) && !synthetic.has(id));
  const staged = gate("staged");
  const data = missing.length
    ? {
        pass: false,
        reason: `It names ${missing.join(", ")}, which isn't in this environment.`,
        fix: {
          kind: "record",
          missing: missing[0],
          options: (known.length ? known : ["O-104", "O-117", "O-122"]).slice(
            0,
            4,
          ),
        },
      }
    : staged?.pass === false
      ? {
          pass: false,
          reason: "The world can't set up the records it presumes.",
        }
      : {
          pass: true,
          reason: synthetic.size
            ? `Uses an approved synthetic record (${[...synthetic].join(", ")}).`
            : null,
        };

  /* tools — every action it needs, the world can answer */
  const knownTools = knownToolsFor(env, envState);
  const needs = requiredToolsOf(row, knownTools);
  const answers = new Set(worldToolsFor(env, envState).map((t) => t.name));
  const noTool = needs.filter((t) => !answers.has(t));
  const tools = noTool.length
    ? {
        pass: false,
        reason: `It needs ${noTool.join(", ")}, which this environment can't answer.`,
        fix: { kind: "tool", tools: noTool },
      }
    : { pass: true };

  /* rules — the outcome it expects is one the rules allow */
  const rules = rulesOf(env, envState);
  const want = ABOUT_REFUNDS.test(text) ? windowOf(text) : null;
  const rule =
    want != null
      ? rules.find(
          (r) =>
            ABOUT_REFUNDS.test(r.text) &&
            windowOf(r.text) != null &&
            windowOf(r.text) !== want,
        )
      : null;
  const rulesCheck = rule
    ? {
        pass: false,
        reason: `It expects ${want} days; the environment's rule ${rule.id} says ${windowOf(rule.text)}: “${rule.text}”`,
        fix: {
          kind: "rule",
          ruleId: rule.id,
          from: want,
          to: windowOf(rule.text),
        },
      }
    : { pass: true };

  /* can-fail — the negative control: a deliberately wrong answer must fail */
  const wrong = wrongAnswerOf(row, needs);
  const vacuous = gate("not-vacuous")?.pass === false && !fixes.addedCheck;
  const canFail = vacuous
    ? {
        pass: false,
        reason: `A wrong answer still passes: an agent that ${wrong} isn't caught by any check — the checks are too loose. Add one, for example “${addedCheckFor(wrong)}”.`,
        fix: { kind: "check", wrong, check: addedCheckFor(wrong) },
      }
    : {
        pass: true,
        reason: fixes.addedCheck
          ? `Caught by the added check: “${fixes.addedCheck}”.`
          : `Tried an agent that ${wrong} — it failed, as it should.`,
      };

  const canPass =
    gate("solvable")?.pass === false
      ? {
          pass: false,
          /* Say which assertion can't be met, not just that one can't (scenarios.md). */
          reason: row?.expected
            ? `A known-good run doesn't reach the expected end state — this assertion can't be met in the world: “${row.expected}”. Change it or the starting data.`
            : "A known-good run doesn't reach the expected end state — the scenario has no Passes when to meet. Add one.",
        }
      : { pass: true };

  const byId = {
    data,
    tools,
    rules: rulesCheck,
    "can-fail": canFail,
    "can-pass": canPass,
  };
  const checks = VALIDATION_CHECKS.map((c) => ({ ...c, ...byId[c.id] }));
  return {
    pass: checks.every((c) => c.pass),
    at: new Date().toISOString(),
    envVersion,
    checks,
  };
}

/* The check that catches a wrong answer. */
const addedCheckFor = (wrong) =>
  /two refunds/.test(wrong)
    ? "Exactly one refund is issued for the order"
    : /refund anyway/.test(wrong)
      ? "No refund is issued"
      : /wrong flight/.test(wrong)
        ? "The passenger is booked on the flight they asked for"
        : "The required action is visible in the world afterwards";

/* ── fixes: each changes the scenario, then it's validated again ────────── */

const replaceIn = (row, from, to) => {
  const next = { ...row };
  TEXT_FIELDS.forEach((k) => {
    if (typeof next[k] === "string") next[k] = next[k].split(from).join(to);
  });
  return next;
};

/** Pick a real record in place of the one that isn't in the world. */
export const pickRecordFix = (row, missing, id) => replaceIn(row, missing, id);

/** Keep the record, as a labelled synthetic one the world stages for this test. */
export const syntheticRecordFix = (row, id) => ({
  ...row,
  syntheticRecords: [...new Set([...(row.syntheticRecords || []), id])],
});

/** Accept the environment's rule: the test now expects what the rule allows. */
export const acceptRuleFix = (row, from, to) =>
  replaceIn(
    replaceIn(row, `${from} days`, `${to} days`),
    `${from}-day`,
    `${to}-day`,
  );

/** Add the check that catches the wrong answer. */
export const addCheckFix = (row, check) => ({
  ...row,
  validationFixes: { ...(row.validationFixes || {}), addedCheck: check },
});

/** Saves a validation result on the scenario: Validated, or a blocked draft with the reason. */
export const validationPatchOf = (envState, rowId, result, nextRow) => {
  const failed = result.checks.find((c) => !c.pass);
  return {
    scenarios: (envState.scenarios || []).map((r) =>
      r.id === rowId
        ? {
            ...(nextRow || r),
            validatedAt: result.at,
            validation: {
              at: result.at,
              envVersion: result.envVersion,
              pass: result.pass,
            },
            blocked: failed ? { gate: failed.id, reason: failed.reason } : null,
          }
        : r,
    ),
  };
};
