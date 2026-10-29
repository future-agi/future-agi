/*
  Sandbox preview (roadmap builder.md "Sandbox preview and publishing"):
  start the world on a chosen starting state, act as the agent — call a
  tool, or say something to the caller — and watch the state, files, logs,
  network and resources. It is a preview: nothing here is a run, scores
  anything, or touches completed runs. Reset puts the starting state back.

  The preview lives in memory for the session (keyed by environment), so
  closing and reopening the drawer keeps it. Snapshots — a preview state
  saved as a named starting point — are kept on the environment as
  `envState.previewSnapshots`.
*/
import { sideEffectOf, toolSettingOf } from "./toolEnv";
import { castOf } from "./actors";
import { currentUser } from "./scenarioProvenance";

const now = () => new Date().toISOString();

/* The preview of each environment, kept for the session. */
export const PREVIEWS = new Map();
const clone = (x) => JSON.parse(JSON.stringify(x));

/* ── where a preview starts ─────────────────────────────────────────────── */

const ORDER_ROWS = [
  {
    order_id: "O-101",
    customer_id: "C-114",
    delivered_at: "2026-10-21",
    amount_cents: 2599,
    status: "delivered",
  },
  {
    order_id: "O-102",
    customer_id: "C-203",
    delivered_at: "2026-10-02",
    amount_cents: 64900,
    status: "delivered",
  },
  {
    order_id: "O-103",
    customer_id: "C-881",
    delivered_at: "2026-09-20",
    amount_cents: 1250,
    status: "delivered",
  },
  {
    order_id: "O-104",
    customer_id: "C-881",
    delivered_at: "2026-09-28",
    amount_cents: 4599,
    status: "delivered",
  },
];

/** The world's records at the start: from imported data, else from its tools. */
const seedTablesOf = (env, envState) => {
  const names = (env?.tools || []).map((t) => t.name).join(" ");
  const tables = {};
  if (
    /order|refund|return|shipment/.test(names) ||
    (envState?.dataImports || []).some((d) => d.name === "orders")
  )
    tables.orders = clone(ORDER_ROWS);
  if (
    /refund/.test(names) ||
    (envState?.dataImports || []).some((d) => d.name === "refunds")
  )
    tables.refunds = /RF-7/.test(JSON.stringify(envState?.startingState || ""))
      ? [
          {
            refund_id: "RF-7",
            order_id: "O-104",
            amount_cents: 4599,
            status: "committed",
          },
        ]
      : [];
  if (!Object.keys(tables).length)
    tables.records = [
      { id: "R-1", name: "Sample record", status: "open" },
      { id: "R-2", name: "Second record", status: "closed" },
    ];
  return tables;
};

/** Starting states to choose from: the environment's seed, then saved snapshots. */
export const startingPointsOf = (env, envState) => [
  {
    id: "seed",
    label: envState?.startingState
      ? `Starting state of the environment · ${envState.startingState.facts.length} fact${envState.startingState.facts.length === 1 ? "" : "s"} from records`
      : "Seed of the environment",
    tables: seedTablesOf(env, envState),
  },
  ...(envState?.previewSnapshots || []).map((s) => ({
    id: s.id,
    label: `Snapshot · ${s.name}`,
    tables: s.tables,
  })),
];

/** Snapshots need a world we hold all of: none while a tool runs Live. */
export const snapshotSupportOf = (env, envState) => {
  const live = (env?.tools || []).filter(
    (t) => toolSettingOf(t, envState).tier === "live",
  );
  return live.length
    ? {
        ok: false,
        why: `${live.map((t) => t.name).join(", ")} run${live.length === 1 ? "s" : ""} Live, so the preview can't hold all of the state. Snapshot is off.`,
      }
    : { ok: true };
};

export const newPreview = (env, envState, pointId = "seed") => {
  const point =
    startingPointsOf(env, envState).find((p) => p.id === pointId) ||
    startingPointsOf(env, envState)[0];
  return {
    status: "stopped",
    pointId: point.id,
    tables: clone(point.tables),
    changed: [],
    files: [],
    log: [],
    network: [],
    calls: 0,
    tokens: 0,
    startedAt: null,
    elapsedMs: 0,
    pending: null,
  };
};

/* ── acting as the agent ───────────────────────────────────────────────── */

const log = (p, who, text, tone) => ({
  ...p,
  log: [...p.log, { at: now(), who, text, tone }],
});

