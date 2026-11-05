/*
  What an edit to one scenario does to its version — PRD v7 §4.10.3.

  Every edit bumps by KIND, never by how much changed, and the kind is
  stated before saving with what it does to past results:

    patch   cosmetic: name, description, labels            → past results reused
    minor   a grader fix on its checks, same intent          → past runs re-graded
    major   what the caller does: instruction, persona,
            fixture identity, setup — or a rewrite after
            it was outdated                                   → re-run; not comparable
                                                                until bridged

  Set-level marks (tier, canary, the old `critical` flag) aren't the
  scenario's content: they move S, not the scenario.
*/
import { bumpScenario, SCENARIO_BUMPS } from "./decoupled";
import { BUMP_RANK, suiteLogPatch } from "./suiteLog";
import { archiveScenario, archivedOf, restoreScenario } from "./scenarioStates";
import { setBlocker } from "./releaseBlocker";
import { scenarioTitle } from "./scenarios";

const COSMETIC = ["name", "title", "summary", "description", "labels", "tags"];
const GRADER = [
  "expected",
  "outcome",
  "idealOutcome",
  "subGoals",
  "subGoalMarks",
  "checks",
  "allowedVariation",
  "mustNot",
];
const CALLER = [
  "task",
  "situation",
  "persona",
  "caller",
  "turns",
  "backgroundNoise",
  "setup",
  "fixture",
  "twinSeedPrompt",
  "requiredTools",
  "conversationBranch",
  "branchCategory",
  "useCase",
];
/* Marks on the set, and bookkeeping — not the scenario's content. */
export const META_FIELDS = [
  "tier",
  "canary",
  "critical",
  "criticalBy",
  "criticalWas",
  "callerEdited",
  "subGoalsUnproved",
  "editedAt",
  "provedAt",
  "provedIn",
  "ruleChecked",
  "version",
  "versionLog",
  "archived",
  "proposed",
];

const LABEL = {
  name: "name",
  title: "title",
  summary: "description",
  description: "description",
  labels: "labels",
  tags: "labels",
  expected: "passes-when",
  outcome: "passes-when",
  idealOutcome: "passes-when",
  subGoals: "sub-goals",
  subGoalMarks: "sub-goal marks",
  checks: "checks",
  task: "caller instruction",
  situation: "caller instruction",
  persona: "persona",
  caller: "caller",
  turns: "call constraints",
  backgroundNoise: "call constraints",
  setup: "setup",
  fixture: "fixture",
  twinSeedPrompt: "setup",
  requiredTools: "tools",
  conversationBranch: "path",
  branchCategory: "path",
  useCase: "use case",
};

