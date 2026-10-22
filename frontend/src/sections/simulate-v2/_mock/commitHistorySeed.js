/*
  Seed 31: a commit history for two code agents, so choosing a branch and a
  commit (agents/CommitPicker) has something to choose from in the demo.

  Each gets seven pushes to main after the commit it was connected at, and a
  fix waiting as a draft pull request on its own branch — two commits there.
  Added to the agent in Agents and to every environment testing it, so a
  label means the same commit everywhere. Environments whose default is
  production move to the newest commit on main; drafts are never tested.

  Each commit carries the line it added to the prompt, so Compare shows the
  change. Idempotent: an agent that already has more than its first version
  is left alone.
*/
import { envAgentKey } from "./agentRegistry";

const HISTORIES = {
  "repo:https://github.com/acme-inc/pickup-dispatch#app/graph.py:dispatch": {
    main: [
      ["devon", "Quote surge pricing before confirming a ride"],
      ["maya.k", "Add cancel_ride tool with fee check"],
      ["priya.s", "Retry the fare lookup once on timeout"],
      ["devon", "Ask for the drop-off before quoting"],
      ["sam-ops", "Switch the planner step to gpt-4.1"],
      ["maya.k", "Don't promise an ETA the dispatcher hasn't confirmed"],
      ["priya.s", "Hand over to a person after two failed lookups"],
    ],
    /* The fix Improvement 2 opened for ISS-4453 — PR #141. */
    fix: {
      branch: "future-agi/fix-iss-4453",
      issueId: "ISS-4453",
      prNumber: 141,
      commits: [
        ["future-agi[bot]", "Confirm the pickup address before booking"],
        ["future-agi[bot]", "Read the address back in one sentence"],
      ],
    },
  },
  "repo:https://github.com/acme-inc/billing-copilot#app/graph.py:billing": {
    main: [
      ["maya.k", "Look up invoices by email as well as number"],
      ["devon", "Cap goodwill credit at £25 per call"],
      ["priya.s", "Explain proration in plain words"],
      ["sam-ops", "Add update_payment_method tool"],
      ["devon", "Confirm before changing the card on file"],
      ["maya.k", "Route chargebacks straight to a person"],
      ["priya.s", "Shorter answers for balance questions"],
    ],
    /* A teammate's own pull request, waiting for review — not one of ours. */
    fix: {
      branch: "devon/refund-window-check",
      commits: [
        ["devon", "Check the refund window before offering a refund"],
        ["devon", "Say the window end date when declining"],
      ],
    },
  },
};

/* FNV-1a, scrambled — stable seven-digit hashes per agent and commit. */
const hash = (s) => {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i += 1) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
};
const sha = (s) => (Math.imul(hash(s), 2654435761) >>> 0).toString(16).padStart(8, "0").slice(-7);

/* What a commit changed — a line in the prompt, the way a sync records it. */
const changeOf = (message) => [{ kind: "instructions", title: "Instructions edited", detail: `Added: "${message}"`, addendum: `- ${message}` }];

/* The versions to add after `first`, spread between it and two hours ago. */
const historyFor = (key, first) => {
  const h = HISTORIES[key];
  const start = Date.parse(first?.createdAt || first?.connectedAt || "") || Date.now() - 4 * 86400000;
  const end = Date.now() - 2 * 3600000;
  const total = h.main.length + h.fix.commits.length;
  const at = (i) => new Date(start + ((end - start) * (i + 1)) / (total + 1)).toISOString();
  const out = [];
  let prev = first?.label || "v1";
  h.main.forEach(([author, message], i) => {
    const label = `v${i + 2}`;
    const commit = { sha: sha(`${key}:main:${i}`), message, author, branch: "main" };
    out.push({
      id: `agent-${label}`, label, origin: "push", commit, seededCommit: true, changes: changeOf(message),
      note: `Pushed ${commit.sha} by ${author} — ${message}.`,
      reach: "endpoint", createdAt: at(i), basedOnVersion: prev, applied: [],
    });
    prev = label;
  });
  const mainHead = prev;
  h.fix.commits.forEach(([author, message], j) => {
    const label = `v${h.main.length + 2 + j}`;
    const commit = { sha: sha(`${key}:${h.fix.branch}:${j}`), message, author, branch: h.fix.branch };
    out.push({
      id: `agent-${label}`, label, origin: "pr", status: "draft", commit, seededCommit: true, changes: changeOf(message),
      note: `${message} — on ${h.fix.branch}, a draft pull request; not live until merged.`,
      reach: "endpoint", createdAt: at(h.main.length + j), basedOnVersion: j ? prev : mainHead, applied: [],
    });
    prev = label;
  });
  return out;
};

