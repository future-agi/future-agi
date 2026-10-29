/*
  Organising scenarios (roadmap features/scenarios.md): tags, groups and
  grounding serve three different purposes and survive every save.

    tag       describes and filters — risk=duplicate, source=production.
              envState.scenarioTags = [{ id, name, category, color, description }];
              a row carries `tagIds`.
    group     selects existing identities — "Refund release gate", "Smoke".
              envState.scenarioGroups = [{ id, name, description, ids, createdAt }];
              one scenario can be in many groups and is still one identity.
              Deleting a group never deletes its scenarios.
    grounding justifies the expected behaviour — a passage, row or span with
              its version and what it supports (setup, policy or expected
              outcome). A row carries `grounding: [{ id, sourceId, ref,
              excerpt, supports, version, note }]`.

  Plus the scenario's lifecycle actions: duplicate to edit (a new identity
  with a parent link), validate on demand, archive, and promotion to a
  regression or benchmark (which freezes its checks).
*/
import { envChangePatch } from "./versions";
import { proveOnAdd } from "./addScenarios";

export const TAG_CATEGORIES = [
  { id: "risk", label: "Risk" },
  { id: "domain", label: "Domain" },
  { id: "source", label: "Source" },
  { id: "failure", label: "Failure type" },
  { id: "other", label: "Other" },
];
export const TAG_COLORS = [
  "#64748B",
  "#2563EB",
  "#7857FC",
  "#EA580C",
  "#CA8A04",
  "#16A34A",
  "#DC2626",
];
export const SUGGESTED_TAGS = [
  "risk=duplicate",
  "risk=refund",
  "source=production",
  "failure=tool-error",
  "domain=billing",
];

export const SUPPORTS = [
  { id: "setup", label: "Setup" },
  { id: "policy", label: "Policy" },
  { id: "expected", label: "Expected outcome" },
];

const slug = (s) =>
  String(s || "")
    .toLowerCase()
    .replace(/[^a-z0-9=]+/g, "-")
    .replace(/^-+|-+$/g, "");
const now = () => new Date().toISOString();

export const tagsOf = (envState) => envState?.scenarioTags || [];
export const groupsOf = (envState) => envState?.scenarioGroups || [];
export const tagIdsOf = (row) => row?.tagIds || [];
export const groupsOfRow = (envState, id) =>
  groupsOf(envState).filter((g) => g.ids.includes(id));

/** A tag by name — made when it doesn't exist yet. Category from "key=value". */
export const ensureTag = (envState, name) => {
  const existing = tagsOf(envState).find(
    (t) => t.name.toLowerCase() === String(name).trim().toLowerCase(),
  );
  if (existing) return { tag: existing, tags: tagsOf(envState) };
  const key = String(name).split("=")[0];
  const category = TAG_CATEGORIES.some((c) => c.id === key) ? key : "other";
  const tag = {
    id: `tag-${slug(name)}`,
    name: String(name).trim(),
    category,
    color: TAG_COLORS[tagsOf(envState).length % TAG_COLORS.length],
    description: "",
  };
  return { tag, tags: [...tagsOf(envState), tag] };
};

/* Tags and groups are organisation — they change no graded content, so they
   make no environment version and past results stay as they were. */

/** Add (or remove) one tag on these scenarios. */
export const tagPatch = (envState, ids, name, on = true) => {
  const { tag, tags } = ensureTag(envState, name);
  return {
    scenarioTags: tags,
    scenarios: (envState.scenarios || []).map((r) =>
      ids.includes(r.id)
        ? {
            ...r,
            tagIds: on
              ? [...new Set([...tagIdsOf(r), tag.id])]
              : tagIdsOf(r).filter((t) => t !== tag.id),
          }
        : r,
    ),
  };
};

/** Rename, recolour or describe a tag — its identity holds across every view. */
export const editTagPatch = (envState, id, fields) => ({
  scenarioTags: tagsOf(envState).map((t) =>
    t.id === id ? { ...t, ...fields } : t,
  ),
});

/** Delete a tag — it comes off every scenario; scenarios themselves stay. */
export const deleteTagPatch = (envState, id) => ({
  scenarioTags: tagsOf(envState).filter((t) => t.id !== id),
  scenarios: (envState.scenarios || []).map((r) =>
    tagIdsOf(r).includes(id)
      ? { ...r, tagIds: tagIdsOf(r).filter((t) => t !== id) }
      : r,
  ),
});