const same = (a, b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

/** The fields that differ, by group. */
export const changedFields = (before = {}, after = {}) => {
  const diff = (keys) => keys.filter((k) => !same(before[k], after[k]));
  /* Anything else on a row is bookkeeping (provenance, proof stamps) — an
     edit never writes it, so it never decides a bump. */
  return {
    cosmetic: diff(COSMETIC),
    grader: diff(GRADER),
    caller: diff(CALLER),
    meta: diff(META_FIELDS),
  };
};

const words = (keys) => [...new Set(keys.map((k) => LABEL[k] || k))].join(", ");

/**
 * The bump an edit makes. `intent` answers the one question a grader edit
 * leaves open: "same" (a fix) or "changed" (it tests something else now —
 * a major). → { level, why, fields, graderEdit, rewrite, prior } — level
 * null when only set-level marks moved.
 */
const bumpOf = (before, after, intent) => {
  const f = changedFields(before, after);
  const rewrite = !!before?.outdatedBy;
  const graderEdit = f.grader.length > 0;
  if (rewrite && (f.caller.length || graderEdit || f.cosmetic.length)) {
    return {
      level: "major",
      why: `Rewritten after it was outdated — ${words([...f.caller, ...f.grader, ...f.cosmetic])}`,
      fields: f,
      graderEdit,
      rewrite,
    };
  }
  if (f.caller.length)
    return {
      level: "major",
      why: `Changed ${words(f.caller)}`,
      fields: f,
      graderEdit,
      rewrite,
    };
  if (graderEdit) {
    return intent === "changed"
      ? {
          level: "major",
          why: `Changed what it tests — ${words(f.grader)}`,
          fields: f,
          graderEdit,
          rewrite,
        }
      : {
          level: "minor",
          why: `Grader fix — ${words(f.grader)}`,
          fields: f,
          graderEdit,
          rewrite,
        };
  }
  if (f.cosmetic.length)
    return {
      level: "patch",
      why: `Renamed or relabelled — ${words(f.cosmetic)}`,
      fields: f,
      graderEdit,
      rewrite,
    };
  return { level: null, why: null, fields: f, graderEdit, rewrite };
};

/* The bump also carries the version it replaces (`prior`), so applying it
   keeps that version's content for runs that pinned it (§4.10.8). */
export const editBumpOf = (before, after, { intent = "same" } = {}) => ({
  ...bumpOf(before, after, intent),
  prior: before || null,
});

/** What the bump does, in a sentence — the line shown before Save. */
export const bumpNote = (level) =>
  level
    ? SCENARIO_BUMPS[level].note
    : "Only how the set treats it changed — the scenario itself is the same.";

/** The row with its bump applied, or as it is when nothing bumped. The
 *  replaced version is snapshotted into the log entry (see bumpScenario). */
export const applyEditBump = (after, bump) =>
  bump?.level
    ? bumpScenario(after, bump.level, bump.why, bump.prior || undefined)
    : after;

export const biggest = (levels = []) =>
  levels.reduce(
    (m, l) => ((BUMP_RANK[l] || 0) > (BUMP_RANK[m] || 0) ? l : m),
    null,
  );

/* ── acting on a selection from the chat ──────────────────────────────────── */

/* Named the way the Scenarios table names a row (summary, then title), so
   three rows of one goal don't all read as the goal. Long ones are cut at a
   word with an ellipsis. */
const clip = (t, max = 48) => {
  if (t.length <= max) return t;
  const cut = t.slice(0, max);
  return `${cut.slice(0, cut.lastIndexOf(" ") > 24 ? cut.lastIndexOf(" ") : max).replace(/[\s,;:—-]+$/, "")}…`;
};
const rowName = (r) =>
  `"${clip(String(r.summary || scenarioTitle(r.title) || r.name || "scenario"))}"`;
const namesOf = (rows) => {
  const shown = rows.slice(0, 3).map(rowName).join(", ");
  return rows.length > 3 ? `${shown} and ${rows.length - 3} more` : shown;
};
const scenarios = (n) => `${n} scenario${n === 1 ? "" : "s"}`;

/**
 * What a chat message asks of the scenarios selected on the Scenarios tab.
 * Only what the tab itself can do to a selection is done here — archive,
 * restore, must-pass on or off — through the same row helpers, as the next
 * scenario-set version. Anything else is answered plainly: it can't be done
 * from chat yet, and where to do it. Never a reply that claims an edit.
 *
 * → { patch?, reply }
 */
export const selectionEditOf = (text, rows, envState, { by = null } = {}) => {
  const t = String(text || "").toLowerCase();
  const ids = new Set((rows || []).map((r) => r.id));
  const current = (envState?.scenarios || []).filter((r) => ids.has(r.id));
  const write = (targets, change, summary) => {
    const byId = new Map(targets.map((r) => [r.id, change(r)]));
    return suiteLogPatch(
      envState,
      {
        scenarios: (envState?.scenarios || []).map((r) => byId.get(r.id) || r),
      },
      { summary, by },
    );
  };

  if (/\b(restore|unarchive|bring back)\b/.test(t)) {
    const targets = current.filter((r) => archivedOf(r));
    if (!targets.length)
      return {
        reply:
          "None of the selected scenarios are archived — nothing to restore.",
      };
    return {
      patch: write(
        targets,
        restoreScenario,
        `Restored ${scenarios(targets.length)}`,
      ),
      reply: `Restored ${scenarios(targets.length)} — ${namesOf(targets)}. They run and count again, as the next scenario-set version.`,
    };
  }
  if (/\b(archive|drop|remove|delete|retire)\b/.test(t)) {
    const targets = current.filter((r) => !archivedOf(r) && !r.proposed);
    if (!targets.length)
      return {
        reply:
          "The selected scenarios are already archived or still proposals — nothing to archive.",
      };
    return {
      patch: write(
        targets,
        (r) => archiveScenario(r, "Archived from chat"),
        `Archived ${scenarios(targets.length)}`,
      ),
      reply: `Archived ${scenarios(targets.length)} — ${namesOf(targets)}. They stay listed (filter Archived) but won't run or count. Restore them from the Scenarios tab, or ask me to.`,
    };
  }
  if (/must[- ]?pass/.test(t)) {
    const on = !/\b(not|no longer|unmark|remove|off|clear)\b/.test(t);
    const targets = current.filter(
      (r) => !!r.critical !== on && !archivedOf(r),
    );
    if (!targets.length)
      return {
        reply: on
          ? "The selected scenarios are already must-pass."
          : "None of the selected scenarios are must-pass.",
      };
    return {
      patch: write(
        targets,
        (r) => setBlocker(r, on),
        `${scenarios(targets.length)} ${on ? "marked must-pass" : "no longer must-pass"}`,
      ),
      reply: `${scenarios(targets.length)} ${on ? "marked must-pass — an improvement that breaks one is rejected" : "no longer must-pass"}: ${namesOf(targets)}.`,
    };
  }
  return {
    reply: `I can't rewrite the ${scenarios(current.length)} you selected from chat yet. Open one on the Scenarios tab to edit it — each edit is a new scenario version, and you'll see what it does to past results before saving. For a selection I can archive, restore, or mark them must-pass.`,
  };
};
