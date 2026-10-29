/*
  Files and organization data (roadmap features/data.md): an organization's
  policies and records become a versioned test world, with each participant
  — the agent, the simulated caller, the evaluator — seeing only what it
  should.

  A source is one attached file (or a workspace source you have access to):
    { id, name, ext, sizeKb, role, visibility, status, statusNote, version,
      versions: [{ v, at, note }], owner, addedAt, inEnv, snapshot,
      parsed: { kind: "text", passages } | { kind: "table", table, columns,
                rows, accepted, rejected: [{ row, reason }] },
      joins: [{ field, to, missing: n, rows: [..], resolution }],
      privacy: { [column]: "retain" | "mask" | "replace" | "omit" } }
  kept on the environment as `envState.sources`. Nothing here parses a real
  file: the content is read from the file's name, the way the rest of the
  prototype's mocks are, so the same names give the same results.

  Week 1: attach in builder chat (progress, cancel, retry), roles, mapping and
  joins, sensitive fields, who-sees-what, the builder's cited proposal, a
  saved snapshot. Week 2: refresh a source with an impact review (keep pinned
  or re-ground), export only what's permitted.
*/
import {
  envChangePatch,
  nextEnvVersion,
  environmentVersions,
} from "./versions";
import { currentUser } from "./scenarioProvenance";

/* ── what a file is for, and who can read it ────────────────────────────── */

export const ROLES = [
  {
    id: "policy",
    label: "Policy / reference",
    note: "Rules the agent must follow",
  },
  { id: "seed", label: "Seed records", note: "Rows the world starts with" },
  {
    id: "task-input",
    label: "Task input",
    note: "What a caller brings to the conversation",
  },
  {
    id: "example",
    label: "Example conversation",
    note: "How a real exchange went",
  },
  {
    id: "evaluation-reference",
    label: "Hidden evaluation reference",
    note: "Expected answers — the grader's only",
  },
];
export const roleLabel = (id) => ROLES.find((r) => r.id === id)?.label || id;

export const VISIBILITY = [
  { id: "agent", label: "Agent-visible", note: "In the agent's context" },
  {
    id: "tool",
    label: "Tool-only",
    note: "The agent reaches it only through the world's tools",
  },
  {
    id: "evaluator",
    label: "Evaluator-only",
    note: "Never reaches the agent or the caller",
  },
];
export const visibilityLabel = (id) =>
  VISIBILITY.find((v) => v.id === id)?.label || id;

/* A role's default visibility — shown before anything is accepted. */
export const defaultVisibility = (role) =>
  role === "evaluation-reference"
    ? "evaluator"
    : role === "task-input" || role === "example"
      ? "agent"
      : "tool";

export const PRIVACY = [
  { id: "retain", label: "Keep privately" },
  { id: "mask", label: "Mask" },
  { id: "replace", label: "Replace consistently" },
  { id: "omit", label: "Leave out" },
];

export const SUPPORTED = ["pdf", "txt", "md", "csv", "json", "jsonl", "tsv"];
export const SUPPORTED_LINE =
  "PDF, text, Markdown, CSV and JSON · up to 25 MB and 10 files at a time";

const extOf = (name) =>
  String(name || "")
    .split(".")
    .pop()
    .toLowerCase();
const isTable = (ext) => ["csv", "tsv", "json", "jsonl"].includes(ext);

/* ── reading a file (mock) ──────────────────────────────────────────────── */

const POLICY_PASSAGES = (days) => [
  {
    id: "p1",
    page: 1,
    text: `Items can be returned within ${days} days of delivery for a full refund.`,
    rule: `Refunds are allowed within ${days} days of delivery.`,
  },
  {
    id: "p2",
    page: 1,
    text: "A refund goes back to the original payment method.",
  },
  {
    id: "p3",
    page: 2,
    text: "Only one refund may exist per order. A retried request reuses the refund already issued.",
    rule: "Only one refund per order — a retry reuses the existing refund.",
  },
  {
    id: "p4",
    page: 2,
    text: "Final-sale items cannot be returned or refunded.",
  },
];