export const startPreview = (p) =>
  log(
    { ...p, status: "running", startedAt: p.startedAt || now() },
    "Sandbox",
    p.startedAt ? "Resumed" : "Started on the chosen starting state",
  );

export const stopPreview = (p) =>
  log(
    {
      ...p,
      status: "stopped",
      elapsedMs:
        p.elapsedMs +
        (p.startedAt && p.status === "running"
          ? Date.now() - new Date(p.startedAt)
          : 0),
      startedAt: null,
    },
    "Sandbox",
    "Stopped. State is kept until you reset.",
  );

/** A plausible request for the tool, from its argument names. */
export const sampleArgsOf = (tool) => {
  const args = (tool?.args || [])
    .map((a) => (typeof a === "string" ? a : a.name))
    .filter(Boolean);
  const value = (a) =>
    /order/.test(a)
      ? "O-104"
      : /customer|user/.test(a)
        ? "C-881"
        : /cents/.test(a)
          ? 4599
          : /amount/.test(a)
            ? 45.99
            : /confirm/.test(a)
              ? true
              : /reason/.test(a)
                ? "damaged"
                : "example";
  return Object.fromEntries(
    (args.length ? args : ["order_id"]).map((a) => [a, value(a)]),
  );
};

const findRow = (tables, key) => {
  for (const [table, rows] of Object.entries(tables)) {
    const row = rows.find((r) => Object.values(r).includes(key));
    if (row) return { table, row };
  }
  return null;
};

/**
 * One tool call from the agent's side. Reads see earlier writes; a write
 * changes the state; faults fire as the tool is set; an approval the tool
 * waits for brings in its approver; Live tools aren't called from here.
 */
export const callToolPatch = (p, { env, envState, tool, args }) => {
  const setting = toolSettingOf(tool, envState);
  const effect = sideEffectOf(tool);
  const n = p.calls + 1;
  const ms = 80 + ((n * 37) % 160) + (setting.faults?.latencyMs || 0);
  const req = {
    at: now(),
    tool: tool.name,
    method: effect === "read" || effect === "fresh" ? "GET" : "POST",
    path: `/tools/${tool.name}`,
    body: args,
  };
  let next = { ...p, calls: n, tokens: p.tokens + 340 };
  const net = (status, response, note) => ({
    ...next,
    network: [...next.network, { ...req, status, ms, response, note }],
  });

  if (setting.tier === "live") {
    next = net("skipped", null, "Live tools aren't called from a preview");
    return log(
      next,
      tool.name,
      "Not called: it runs Live, and a preview never reaches a real system.",
      "warn",
    );
  }
  /* Faults, as the tool is set: the nth call times out when its rate says so. */
  const f = setting.faults || {};
  if (f.timeoutPct && n % Math.max(2, Math.round(100 / f.timeoutPct)) === 0) {
    next = net(
      504,
      { error: "timeout" },
      `Injected timeout (${f.timeoutPct}% of calls)`,
    );
    return log(
      next,
      tool.name,
      "Timed out before it committed. Injected by the tool's fault settings.",
      "warn",
    );
  }
  if (setting.tier === "stub") {
    next = net(200, { ok: true, note: "fixed response" });
    return log(
      next,
      tool.name,
      "Answered with its fixed response. State doesn't change.",
    );
  }
  if (setting.tier === "replay") {
    next = net(200, { ok: true, recorded_at: "2026-10-12", ...args });
    const reads = effect === "read" || effect === "fresh";
    return log(
      next,
      tool.name,
      reads && next.changed.length
        ? "Answered with the recorded response, so it doesn't see what this preview wrote. Set it to Simulated to read the state."
        : "Answered with the recorded response. State doesn't change.",
    );
  }
  const key = Object.values(args).find((v) => typeof v === "string");
  const found = key ? findRow(next.tables, key) : null;
  if (effect === "read" || effect === "fresh") {
    next = net(
      found ? 200 : 404,
      found ? { ok: true, ...found.row } : { error: "not_found", id: key },
    );
    return log(
      next,
      tool.name,
      found
        ? `Read ${found.table} ${key}${next.changed.some((c) => c.key === key) ? ", including what was written earlier in this preview" : ""}.`
        : `No record ${key} in the state.`,
    );
  }
  /* A write. Above an approval threshold it waits for the approver first. */
  const amount =
    args.amount != null
      ? Number(args.amount)
      : args.amount_cents != null
        ? args.amount_cents / 100
        : 0;
  const approval = setting.approval;
  if (approval && amount > approval.above && !args.approval_id) {
    const approver = castOf(env, envState).find((a) => a.name === approval.by);
    next = net(202, {
      status: "needs_approval",
      above: approval.above,
      approver: approval.by,
    });
    next = log(
      next,
      tool.name,
      `Held: $${amount} is above $${approval.above}, so it needs ${approval.by}'s approval.`,
      "warn",
    );
    next = {
      ...next,
      pending: { tool: tool.name, args, approver: approval.by },
    };
    return log(
      next,
      approval.by,
      approver
        ? `Joins (${approver.trigger || "asked to approve"}). Approved: call again with approval_id AP-${n}.`
        : `Asked to approve. Approved: call again with approval_id AP-${n}.`,
      "actor",
    );
  }
  if (/refund/.test(tool.name)) {
    const orderId = args.order_id || key;
    const refunds = next.tables.refunds || [];
    const existing = refunds.find((r) => r.order_id === orderId);
    if (existing) {
      next = net(200, {
        ok: true,
        refund_id: existing.refund_id,
        reused: true,
      });
      return log(
        next,
        tool.name,
        `${orderId} already has ${existing.refund_id}. Reused it, so no second refund exists.`,
      );
    }
    const id = `RF-${20 + n}`;
    const row = {
      refund_id: id,
      order_id: orderId,
      amount_cents: Math.round(amount * 100) || 4599,
      status: "committed",
    };
    next = {
      ...next,
      tables: { ...next.tables, refunds: [...refunds, row] },
      changed: [
        ...next.changed,
        { table: "refunds", key: id, change: "added" },
      ],
      pending: null,
    };
    next = net(201, { ok: true, refund_id: id, status: "committed" });
    return log(
      next,
      tool.name,
      `Wrote refund ${id} on ${orderId}. A later read sees it.`,
    );
  }
  if (found) {
    const row = { ...found.row, status: "updated" };
    next = {
      ...next,
      tables: {
        ...next.tables,
        [found.table]: next.tables[found.table].map((r) =>
          r === found.row ? row : r,
        ),
      },
      changed: [
        ...next.changed,
        { table: found.table, key, change: "updated" },
      ],
    };
    next = net(200, { ok: true, ...row });
    return log(next, tool.name, `Updated ${found.table} ${key}.`);
  }
  next = net(200, { ok: true, ...args });
  return log(
    next,
    tool.name,
    "Accepted. Nothing in the state matched, so nothing changed.",
  );
};