export const withCommitHistory = (state) => {
  if (!state?.connectedAgents) return state;
  const added = {};
  const connectedAgents = state.connectedAgents.map((a) => {
    if (!HISTORIES[a.key] || (a.versions || []).length !== 1) return a;
    added[a.key] = historyFor(a.key, a.versions[0]);
    return { ...a, versions: [...a.versions, ...added[a.key]] };
  });
  if (!Object.keys(added).length) return state;
  const byEnv = { ...state.byEnv };
  Object.entries(byEnv).forEach(([id, es]) => {
    const key = es?.agent ? envAgentKey(es.agent, id) : null;
    if (!added[key]) return;
    const have = new Set((es.agentVersions || []).map((v) => v.label));
    const extra = added[key].filter((v) => !have.has(v.label));
    if (!extra.length) return;
    /* Following production moves to main's newest commit; a held default stays. */
    const mainHead = [...extra].reverse().find((v) => v.status !== "draft")?.label;
    byEnv[id] = {
      ...es,
      agentVersions: [...(es.agentVersions || []), ...extra],
      ...(!es.agentVersionPinned && mainHead && { activeAgentVersion: mainHead }),
    };
  });
  return { ...state, connectedAgents, byEnv };
};

/* Seed 32: commits seeded at 31 gain the change they made. */
export const withCommitChanges = (state) => {
  const fill = (list) => (list || []).map((v) => (v.seededCommit && !v.changes?.length && v.commit?.message ? { ...v, changes: changeOf(v.commit.message) } : v));
  return {
    ...state,
    connectedAgents: (state.connectedAgents || []).map((a) => (a.versions?.some((v) => v.seededCommit) ? { ...a, versions: fill(a.versions) } : a)),
    byEnv: Object.fromEntries(Object.entries(state.byEnv || {}).map(([id, es]) => [id, es?.agentVersions?.some((v) => v.seededCommit) ? { ...es, agentVersions: fill(es.agentVersions) } : es])),
  };
};

/*
  Seed 33: the fix branches mean something. Pickup dispatch's is the pull
  request Improvement 2 opened for ISS-4453 (PR #141), so marking it merged
  releases that fix; Billing copilot's is a teammate's own pull request.
  Commits seeded at 31 are renamed onto those branches.
*/
const OLD_BRANCHES = { "future-agi/fix-confirm-pickup-address": true, "future-agi/fix-refund-window-check": true };
export const withCommitTies = (state) => {
  const ties = {};
  Object.entries(HISTORIES).forEach(([key, h]) => {
    let draftOf = null;
    if (h.fix.issueId) {
      Object.entries(state.byEnv || {}).forEach(([envId, es]) => {
        if (draftOf || !es?.agent || envAgentKey(es.agent, envId) !== key) return;
        const opt = (es.optimizations || []).find((o) => o.trigger?.issueId === h.fix.issueId);
        if (opt) draftOf = { optId: opt.id, envId, source: "pr", name: opt.name || null, issueId: h.fix.issueId, prNumber: h.fix.prNumber };
      });
    }
    ties[key] = { branch: h.fix.branch, authors: h.fix.commits.map(([a]) => a), draftOf, prNumber: h.fix.prNumber };
  });
  const fix = (key, list) => {
    const t = ties[key];
    if (!t) return list;
    let j = 0;
    return (list || []).map((v) => {
      if (!v.seededCommit || !v.commit || v.commit.branch === "main") return v;
      if (!OLD_BRANCHES[v.commit.branch] && v.commit.branch !== t.branch) return v;
      const author = t.authors[j] || v.commit.author;
      j += 1;
      return {
        ...v,
        commit: { ...v.commit, branch: t.branch, author },
        ...(t.draftOf && { draftOf: t.draftOf, prNumber: t.prNumber }),
        note: `${v.commit.message} — on ${t.branch}, ${t.prNumber ? `PR #${t.prNumber}` : "a pull request"}; not live until merged.`,
      };
    });
  };
  return {
    ...state,
    connectedAgents: (state.connectedAgents || []).map((a) => (ties[a.key] ? { ...a, versions: fix(a.key, a.versions) } : a)),
    byEnv: Object.fromEntries(Object.entries(state.byEnv || {}).map(([id, es]) => {
      const key = es?.agent ? envAgentKey(es.agent, id) : null;
      return [id, ties[key] ? { ...es, agentVersions: fix(key, es.agentVersions) } : es];
    })),
  };
};
