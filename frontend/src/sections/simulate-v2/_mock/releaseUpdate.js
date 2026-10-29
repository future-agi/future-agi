/*
  Taking a world's newer release (roadmap catalogue.md "Update and manage
  copies"): never automatic. The review separates what the release changes,
  what you edited (kept — a source change that touches it is skipped and
  listed), and what's unchanged. Applying saves a new environment version and
  flags the scenarios that need re-checking; runs before it stay pinned to
  the version they ran on. Skip dismisses the release; it stays reviewable.
*/
import { rulesOf, ruleOfScenario } from "./envModel";
import { environmentVersions, nextEnvVersion } from "./versions";

/** The review of `update` against this environment: each change's fate, your edits, what's unchanged, what to re-check. */
export const releaseReviewOf = (env, envState, update) => {
  const rules = rulesOf(env, envState);
  const items = update.changes.map((c) => {
    if (c.area === "rules" && c.replaces) {
      const r = rules.find((x) => x.original === c.replaces);
      if (!r) return { ...c, status: "skipped", note: "You removed this rule — the release's change to it is skipped." };
      if (r.changedIn) return { ...c, status: "skipped", ruleId: r.id, note: `You edited this rule in ${r.changedIn} — your edit is kept; the release's change is skipped.` };
      return { ...c, status: "applies", ruleId: r.id };
    }
    return { ...c, status: "applies" };
  });
  const changedRuleIds = new Set(items.filter((i) => i.status === "applies" && i.ruleId).map((i) => i.ruleId));
  const recheck = (envState?.scenarios || [])
    .filter((s) => {
      const r = ruleOfScenario(s, rules);
      return r && changedRuleIds.has(r.id);
    })
    .map((s) => s.id);
  const yourEdits = [
    ...environmentVersions(env, envState)
      .filter((v) => v.label !== "v1" && v.note)
      .map((v) => `${v.label} · ${v.note}`),
    ...rules.filter((r) => r.changedIn && !r.original).map((r) => `Rule added in ${r.changedIn}: ${r.text}`),
  ];
  return {
    items,
    yourEdits,
    recheck,
    unchanged: {
      rules: rules.length - changedRuleIds.size,
      tools: (env?.tools || []).length - items.filter((i) => i.area === "tools").length,
    },
  };
};

/** What applying the update writes: the env's release, the rule changes, a new version, and the scenarios to re-check. */
export const applyReleasePatch = (env, envState, update, review) => {
  const v = nextEnvVersion(env, envState, {
    changed: ["release", ...new Set(review.items.filter((i) => i.status === "applies").map((i) => i.area))],
    note: `Took release ${update.to} — ${update.notes}`,
  });
  return {
    envPatch: { release: update.to },
    statePatch: {
      envVersions: [...(envState?.envVersions || []), v],
      activeEnvVersion: v.label,
      unsavedEnvChanges: [],
      ruleChanges: [
        ...(envState?.ruleChanges || []),
        ...review.items
          .filter((i) => i.status === "applies" && i.ruleId)
          .map((i) => ({ kind: "changed", id: i.ruleId, to: i.text, envVersion: v.label, at: new Date().toISOString(), by: "release" })),
      ],
      releaseLog: [...(envState?.releaseLog || []), { version: update.to, action: "applied", at: new Date().toISOString(), envVersion: v.label, recheck: review.recheck }],
    },
    label: v.label,
  };
};

/** What skipping writes: the release is dismissed but stays reviewable. */
export const skipReleasePatch = (envState, update) => ({
  skippedReleases: [...new Set([...(envState?.skippedReleases || []), update.to])],
  releaseLog: [...(envState?.releaseLog || []), { version: update.to, action: "skipped", at: new Date().toISOString() }],
});