const TABLES = [
  {
    match: /order/,
    table: "orders",
    columns: [
      { name: "order_id", type: "string", key: true },
      { name: "customer_id", type: "string", ref: "customers.id" },
      { name: "delivered_at", type: "date" },
      { name: "amount_cents", type: "integer" },
      { name: "email", type: "string", sensitive: true },
    ],
    rows: 500,
    rejected: [
      { row: 104, reason: "customer_id C-881 isn't in customers" },
      { row: 377, reason: "customer_id C-902 isn't in customers" },
    ],
    sample: {
      order_id: "O-104",
      customer_id: "C-881",
      delivered_at: "2026-09-28",
      amount_cents: 4599,
      email: "dana.liu@example.com",
    },
  },
  {
    match: /customer|account|user/,
    table: "customers",
    columns: [
      { name: "id", type: "string", key: true },
      { name: "name", type: "string", sensitive: true },
      { name: "email", type: "string", sensitive: true },
      { name: "tier", type: "string" },
    ],
    rows: 200,
    rejected: [],
    sample: {
      id: "C-881",
      name: "Dana Liu",
      email: "dana.liu@example.com",
      tier: "gold",
    },
  },
  {
    match: /refund/,
    table: "refunds",
    columns: [
      { name: "refund_id", type: "string", key: true },
      { name: "order_id", type: "string", ref: "orders.order_id" },
      { name: "amount_cents", type: "integer" },
      { name: "status", type: "string" },
    ],
    rows: 85,
    rejected: [],
    sample: {
      refund_id: "RF-7",
      order_id: "O-104",
      amount_cents: 4599,
      status: "committed",
    },
  },
];

/** What reading the file found — deterministic from its name. */
export const readFile = (file) => {
  const name = file?.name || "file";
  const lower = name.toLowerCase();
  const ext = extOf(name);
  const base = {
    id: `src-${lower.replace(/[^a-z0-9]+/g, "-")}-${Date.now().toString(36)}`,
    name,
    ext,
    sizeKb: Math.max(1, Math.round((file?.size || 48000) / 1024)),
    owner: currentUser()?.name || "You",
    addedAt: new Date().toISOString(),
    version: 1,
    versions: [
      { v: 1, at: new Date().toISOString(), note: "Attached in builder chat" },
    ],
    inEnv: false,
    privacy: {},
  };
  if (!SUPPORTED.includes(ext))
    return {
      ...base,
      role: null,
      visibility: null,
      status: "unsupported",
      statusNote: `Can't read .${ext} — ${SUPPORTED_LINE}.`,
    };
  if (/scan/.test(lower))
    return {
      ...base,
      role: "policy",
      visibility: "tool",
      status: "unreadable",
      statusNote:
        "Scanned pages with no text — upload a text PDF or paste the policy into chat.",
    };
  if (/expected|outcome|answer|golden/.test(lower))
    return {
      ...base,
      role: "evaluation-reference",
      visibility: "evaluator",
      status: "ready",
      parsed: {
        kind: "text",
        passages: [
          {
            id: "p1",
            page: 1,
            text: "O-104 retry: reuse RF-7; never issue a second refund.",
          },
          {
            id: "p2",
            page: 1,
            text: "Day-31 return: refuse, explain the 30-day window, offer store credit.",
          },
        ],
      },
    };
  if (isTable(ext)) {
    const t = TABLES.find((x) => x.match.test(lower)) || {
      table: lower.replace(/\.[^.]+$/, "").replace(/[^a-z0-9]+/g, "_"),
      columns: [
        { name: "id", type: "string", key: true },
        { name: "value", type: "string" },
      ],
      rows: 120,
      rejected: [],
      sample: { id: "1", value: "…" },
    };
    const privacy = Object.fromEntries(
      t.columns.filter((c) => c.sensitive).map((c) => [c.name, "replace"]),
    );
    return {
      ...base,
      role: "seed",
      visibility: "tool",
      status: t.rejected.length ? "needs-attention" : "ready",
      statusNote: t.rejected.length
        ? `${t.rejected.length} of ${t.rows} rows rejected`
        : null,
      parsed: {
        kind: "table",
        table: t.table,
        columns: t.columns,
        rows: t.rows,
        accepted: t.rows - t.rejected.length,
        rejected: t.rejected,
        sample: t.sample,
        format:
          ext === "csv"
            ? "CSV · comma · header row · UTF-8"
            : "JSON · array of objects",
      },
      privacy,
    };
  }
  const days = (lower.match(/(\d{2})/) || [])[1] || "30";
  return {
    ...base,
    role: /chat|conversation|transcript/.test(lower) ? "example" : "policy",
    visibility: "tool",
    status: "ready",
    parsed: { kind: "text", pages: 2, passages: POLICY_PASSAGES(days) },
    effectiveFrom: "2026-01-01",
  };
};