const CALLER_LINES = [
  "Okay. So what happens now?",
  "I've already waited a week for this.",
  "Can you just confirm the amount for me?",
  "Fine, thank you.",
];
/** Say something as the agent; the simulated caller answers. */
export const sayPatch = (p, { text, persona }) => {
  let next = log({ ...p, tokens: p.tokens + 120 }, "You, as the agent", text);
  const said = next.log.filter((l) => l.who === "You, as the agent").length;
  next = log(
    next,
    persona || "Caller",
    CALLER_LINES[(said - 1) % CALLER_LINES.length],
    "actor",
  );
  return next;
};

/** What a file shows the agent: only what it may read. */
export const previewFilesOf = (envState) =>
  (envState?.sources || [])
    .filter((s) => s.inEnv)
    .map((s) => ({
      name: s.name,
      role: s.role,
      agentSees: s.visibility !== "evaluator",
    }));

export const resourcesOf = (p) => {
  const elapsed =
    p.elapsedMs +
    (p.status === "running" && p.startedAt
      ? Date.now() - new Date(p.startedAt)
      : 0);
  return {
    elapsed,
    calls: p.calls,
    tokens: p.tokens,
    credits: Math.round(p.tokens / 400) / 10,
    memoryMb: 180 + p.calls * 6,
  };
};

export const snapshotPatch = (envState, p, name) => ({
  previewSnapshots: [
    ...(envState?.previewSnapshots || []),
    {
      id: `snap-${Date.now().toString(36)}`,
      name,
      at: now(),
      by: currentUser().name,
      tables: clone(p.tables),
    },
  ],
});

export const previewCli = (env, pointId) =>
  `agent-learn env preview start --environment ${(env?.name || "env").toLowerCase().replace(/[^a-z0-9]+/g, "-")}${pointId && pointId !== "seed" ? ` --from-snapshot ${pointId}` : ""}`;