/** Add scenarios to a group (made when new) — the same identities, not copies. */
export const groupPatch = (envState, ids, name, on = true) => {
  const groups = groupsOf(envState);
  const existing = groups.find(
    (g) => g.name.toLowerCase() === String(name).trim().toLowerCase(),
  );
  const group = existing || {
    id: `grp-${slug(name)}-${Date.now().toString(36)}`,
    name: String(name).trim(),
    description: "",
    ids: [],
    createdAt: now(),
  };
  const next = {
    ...group,
    ids: on
      ? [...new Set([...group.ids, ...ids])]
      : group.ids.filter((x) => !ids.includes(x)),
  };
  return {
    scenarioGroups: existing
      ? groups.map((g) => (g.id === group.id ? next : g))
      : [...groups, next],
  };
};

/** Delete a group — its scenarios stay in the environment and in other groups. */
export const deleteGroupPatch = (envState, id) => ({
  scenarioGroups: groupsOf(envState).filter((g) => g.id !== id),
});

/** One citation on one scenario. */
export const groundPatch = (envState, rowId, cite) => ({
  scenarios: (envState.scenarios || []).map((r) =>
    r.id === rowId
      ? {
          ...r,
          grounding: [
            ...(r.grounding || []),
            { id: `g-${Date.now().toString(36)}`, at: now(), ...cite },
          ],
        }
      : r,
  ),
});
export const ungroundPatch = (envState, rowId, citeId) => ({
  scenarios: (envState.scenarios || []).map((r) =>
    r.id === rowId
      ? { ...r, grounding: (r.grounding || []).filter((g) => g.id !== citeId) }
      : r,
  ),
});

/**
 * A citation whose source moved on since it was cited (Week 2): the source's
 * version now, and what the person can do — keep pinned, re-ground, archive.
 */
export const staleGroundingOf = (row, envState) =>
  (row.grounding || [])
    .map((g) => {
      const src = (envState?.sources || []).find((s) => s.id === g.sourceId);
      return src && g.version && src.version > g.version
        ? { ...g, now: src.version, sourceName: src.name }
        : null;
    })
    .filter(Boolean);

export const keepPinnedGroundPatch = (envState, rowId, citeId) => ({
  scenarios: (envState.scenarios || []).map((r) =>
    r.id === rowId
      ? {
          ...r,
          grounding: (r.grounding || []).map((g) =>
            g.id === citeId ? { ...g, pinned: true } : g,
          ),
        }
      : r,
  ),
});
export const regroundPatch = (envState, rowId, citeId) => {
  const row = (envState.scenarios || []).find((r) => r.id === rowId);
  const cite = row?.grounding?.find((g) => g.id === citeId);
  const src = (envState.sources || []).find((s) => s.id === cite?.sourceId);
  return {
    scenarios: (envState.scenarios || []).map((r) =>
      r.id === rowId
        ? {
            ...r,
            recheck: true,
            grounding: (r.grounding || []).map((g) =>
              g.id === citeId
                ? {
                    ...g,
                    version: src?.version,
                    pinned: false,
                    regroundedAt: now(),
                  }
                : g,
            ),
          }
        : r,
    ),
    ...envChangePatch(
      envState,
      "rules",
      `${row?.title || "A scenario"} re-grounded on ${src?.name} v${src?.version}`,
    ),
  };
};

/** Duplicate to edit: a new identity, a draft, linked to its parent. */
export const duplicatePatch = (envState, rows) => {
  const at = now();
  const copies = rows.map((r) => ({
    ...r,
    id: `${r.id}-copy-${Date.now().toString(36)}`,
    title: `${r.title || r.summary || "Scenario"} (copy)`,
    parentId: r.id,
    proposed: { at, source: "manual" },
    runs: undefined,
    critical: false,
    promoted: null,
  }));
  return { scenarios: [...(envState.scenarios || []), ...copies], copies };
};

/** Validate on demand: the admission gates again, with what each found. */
export const validateRows = (rows, env, envLabel) =>
  rows.map((r) => ({
    id: r.id,
    title: r.title || r.summary || r.id,
    result: proveOnAdd(r, env, envLabel),
  }));