/* Joins a table needs and whether the table it points at is here. */
export const joinsOf = (source, sources) => {
  if (source?.parsed?.kind !== "table") return [];
  const tables = new Set(
    sources
      .filter((s) => s.parsed?.kind === "table")
      .map((s) => s.parsed.table),
  );
  return source.parsed.columns
    .filter((c) => c.ref)
    .map((c) => {
      const to = c.ref.split(".")[0];
      const present = tables.has(to);
      const resolution = source.joins?.[c.name] || null;
      return {
        field: c.name,
        to: c.ref,
        present,
        /* With the table here, only the rows that miss it; without it, every row. */
        missing: present ? source.parsed.rejected.length : source.parsed.rows,
        rows: present ? source.parsed.rejected : [],
        resolution,
        blocking:
          !resolution && (!present || source.parsed.rejected.length > 0),
      };
    });
};

/* ── who sees what ──────────────────────────────────────────────────────── */

/** Each participant's effective view of the sources: { agent, caller, evaluator } → [{ source, how }]. */
export const participantViewsOf = (sources) => {
  const live = sources.filter(
    (s) => s.status !== "unsupported" && s.status !== "no-access",
  );
  const view = { agent: [], caller: [], evaluator: [] };
  live.forEach((s) => {
    if (s.visibility === "agent")
      view.agent.push({ source: s, how: "In its context" });
    if (s.visibility === "tool")
      view.agent.push({ source: s, how: "Only through the world's tools" });
    if (s.role === "task-input")
      view.caller.push({ source: s, how: "What the caller brings" });
    view.evaluator.push({
      source: s,
      how:
        s.visibility === "evaluator"
          ? "Only the evaluator"
          : "To grade against",
    });
  });
  return view;
};

/* ── the builder's proposal ─────────────────────────────────────────────── */

/**
 * What the builder proposes from these sources, each change citing the
 * passage or row it came from:
 *   { items: [{ id, kind, title, detail, cite, inferred?, blocking?, rule?, table?, scenario? }] }
 * kind: rule | table | scenario | missing | hidden | conflict | unreadable.
 */
