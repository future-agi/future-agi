/*
  A code agent's versions, with the commit each one is.

  Every agent reads by one scheme: its version number (team feedback, Oct
  2026 — "one version scheme for every agent"). An agent read from a
  repository (GitHub, GitLab, Bitbucket, or an SDK adapter in a repo) also
  has a commit on a branch for every version, so that rides alongside:
  "v5 · main @ 3f9a2c1" — the label people use in their own tools (the way
  Braintrust and Vercel tie runs and deployments to commits).

  Underneath nothing moves: each version keeps its stored label, runs still
  record it, and Compare, history and pass rate key off it. This module only
  says which branch and commit a version is, and how to show it.

  Platform agents (Vapi, Retell, ElevenLabs …), uploads and agents built in
  Future AGI read as the version number alone — they have no commits.
*/
import { definitionOf, envAgentKey, sourceKindOf } from "./agentRegistry";
import { agentVersions } from "./versions";

const GIT_KINDS = ["repo", "framework"];

/** Read from a repository, so its versions are commits on branches. */
export const isGitAgent = (agent) => !!agent && GIT_KINDS.includes(sourceKindOf(agent));

/* FNV-1a with a scramble — the same digits versionPins prints for a commit
   that wasn't recorded, so every screen shows one hash per version. */
const hash = (s) => {
  let h = 0x811c9dc5;
  const str = String(s || "");
  for (let i = 0; i < str.length; i += 1) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
};
const hex = (s, n) => (Math.imul(hash(s), 2654435761) >>> 0).toString(16).padStart(8, "0").slice(-n);

const AUTHORS = ["maya.k", "devon", "priya.s", "sam-ops"];
const slug = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 32) || "change";

/** The repository's release branch — what "in production" is read from. */
export const defaultBranchOf = (agent) => definitionOf(agent).ref || "main";

/* The commit message a version carries, else one read from its note. */
const messageOf = (v) => {
  if (v?.commit?.message && v.origin !== "rollback") return v.commit.message;
  if (v?.draftOf) return `Fix: ${v.draftOf.name || "improvement"}`;
  if (v?.origin === "connect" || v?.origin === "pre-versioning") return "Agent as connected";
  const note = String(v?.note || "").replace(/^Synced from [^—]+— /, "").replace(/^Pushed \S+ by \S+ — /, "");
  const first = note.split(/(?<=\.)\s/)[0].replace(/\.$/, "").trim();
  return first ? first.charAt(0).toUpperCase() + first.slice(1) : "Change";
};

/**
 * Which branch and commit a version is:
 *   { branch, sha, message, author, at, draft, release }
 * A fix opened as a draft pull request lives on its own branch
 * (future-agi/fix-…) until it's merged; everything else is on the release
 * branch.
 */
export const codeRefOf = (agent, v) => {
  if (!v) return null;
  const key = agent?.key || agent?.id || agent?.name || "agent";
  const release = defaultBranchOf(agent);
  const draft = v.status === "draft" || !!v.pendingDraft;
  /* A roll back is a revert: a new commit (it carries the target's pointer,
     not its hash), on its own branch until it's merged. */
  const revert = v.origin === "rollback";
  const target = revert ? (agent?.versions || []).find((x) => x.label === v.rollbackOf) : null;
  const targetSha = target ? codeRefOf(agent, target).sha : null;
  const own = revert ? null : v.commit;
  /* A fix branch's commits, once merged, read as on the release branch. */
  const merged = !!own?.branch && own.branch !== release && !draft;
  const branch = (merged ? release : own?.branch)
    || (draft && v.draftOf ? `future-agi/fix-${slug(v.draftOf.name || v.label)}`
      : draft && revert ? `future-agi/revert-${targetSha || slug(v.rollbackOf)}` : release);
  return {
    branch,
    sha: own?.sha || hex(`${key}:${v.label}:commit`, 7),
    message: revert ? `Revert to ${targetSha || v.rollbackOf || "an earlier commit"}` : messageOf(v),
    author: own?.author || (v.draftOf || revert ? "future-agi[bot]" : AUTHORS[hash(`${key}:${v.label}`) % AUTHORS.length]),
    at: v.createdAt || v.connectedAt || null,
    draft: draft && branch !== release,
    release: branch === release,
  };
};

/** Branches with versions on them, the release branch first: [{ name, count, release }]. */
export const branchesOf = (agent) => {
  const counts = new Map();
  (agent?.versions || []).forEach((v) => {
    const b = codeRefOf(agent, v).branch;
    counts.set(b, (counts.get(b) || 0) + 1);
  });
  const release = defaultBranchOf(agent);
  if (!counts.has(release)) counts.set(release, 0);
  return [...counts.entries()]
    .map(([name, count]) => ({ name, count, release: name === release }))
    .sort((a, b) => (b.release - a.release) || a.name.localeCompare(b.name));
};

/** A branch's commits, newest first: [{ label, ...codeRef }]. */
export const commitsOn = (agent, branch) => [...(agent?.versions || [])]
  .reverse()
  .map((v) => ({ label: v.label, ...codeRefOf(agent, v) }))
  .filter((c) => c.branch === branch);

/**
 * How a version reads anywhere a version is named — for a git agent
 * `{ main: "v5 · main @ 3f9a2c1", short: "v5 · 3f9a2c1", commit: "main @ 3f9a2c1",
 *    sub: "Add order lookup by email", ref }`, else `{ main: "v3", short: "v3" }`.
 * `short` is for tight spots (buttons, chart bars). `agent` may be a
 * registry agent or an environment's copy with `versions` set.
 */
export const versionNameOf = (agent, label) => {
  const plain = { main: label, short: label, commit: null, sub: null };
  if (!isGitAgent(agent) || !label) return plain;
  const v = (agent?.versions || []).find((x) => x.label === label);
  const r = codeRefOf(agent, v);
  if (!r) return plain;
  const commit = `${r.branch} @ ${r.sha}`;
  return { main: `${label} · ${commit}`, short: `${label} · ${r.sha}`, commit, sub: r.message, ref: r };
};

/** "v5 · main @ 3f9a2c1" for a git agent's version; its stored label for any other agent. */
export const refLabelOf = (agent, label) => versionNameOf(agent, label).main;

/**
 * The same, read from an environment: its copy of the agent and the versions
 * it knows, keyed as Agents keys it — so a commit has one hash everywhere.
 */
export const envVersionNameOf = (envState, label, envId) => {
  const a = envState?.agent;
  if (!a || !label) return { main: label, sub: null };
  return versionNameOf({ ...a, key: a.key || envAgentKey(a, envId), versions: agentVersions(envState) }, label);
};

/**
 * The commit that adds traceAI, from the tracing pull request — shown in the
 * commit log but never an agent version: it doesn't change what the agent
 * does, so nothing re-tests it and no version nudge follows. On its own
 * branch while the PR is open, on the release branch once it's merged.
 *   → { sha, message, branch, author, at, merged, pr } | null
 */
export const tracingCommitOf = (agent) => {
  const pr = agent?.values?.tracingPr;
  if (!pr) return null;
  const merged = !!pr.mergedAt;
  return {
    sha: hex(`${agent?.key || agent?.name}:tracing:${pr.number}`, 7),
    message: pr.title || "Add traceAI tracing",
    branch: merged ? defaultBranchOf(agent) : pr.branch,
    author: "future-agi[bot]",
    at: pr.mergedAt || pr.openedAt || null,
    merged,
    pr: pr.number,
  };
};
