/*
  Where each version stands on the way to production. A version an
  improvement made is a draft until someone marks it released — the PRD's
  "nothing ships silently". Read from the environments that test the agent:
  which hand-off landed as which version, and what each one released.

  A Draft is recorded the moment a proposal leaves Future AGI — a draft PR
  opened, a platform draft (Vapi copy, Retell draft) made, a Future AGI agent
  or prompt version created, a patch exported — so the agent's history shows
  it straight away. It carries `status: "draft"` and `draftOf` (the
  improvement, how it shipped). When the change lands (the PR merges, the
  platform draft goes live, someone marks it released) that same version is
  the one released; nothing is minted a second time.
*/
import { improvementRunName } from "../_mock/optimizer";
import { releasePatch } from "../_mock/releaseFix";
import { agentVersions, majorOf, nextAgentVersion, versionNumber } from "../_mock/versions";

/** How a draft left Future AGI, as the version history says it. */
export const DRAFT_SOURCE_LABEL = {
  pr: "Draft PR",
  "platform-draft": "Platform draft",
  builtin: "Future AGI version",
  patch: "Patch exported",
  upload: "Patch for your upload",
};

/** The Draft version a proposal recorded, if it recorded one. */
export const draftVersionOf = (envState, optId) => (optId
  ? agentVersions(envState).find((v) => v.draftOf?.optId === optId) || null
  : null);

/**
 * A Draft agent version for a proposal, numbered past every version this
 * environment knows. `source` is one of DRAFT_SOURCE_LABEL's keys.
 */
export const draftVersionRecord = (envState, {
  optId, envId, source, name, issueId, where, prNumber, note, applied = [], fromRunId = null, basedOnVersion = null, at,
}) => ({
  ...nextAgentVersion(envState, {
    note: note || `Draft from ${name || "an improvement"}${where ? ` · ${where}` : ""} — not live until released.`,
    applied,
    fromRunId,
    basedOnVersion,
    now: at,
  }),
  status: "draft",
  origin: source,
  draftOf: {
    optId, envId: envId || null, source, name: name || null,
    ...(issueId && { issueId }),
    ...(where && { where }),
    ...(prNumber != null && { prNumber }),
  },
});

/* A draft's note says it isn't live yet; once released, that part is stale.
   `released` defaults to the version's own status. */
export const releasedNote = (v, released = v?.status !== "draft") => (released
  ? String(v.note).replace(/^Draft from /, "From ").replace(/ — not live until released\.$/, ".")
  : v.note);

export const versionReleaseOf = (agent, state) => {
  const out = {};
  (agent?.usedIn || []).forEach((u) => {
    const es = state?.byEnv?.[u.envId];
    if (!es) return;
    const current = es.releases?.[0]?.version || null;
    const releasedLabel = (label) => (es.releases || []).some((r) => r.version === label);
    Object.entries(es.handoffLandings || {}).forEach(([key, label]) => {
      const record = (es.optimizations || []).find((o) => o.id === key);
      out[label] = {
        ...out[label],
        from: record ? improvementRunName(es, record) : "Debug failures",
        released: !!out[label]?.released || releasedLabel(label),
      };
    });
    /* Drafts recorded at proposal time — a draft until it's released. */
    agentVersions(es).filter((v) => v.draftOf).forEach((v) => {
      const record = (es.optimizations || []).find((o) => o.id === v.draftOf.optId);
      out[v.label] = {
        ...out[v.label],
        from: out[v.label]?.from || v.draftOf.name || (record ? improvementRunName(es, record) : "Debug failures"),
        via: DRAFT_SOURCE_LABEL[v.draftOf.source] || null,
        released: !!out[v.label]?.released || v.status === "released" || releasedLabel(v.label),
      };
    });
    if (current) out[current] = { ...out[current], released: true, live: true };
  });
  return out;
};

/*
  An improvement's draft marked released is that improvement's release,
  recorded in the environment where it was verified: it becomes the new
  baseline, the Error Feed watch starts, and every environment testing the
  agent learns it (store patchEnvState). { envId, patch } — or null when
  the draft has no home environment, or it's already released there.
*/
export const draftReleaseOf = (agent, state, label) => {
  const draftOf = (agent?.versions || []).find((v) => v.label === label)?.draftOf;
  const home = draftOf?.envId ? state?.byEnv?.[draftOf.envId] : null;
  if (!home || (home.releases || []).some((r) => r.version === label)) return null;
  return { envId: draftOf.envId, patch: releasePatch(home, { version: label, optId: draftOf.optId }).patch };
};

/* Where a draft goes live, by how it left Future AGI — said next to Mark released. */
export const RELEASE_WHEN = {
  pr: "once the PR is merged into your release branch",
  "platform-draft": "once the draft is live on the platform",
  patch: "once the patch is applied and deployed",
  upload: "once the patched code is uploaded",
};

/*
  Roll back = a NEW version that holds an older one's snapshot — never a
  rewrite of history. It's a draft until released, like any proposal: what's
  in production doesn't change until someone releases it. What it points at
  (commit, bundle, Future AGI prompts, tools) is the older version's; the
  definition reads through `rollbackOf` (agentRegistry's agentDefinitionOf).
*/
const POINTERS = ["commit", "bundle", "fagiPrompts", "tools"];

/** The version record "Roll back to {target}" mints. `toolsOf(label)` gives a version's tools, when known. */
export const rollbackVersionRecord = (agent, targetLabel, { toolsOf, at = new Date().toISOString() } = {}) => {
  const versions = agent?.versions || [];
  const n = Math.max(0, ...versions.map((v) => majorOf(v.label))) + 1;
  /* Each pointer as it stood at the target — set there or on a version before it. */
  const upTo = versions.filter((v) => versionNumber(v.label) <= versionNumber(targetLabel)).reverse();
  const pointers = Object.fromEntries(POINTERS
    .map((k) => [k, upTo.find((v) => v[k] != null)?.[k]])
    .filter(([, val]) => val != null));
  const tools = toolsOf?.(targetLabel);
  return {
    ...pointers,
    ...(tools?.length && { tools }),
    id: `agent-v${n}`,
    label: `v${n}`,
    note: `Rolled back to ${targetLabel}`,
    origin: "rollback",
    rollbackOf: targetLabel,
    status: "draft",
    applied: [],
    createdAt: at,
    basedOnVersion: versions[versions.length - 1]?.label || targetLabel,
  };
};