export const dataProposalOf = (sources) => {
  const items = [];
  const policies = sources.filter(
    (s) => s.role === "policy" && s.status === "ready",
  );
  /* Two policies that set the same rule differently: a choice, never the newest filename. */
  const windows = policies
    .map((s) => ({ s, p: s.parsed.passages.find((x) => x.id === "p1") }))
    .filter((x) => x.p);
  const distinct = [...new Set(windows.map((w) => w.p.rule))];
  if (distinct.length > 1)
    items.push({
      id: "conflict-window",
      kind: "conflict",
      title: "Two policies set the return window differently",
      detail: windows
        .map((w) => `${w.s.name} p.${w.p.page}: “${w.p.text}”`)
        .join(" · "),
      options: windows.map((w) => ({
        sourceId: w.s.id,
        label: w.s.name,
        rule: w.p.rule,
      })),
      blocking: true,
    });
  policies.forEach((s) =>
    s.parsed.passages
      .filter((p) => p.rule && !(distinct.length > 1 && p.id === "p1"))
      .forEach((p) =>
        items.push({
          id: `rule-${s.id}-${p.id}`,
          kind: "rule",
          title: `Rule: ${p.rule}`,
          cite: {
            sourceId: s.id,
            ref: `${s.name} · p.${p.page}`,
            excerpt: p.text,
          },
          rule: p.rule,
        }),
      ),
  );
  const tables = sources.filter(
    (s) => s.parsed?.kind === "table" && s.role === "seed",
  );
  tables.forEach((s) => {
    const t = s.parsed;
    items.push({
      id: `table-${s.id}`,
      kind: "table",
      title: `Seed table ${t.table}: ${t.accepted.toLocaleString()} rows${t.rejected.length ? `, ${t.rejected.length} rejected` : ""}`,
      cite: {
        sourceId: s.id,
        ref: s.name,
        excerpt: t.columns.map((c) => c.name).join(", "),
      },
      table: {
        name: t.table,
        rows: t.accepted,
        cols: t.columns.map((c) => [c.name, c.type, c.key]),
      },
    });
    joinsOf(s, sources)
      .filter((j) => j.blocking)
      .forEach((j) =>
        items.push({
          id: `missing-${s.id}-${j.field}`,
          kind: "missing",
          title: j.present
            ? `${j.missing} rows of ${t.table} point at missing ${j.to.split(".")[0]}`
            : `Missing table: ${j.to.split(".")[0]}`,
          detail: j.present
            ? j.rows.map((r) => `row ${r.row}: ${r.reason}`).join(" · ")
            : `${t.table}.${j.field} refers to ${j.to}, which isn't attached — every row needs it.`,
          cite: { sourceId: s.id, ref: `${s.name} · ${j.field}` },
          sourceId: s.id,
          field: j.field,
          blocking: true,
        }),
      );
  });
  /* Cases the rules imply — boundaries and the retry — cited to their passage. */
  const window = policies[0]?.parsed.passages.find((p) => p.id === "p1");
  const days = window ? (window.text.match(/(\d+) days/) || [])[1] : null;
  if (window && days)
    items.push({
      id: "scenario-boundary",
      kind: "scenario",
      title: `Scenarios: a return on day ${days} and on day ${Number(days) + 1}`,
      detail: `Day ${days} is refunded; day ${Number(days) + 1} is refused with the window explained.`,
      cite: {
        sourceId: policies[0].id,
        ref: `${policies[0].name} · p.${window.page}`,
        excerpt: window.text,
      },
      inferred: true,
      scenario: [
        {
          t: `Return on day ${days}`,
          k: `A caller returns an item delivered exactly ${days} days ago and asks for a refund.`,
          e: "Agent refunds to the original payment method and confirms the amount.",
        },
        {
          t: `Return on day ${Number(days) + 1}`,
          k: `A caller returns an item delivered ${Number(days) + 1} days ago and insists on a refund.`,
          e: `Agent refuses, explains the ${days}-day window, and offers what policy allows.`,
        },
      ],
    });
  const retry = policies.flatMap((s) =>
    s.parsed.passages.filter((p) => p.id === "p3").map((p) => ({ s, p })),
  )[0];
  const hasRefunds = tables.some((s) => s.parsed.table === "refunds");
  if (retry)
    items.push({
      id: "scenario-retry",
      kind: "scenario",
      title: "Scenario: duplicate refund retry",
      detail: hasRefunds
        ? "Order O-104 already has refund RF-7; the caller asks to retry. One refund must exist at the end."
        : "Needs a refunds table holding an existing refund — attach one, or this case stays blocked.",
      cite: {
        sourceId: retry.s.id,
        ref: `${retry.s.name} · p.${retry.p.page}`,
        excerpt: retry.p.text,
      },
      blocking: !hasRefunds,
      scenario: [
        {
          t: "Duplicate refund retry",
          k: "Order O-104 already has refund RF-7 committed. The caller says the refund failed and asks to try again.",
          e: "Agent finds RF-7, reuses it, and tells the caller the refund already went through — no second refund.",
        },
      ],
    });
  sources
    .filter((s) => s.visibility === "evaluator")
    .forEach((s) =>
      items.push({
        id: `hidden-${s.id}`,
        kind: "hidden",
        title: `${s.name} stays evaluator-only`,
        detail:
          "The agent and the simulated caller can't read it, even though it came through the same chat.",
        cite: { sourceId: s.id, ref: s.name },
      }),
    );
  sources
    .filter((s) => s.status === "unreadable")
    .forEach((s) =>
      items.push({
        id: `unreadable-${s.id}`,
        kind: "unreadable",
        title: `${s.name} couldn't be read`,
        detail: s.statusNote,
        blocking: true,
      }),
    );
  return { items };
};

/* ── accepting it ───────────────────────────────────────────────────────── */

const nextRuleNumber = (env, envState, n) => {
  const ids = [
    ...(env?.rules || []).map((_, i) => i + 1),
    ...(envState?.ruleChanges || []).map(
      (c) => parseInt(String(c.id).replace(/\D/g, ""), 10) || 0,
    ),
  ];
  return `R${Math.max(0, ...ids) + 1 + n}`;
};

/**
 * Accept the picked items in one new environment version: the sources join
 * the environment as one snapshot, rules land on the contract, tables seed
 * the world, and proposed scenarios go to the Scenarios tab as proposals.
 * `scenarioRows(items)` builds those rows (the caller has the env's helpers).
 */