export const validatePatch = (envState, results) => {
  const byId = new Map(results.map((x) => [x.id, x.result]));
  return {
    scenarios: (envState.scenarios || []).map((r) =>
      byId.has(r.id)
        ? {
            ...r,
            validatedAt: now(),
            ...(byId.get(r.id).pass
              ? { blocked: null }
              : {
                  blocked: {
                    gate: byId.get(r.id).failed.id,
                    reason: byId.get(r.id).failed.reason,
                  },
                }),
          }
        : r,
    ),
  };
};

/** Archive: out of new default selections, kept in history and past runs. */
export const archivePatch = (envState, ids) => ({
  scenarios: (envState.scenarios || []).map((r) =>
    ids.includes(r.id) ? { ...r, archived: true, archivedAt: now() } : r,
  ),
  ...envChangePatch(
    envState,
    "scenarios",
    `Archived ${ids.length} scenario${ids.length === 1 ? "" : "s"}`,
  ),
});

/**
 * Promote: a regression freezes the reviewed case and its checks (and makes
 * it must-pass); a benchmark also fixes its suite and grading policy.
 */
export const promotePatch = (envState, ids, kind = "regression") => ({
  scenarios: (envState.scenarios || []).map((r) =>
    ids.includes(r.id)
      ? { ...r, critical: true, promoted: { kind, at: now() } }
      : r,
  ),
  ...envChangePatch(envState, "scenarios", `Promoted ${ids.length} to ${kind}`),
});

/** An export of these scenarios — definitions only, never evaluator-only references. */
export const exportScenarios = (rows) =>
  JSON.stringify(
    rows.map((r) => ({
      id: r.id,
      title: r.title,
      situation: r.task || r.situation,
      expected: r.expected,
      tags: r.tagIds || [],
      grounding: (r.grounding || []).map((g) => ({
        ref: g.ref,
        supports: g.supports,
      })),
    })),
    null,
    2,
  );

/* The commands a developer would type — roadmap CLI names. */
export const scenarioCli = {
  create: (env) =>
    `agent-learn scenario create --environment ${env?.id || "<env>"} --file ./scenario.yaml`,
  tag: (ids, tag) =>
    `agent-learn scenario tag ${ids.slice(0, 2).join(" ")}${ids.length > 2 ? " …" : ""} --add ${tag || "risk=duplicate"}`,
  group: (ids, group) =>
    `agent-learn scenario group add "${group || "Refund release gate"}" ${ids.slice(0, 2).join(" ")}${ids.length > 2 ? " …" : ""}`,
  ground: (id) =>
    `agent-learn scenario ground ${id} --source <source-ref> --supports expected`,
  validate: (ids) =>
    `agent-learn scenario validate ${ids.slice(0, 2).join(" ")}${ids.length > 2 ? " …" : ""}`,
  duplicate: (id) => `agent-learn scenario duplicate ${id}`,
  promote: (ids, kind = "regression") =>
    `agent-learn scenario promote ${ids.slice(0, 2).join(" ")}${ids.length > 2 ? " …" : ""} --to ${kind}`,
  export: (env) =>
    `agent-learn scenario export --environment ${env?.id || "<env>"} --destination ./scenarios.json`,
};

/**
 * Running a group (roadmap scenarios.md: "Selecting a group for a run
 * resolves its current eligible scenarios. Invalid, archived or stale cases
 * are shown separately … they do not disappear from the count").
 *   { run: [ids], left: [{ id, title, why }] }
 * `stateOf` (the Scenarios tab's) adds what the row alone can't say —
 * outdated or stale.
 */
export const groupRunPlanOf = (group, rows, stateOf) => {
  const byId = new Map((rows || []).map((r) => [r.id, r]));
  const run = [];
  const left = [];
  (group?.ids || []).forEach((id) => {
    const r = byId.get(id);
    if (!r)
      return left.push({
        id,
        title: "A deleted scenario",
        why: "No longer in this environment",
      });
    const title = r.title || r.summary || r.name || id;
    if (r.archived) return left.push({ id, title, why: "Archived" });
    if (r.blocked)
      return left.push({ id, title, why: `Blocked — ${r.blocked.reason}` });
    if (r.proposed)
      return left.push({
        id,
        title,
        why: "Still a proposal — add it to the set first",
      });
    const st = stateOf ? stateOf(r) : null;
    if (st && st.canRun === false)
      return left.push({
        id,
        title,
        why: `${st.label}${st.why ? ` — ${st.why}` : ""}`,
      });
    return run.push(id);
  });
  return { run, left };
};
