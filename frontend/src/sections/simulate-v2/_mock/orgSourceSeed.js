/*
  Sources a seeded environment already holds, so the Sources panel has
  something to show on first open: a Knowledge base policy one revision
  behind, an order export one revision behind (its refresh fails validation
  until the missing customers are dealt with), a customers file, a Knowledge
  base file whose access was revoked, a transcript still waiting on a
  personal-data choice, and an archived export that can be restored.

  The stored copy of the prototype's state predates these, so they are added
  once, the first time the environment's Sources panel opens.
*/
import { ORG_SOURCES } from "./kbCatalog";
import { readFile, sourceFromOrg, nextRuleNumber } from "./orgData";

const daysAgo = (n) => new Date(Date.now() - n * 86400000).toISOString();

const SEEDED = {
  "env-refunds-desk": {
    policy: { rev: 2, inVersion: "v2" },
    orders: { rev: 6, inVersion: "v3" },
    customers: { inVersion: "v3" },
    macros: { rev: 4, inVersion: "v3" },
  },
};

const kbFile = (id) => ORG_SOURCES.find((o) => o.id === id);

/* A Knowledge base file as it was copied: pinned at `rev`, in a version. */
const pinned = (fileId, rev, inVersion, id, extra = {}) => {
  const s = sourceFromOrg({ ...kbFile(fileId), revision: rev });
  const at = daysAgo(inVersion === "v2" ? 11 : 2);
  return {
    ...s,
    id,
    addedAt: at,
    inEnv: true,
    snapshot: `data-${inVersion}`,
    inVersion,
    personalChoice: s.parsed?.columns?.some((c) => c.sensitive)
      ? "replace"
      : undefined,
    kb: { ...s.kb, pickedAt: at },
    versions: [
      {
        v: 1,
        at,
        rev,
        note: `Copied from the Knowledge base · ${s.kb.collection} · rev ${rev}`,
        inVersion,
      },
    ],
    ...extra,
  };
};

/** The patch adding an environment's seeded sources, or null when it has none to add. */
export const seedSourcesPatch = (env, envState) => {
  const spec = SEEDED[env?.id];
  if (!spec || envState?.sourcesSeeded || (envState?.sources || []).length)
    return null;

  const policy = pinned(
    "org-refund-policy",
    spec.policy.rev,
    spec.policy.inVersion,
    "src-refund-policy-2026",
  );
  const orders = pinned(
    "org-orders-q3",
    spec.orders.rev,
    spec.orders.inVersion,
    "src-orders-q3-sample",
  );
  const customers = {
    ...readFile({ name: "customers.csv", size: 168000 }),
    id: "src-customers",
    addedAt: daysAgo(2),
    inEnv: true,
    snapshot: `data-${spec.customers.inVersion}`,
    inVersion: spec.customers.inVersion,
    personalChoice: "replace",
    versions: [
      {
        v: 1,
        at: daysAgo(2),
        note: "Uploaded",
        inVersion: spec.customers.inVersion,
      },
    ],
  };
  /* Access was taken away after it was copied: the pinned copy stays. */
  const macros = pinned(
    "org-macros",
    spec.macros.rev,
    spec.macros.inVersion,
    "src-support-macros",
    {
      revoked: { at: daysAgo(1), by: "Support team", requested: false },
    },
  );
  /* Read, but not in the world yet: the personal-data choice is still open. */
  const transcripts = {
    ...readFile({ name: "chat-transcripts-sept.pdf", size: 220000 }),
    id: "src-chat-transcripts-sept",
    addedAt: daysAgo(0),
    visibility: "agent",
    addedFor: "evidence",
    suggestionsPending: true,
    versions: [{ v: 1, at: daysAgo(0), note: "Uploaded" }],
  };
  const archived = {
    ...readFile({ name: "returns-2025-archive.csv", size: 90000 }),
    id: "src-returns-2025-archive",
    addedAt: daysAgo(20),
    inEnv: true,
    archived: true,
    snapshot: "data-v2",
    inVersion: "v2",
    personalChoice: "none",
    versions: [{ v: 1, at: daysAgo(20), note: "Uploaded", inVersion: "v2" }],
  };

  const rule = (text, n) => ({
    id: nextRuleNumber(env, envState, n),
    kind: "added",
    to: text,
    envVersion: spec.policy.inVersion,
    at: daysAgo(11),
    scenarios: [],
    fromSource: true,
    sourceName: policy.name,
  });
  const rules = policy.parsed.passages.filter((p) => p.rule);

  return {
    sourcesSeeded: true,
    sources: [policy, orders, customers, macros, transcripts, archived],
    ruleChanges: [
      ...(envState?.ruleChanges || []),
      ...rules.map((p, n) => rule(p.rule, n)),
    ],
    dataImports: [
      ...(envState?.dataImports || []),
      ...[orders, customers].map((s) => ({
        name: s.parsed.table,
        rows: s.parsed.accepted,
        cols: s.parsed.columns.map((c) => [c.name, c.type, c.key]),
        sourceId: s.id,
        envVersion: s.inVersion,
        note: "imported",
      })),
    ],
    dataMode: "private-sample",
  };
};