export const acceptDataPatch = (
  env,
  envState,
  proposal,
  picked,
  { conflictPick, scenarioRows } = {},
) => {
  const now = new Date().toISOString();
  const take = proposal.items.filter(
    (i) => picked.includes(i.id) && !i.blocking,
  );
  const list = envState?.envVersions?.length
    ? envState.envVersions
    : [...environmentVersions(env, envState)].reverse();
  const usedSources = new Set(
    take.map((i) => i.cite?.sourceId).filter(Boolean),
  );
  const conflict = proposal.items.find((i) => i.kind === "conflict");
  const chosen =
    conflict && conflictPick
      ? conflict.options.find((o) => o.sourceId === conflictPick)
      : null;
  if (chosen) usedSources.add(chosen.sourceId);
  const rules = [
    ...take.filter((i) => i.kind === "rule").map((i) => i.rule),
    ...(chosen ? [chosen.rule] : []),
  ];
  const tables = take.filter((i) => i.kind === "table");
  const minted = nextEnvVersion(env, envState, {
    changed: [rules.length && "rules", tables.length && "seed"].filter(Boolean),
    note: `From ${[...usedSources]
      .map((id) => (envState.sources || []).find((s) => s.id === id)?.name)
      .filter(Boolean)
      .join(", ")}`,
    now,
  });
  const snapshot = `data-${minted.label}`;
  const ruleChanges = rules.map((text, n) => ({
    id: nextRuleNumber(env, envState, n),
    kind: "added",
    to: text,
    envVersion: minted.label,
    at: now,
    scenarios: [],
    fromSource: true,
  }));
  return {
    envVersions: [...list, minted],
    activeEnvVersion: minted.label,
    unsavedEnvChanges: [],
    ruleChanges: [...(envState?.ruleChanges || []), ...ruleChanges],
    dataImports: [
      ...(envState?.dataImports || []),
      ...tables.map((i) => ({
        ...i.table,
        sourceId: i.cite.sourceId,
        envVersion: minted.label,
        note: "imported",
      })),
    ],
    sources: (envState?.sources || []).map((s) =>
      usedSources.has(s.id) || s.visibility === "evaluator"
        ? { ...s, inEnv: true, snapshot, inVersion: minted.label }
        : s,
    ),
    scenarios: [
      ...(envState?.scenarios || []),
      ...(scenarioRows
        ? scenarioRows(take.filter((i) => i.kind === "scenario"))
        : []),
    ],
    dataMode: tables.length
      ? "private-sample"
      : envState?.dataMode || "synthetic",
    ruleIds: ruleChanges.map((r) => r.id),
  };
};

/* ── after import: usage, refresh, export ───────────────────────────────── */

/** The rules and scenarios that cite a source. */
export const usageOf = (source, envState) => {
  const rules = (envState?.ruleChanges || []).filter(
    (r) =>
      r.fromSource && source.parsed?.passages?.some((p) => p.rule === r.to),
  );
  const scenarios = (envState?.scenarios || []).filter(
    (r) => r.origin === source.name || r.citedSource === source.id,
  );
  const tables = (envState?.dataImports || []).filter(
    (t) => t.sourceId === source.id,
  );
  return { rules, scenarios, tables };
};

/**
 * A replacement file for a source (Week 2): what changed, which rules and
 * scenarios it touches, and what keeping the old one or re-grounding does.
 */
export const refreshPlanOf = (env, envState, source, file) => {
  const next = readFile(file);
  const before = source.parsed?.passages || [];
  const after = next.parsed?.passages || [];
  const changed = after
    .map((p) => ({ p, was: before.find((b) => b.id === p.id) }))
    .filter(({ p, was }) => was && was.text !== p.text)
    .map(({ p, was }) => ({
      id: p.id,
      page: p.page,
      from: was.text,
      to: p.text,
      fromRule: was.rule,
      toRule: p.rule,
    }));
  const words = changed.flatMap((c) => c.from.match(/\d+ days/g) || []);
  const affected = (envState?.scenarios || []).filter(
    (r) =>
      !r.retired &&
      !r.archived &&
      (words.some((w) =>
        `${r.task || ""} ${r.expected || ""} ${r.title || ""}`.includes(
          w.split(" ")[0],
        ),
      ) ||
        /return|refund/i.test(`${r.title || ""} ${r.task || ""}`)),
  );
  const rules = (envState?.ruleChanges || []).filter(
    (r) => r.fromSource && changed.some((c) => c.fromRule === r.to),
  );
  return { next, changed, affected, rules, nextVersion: source.version + 1 };
};

