/*
  Refreshing a source from the Knowledge base (data.md journey step 5): a new
  revision is compared, each change is accepted or rejected on its own, the
  scenarios each one touches are named, and only then is the result validated
  and saved as a new environment version. If validation fails, the revision
  the environment already pins stays; the reason and the failing rows come
  back with a way to fix them.
*/
import { kbChangesOf, kbRefreshOf } from "./kbCatalog";
import { nextEnvVersion, environmentVersions } from "./versions";
import { nextRuleNumber } from "./orgData";
import { scenarioTitle } from "./scenarios";

export const CHANGE_KIND = {
  "rule-added": { label: "Rule added", group: "rules" },
  "rule-changed": { label: "Rule changed", group: "rules" },
  "row-added": { label: "Rows added", group: "rows" },
  "row-changed": { label: "Rows changed", group: "rows" },
  "row-removed": { label: "Rows removed", group: "rows" },
};

const textOf = (sc) =>
  [sc.title, sc.name, sc.task, sc.situation, sc.expected, sc.outcome]
    .filter(Boolean)
    .join(" ");

/* The live scenarios a change touches, by what they say. */
const touchedBy = (change, envState) =>
  (envState?.scenarios || [])
    .filter((sc) => !sc.retired && !sc.archived && !sc.proposed)
    .filter((sc) => {
      const text = textOf(sc);
      return (
        (change.ids || []).some((id) => text.includes(id)) ||
        change.match?.test(text)
      );
    })
    .map((sc) => ({
      id: sc.id,
      name:
        scenarioTitle(
          sc.title || sc.name || sc.summary || sc.situation || sc.scenario,
        ) || sc.id,
    }));

/**
 * What a newer revision changes, or null when none is waiting. Each change
 * names the scenarios it touches; `scenarios` is the union.
 */
export const kbRefreshPlanOf = (envState, source) => {
  const refresh = kbRefreshOf(source);
  if (!refresh) return null;
  const changes = kbChangesOf(source.kb.fileId, refresh.latest).map((c) => ({
    ...c,
    scenarios: touchedBy(c, envState),
  }));
  const seen = new Set();
  const scenarios = changes
    .flatMap((c) => c.scenarios)
    .filter((s) => !seen.has(s.id) && seen.add(s.id));
  return { source, ...refresh, changes, scenarios };
};

/* Does a file in the draft hold the records a change points at? */
const suppliedFor = (envState, table) =>
  (envState?.sources || []).some(
    (s) => !s.archived && (s.fills || []).includes(table),
  );

/**
 * Validate the changes that were accepted. Returns { ok, failures }; each
 * failure names the change, the reason, the failing rows and a way to fix it.
 */
export const validateKbRefresh = (envState, plan, accepted) => {
  const taken = plan.changes.filter((c) => accepted.includes(c.id));
  if (!taken.length)
    return {
      ok: false,
      failures: [
        {
          id: "none",
          reason: "No change is accepted, so there is nothing to save.",
          rows: [],
          fix: "reject-all",
        },
      ],
    };
  const failures = taken.flatMap((c) => {
    const bad = (c.badRows || []).filter(
      (r) => !suppliedFor(envState, r.table),
    );
    return bad.length
      ? [
          {
            id: c.id,
            change: c,
            reason: `${bad.length} of the added rows name records that the ${bad[0].table} data doesn't have.`,
            rows: bad,
            table: bad[0].table,
            fix: "rows",
          },
        ]
      : [];
  });
  return { ok: failures.length === 0, failures };
};

/**
 * Save the accepted changes as a new environment version: the source moves to
 * the new revision, its rules change, and what each change touches is
 * re-checked. Rejected changes are noted on the source, not applied.
 */
export const kbRefreshPatch = (env, envState, source, plan, accepted) => {
  const now = new Date().toISOString();
  const taken = plan.changes.filter((c) => accepted.includes(c.id));
  const left = plan.changes.filter((c) => !accepted.includes(c.id));
  const list = envState?.envVersions?.length
    ? envState.envVersions
    : [...environmentVersions(env, envState)].reverse();
  const minted = nextEnvVersion(env, envState, {
    changed: [
      taken.some((c) => c.kind.startsWith("rule")) && "rules",
      taken.some((c) => c.kind.startsWith("row")) && "seed",
    ].filter(Boolean),
    note: `${source.name} rev ${plan.latest}: ${taken.map((c) => c.label.toLowerCase()).join("; ")}`,
    now,
  });
  const hit = [...new Set(taken.flatMap((c) => c.scenarios.map((s) => s.id)))];

  /* Rules: a changed rule keeps its number; an added one takes the next. */
  let added = 0;
  const ruleChanges = taken
    .filter((c) => c.kind.startsWith("rule"))
    .map((c) => {
      const existing = (envState?.ruleChanges || []).find(
        (r) => r.fromSource && r.to === c.fromRule,
      );
      if (c.kind === "rule-changed" && existing)
        return {
          id: existing.id,
          kind: "changed",
          to: c.toRule,
          envVersion: minted.label,
          at: now,
          scenarios: hit,
          fromSource: true,
          sourceName: source.name,
        };
      const id = nextRuleNumber(env, envState, added);
      added += 1;
      return {
        id,
        kind: "added",
        to: c.toRule,
        envVersion: minted.label,
        at: now,
        scenarios: hit,
        fromSource: true,
        sourceName: source.name,
      };
    });

  /* The source's content: passages replaced or added; rows counted. */
  const p = source.parsed || {};
  let parsed = p;
  if (p.kind === "text") {
    const passages = [...p.passages];
    taken
      .filter((c) => c.passage)
      .forEach((c) => {
        const at = passages.findIndex((x) => x.id === c.passage.id);
        if (at >= 0) passages[at] = c.passage;
        else passages.push(c.passage);
      });
    parsed = { ...p, passages };
  } else if (p.kind === "table") {
    const delta = taken.reduce(
      (n, c) =>
        n +
        (c.kind === "row-added"
          ? c.count
          : c.kind === "row-removed"
            ? -c.count
            : 0),
      0,
    );
    parsed = {
      ...p,
      rows: p.rows + delta,
      accepted: p.accepted + delta,
    };
  }
  const note = `Refreshed to rev ${plan.latest} — ${taken.length} of ${plan.changes.length} changes applied`;
  return {
    envVersions: [...list, minted],
    activeEnvVersion: minted.label,
    unsavedEnvChanges: [],
    ruleChanges: [...(envState?.ruleChanges || []), ...ruleChanges],
    sources: (envState.sources || []).map((s) =>
      s.id === source.id
        ? {
            ...s,
            parsed,
            version: s.version + 1,
            kb: {
              ...s.kb,
              revision: plan.latest,
              refreshedAt: now,
              leftOut: left.map((c) => c.label),
            },
            versions: [
              ...s.versions,
              {
                v: s.version + 1,
                at: now,
                rev: plan.latest,
                note: left.length
                  ? `${note}; left out: ${left.map((c) => c.label.toLowerCase()).join(", ")}`
                  : note,
                inVersion: minted.label,
              },
            ],
            snapshot: `data-${minted.label}`,
            inVersion: minted.label,
          }
        : s,
    ),
  };
};