/** Re-ground: the source moves to its new version, its rules change, and the scenarios they touch are re-checked. */
export const regroundPatch = (env, envState, source, plan) => {
  const now = new Date().toISOString();
  const list = envState?.envVersions?.length
    ? envState.envVersions
    : [...environmentVersions(env, envState)].reverse();
  const detail = `${source.name} v${plan.nextVersion}: ${plan.changed.map((c) => c.toRule || c.to).join("; ")}`;
  const minted = nextEnvVersion(env, envState, {
    changed: ["rules", "seed"],
    note: detail,
    now,
  });
  const hit = plan.affected.map((r) => r.id);
  const ruleChanges = plan.rules.map((r) => {
    const c = plan.changed.find((x) => x.fromRule === r.to);
    return {
      id: r.id,
      kind: "changed",
      to: c?.toRule || r.to,
      envVersion: minted.label,
      at: now,
      scenarios: hit,
      fromSource: true,
    };
  });
  return {
    envVersions: [...list, minted],
    activeEnvVersion: minted.label,
    unsavedEnvChanges: [],
    ruleChanges: [...(envState?.ruleChanges || []), ...ruleChanges],
    sources: (envState.sources || []).map((s) =>
      s.id === source.id
        ? {
            ...s,
            parsed: plan.next.parsed,
            version: plan.nextVersion,
            versions: [
              ...s.versions,
              {
                v: plan.nextVersion,
                at: now,
                note: `Replaced with ${plan.next.name}`,
                inVersion: minted.label,
              },
            ],
            snapshot: `data-${minted.label}`,
            inVersion: minted.label,
            pendingRefresh: null,
          }
        : s,
    ),
  };
};

/** Keep pinned: the new file is kept as a draft version the environment doesn't use yet. */
export const keepPinnedPatch = (envState, source, plan) => ({
  sources: (envState.sources || []).map((s) =>
    s.id === source.id
      ? {
          ...s,
          pendingRefresh: {
            v: plan.nextVersion,
            name: plan.next.name,
            at: new Date().toISOString(),
            changed: plan.changed,
          },
        }
      : s,
  ),
});

/** What an export would hold — and what it leaves out, by name only. */
export const exportPreviewOf = (source) => {
  if (source.visibility === "evaluator")
    return {
      allowed: false,
      reason:
        "Evaluator-only references aren't exported — they'd leak the expected answers.",
    };
  const cols = source.parsed?.columns || [];
  const left = cols
    .filter((c) => ["omit"].includes(source.privacy?.[c.name]))
    .map((c) => c.name);
  const masked = cols
    .filter((c) => ["mask", "replace"].includes(source.privacy?.[c.name]))
    .map((c) => c.name);
  return { allowed: true, left, masked, version: source.version };
};

/* Workspace sources someone else owns — added by reference, never copied. */
export const ORG_SOURCES = [
  {
    id: "org-refund-policy",
    name: "Refund policy 2026.pdf",
    owner: "Legal",
    access: true,
    role: "policy",
  },
  {
    id: "org-orders-q3",
    name: "orders-q3-sample.csv",
    owner: "Data team",
    access: true,
    role: "seed",
  },
  {
    id: "org-payroll",
    name: "HR payroll 2026.xlsx",
    owner: "People team",
    access: false,
    role: "evaluation-reference",
  },
];

/** Patch fragment for sources changed by the person (role, visibility, mapping, privacy). */
export const sourceEditPatch = (envState, id, fields, detail) => {
  const s = (envState?.sources || []).find((x) => x.id === id);
  return {
    sources: (envState?.sources || []).map((x) =>
      x.id === id ? { ...x, ...fields } : x,
    ),
    ...(s?.inEnv && detail ? envChangePatch(envState, "seed", detail) : {}),
  };
};

/* The commands a developer would type — roadmap CLI names. */
export const dataCli = {
  import: (env, s) =>
    `agent-learn data import ./${s?.name || "orders.csv"} --environment ${env?.id || "<env>"} --role ${s?.role || "seed"} --visibility ${s?.visibility || "tool"} --preview`,
  show: (s) => `agent-learn data show ${s.id}`,
  refresh: (s) =>
    `agent-learn data refresh ${s.id} --file ./${s.name.replace(/(\.[^.]+)$/, "-v2$1")} --preview`,
  export: (s) =>
    `agent-learn data export ${s.id} --destination ./export --format ${s.parsed?.kind === "table" ? "csv" : "original"}`,
  list: "agent-learn data list",
};
