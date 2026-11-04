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
import { schemaFor } from "./envInternals";

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

/* Sample rows for one of the world's tables, from its columns: ids that
   other tables point at, dates, amounts, statuses. Steady per table. */
const ID_PREFIX = {
  customers: "C",
  accounts: "A",
  orders: "O",
  transactions: "T",
  invoices: "INV",
  cards: "CARD",
  disputes: "D",
  refunds: "RF",
  payments: "P",
  returns: "R",
  bookings: "B",
  drivers: "DR",
};
const singular = (t) => t.replace(/ies$/, "y").replace(/s$/, "");
const prefixOf = (table) => ID_PREFIX[table] || table.slice(0, 2).toUpperCase();
const idsOf = (table) =>
  table === "customers"
    ? ["C-114", "C-203", "C-881", "C-412"]
    : [0, 1, 2, 3].map((i) => `${prefixOf(table)}-${101 + i}`);
const STATUSES = ["open", "pending", "closed", "active"];
const NAMES = [
  "Jamal Carter",
  "Priya Shah",
  "Alan Whitfield",
  "Diane Sullivan",
];

const rowsFor = (t, names) => {
  const cols = t.cols?.length ? t.cols : [{ name: "id", pk: true }];
  const pk = cols.find((c) => c.pk) || cols[0];
  return idsOf(t.name).map((id, i) =>
    Object.fromEntries(
      cols.map((c) => {
        const n = c.name.toLowerCase();
        if (c === pk) return [c.name, id];
        /* A key into another table: one of that table's ids. */
        const ref = n.endsWith("_id") ? n.slice(0, -3) : null;
        const target = ref && names.find((x) => singular(x) === ref);
        if (ref)
          return [
            c.name,
            target || ref === "customer"
              ? idsOf(target || "customers")[i % 4]
              : `${ref.toUpperCase().slice(0, 2)}-${101 + i}`,
          ];
        if (/^past|overdue|^is_|^has_|_sla$|flag|frozen|lost|joint/.test(n))
          return [c.name, i % 2 === 0];
        if (/last_?4/.test(n))
          return [c.name, ["4242", "1881", "0005", "3714"][i]];
        if (/reason/.test(n))
          return [c.name, ["fraud", "duplicate", "not received", "damaged"][i]];
        if (/kind|type|category/.test(n))
          return [c.name, ["debit", "credit", "refund", "fee"][i]];
        if (/holder|owner|person/.test(n)) return [c.name, NAMES[i]];
        if (/_at$|date|day/.test(n))
          return [c.name, `2026-10-${String(2 + i * 6).padStart(2, "0")}`];
        if (/cents/.test(n)) return [c.name, [2599, 64900, 1250, 4599][i]];
        if (/amount|total|balance|price|limit/.test(n))
          return [c.name, [25.99, 649, 12.5, 45.99][i]];
        if (/email/.test(n)) return [c.name, `customer${i + 1}@sandbox.test`];
        if (/status|state/.test(n)) return [c.name, STATUSES[i]];
        if (/name/.test(n)) return [c.name, NAMES[i]];
        if (/^(is_|has_)|flag|frozen|lost|joint/.test(n))
          return [c.name, i % 2 === 0];
        if (/int|num|count/.test(String(c.type || "").toLowerCase()))
          return [c.name, i + 1];
        return [c.name, `${n.replace(/_/g, " ")} ${i + 1}`];
      }),
    ),
  );
};

/** The world's records at the start: a few rows of each of its tables. */
const seedTablesOf = (env, envState) => {
  const schema = schemaFor(env, envState);
  const toolNames = (env?.tools || []).map((t) => t.name).join(" ");
  const names = schema.map((t) => t.name);
  const tables = {};
  schema.forEach((t) => {
    tables[t.name] =
      t.name === "orders" ? clone(ORDER_ROWS) : rowsFor(t, names);
  });
  /* Tools that act on orders and refunds get those tables even when the
     schema doesn't list them. */
  if (!tables.orders && /order|refund|return|shipment/.test(toolNames))
    tables.orders = clone(ORDER_ROWS);
  if (
    /refund/.test(toolNames) ||
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
      : /* No refund exists yet, so a first refund is the one the test makes. */
        [];
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
      ? `Starting data · from ${(envState.sources || []).find((x) => x.id === envState.startingState.sourceId)?.name || "your records"}`
      : "Starting data of the environment",
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
    p.log.length
      ? "Resumed — what changed before you stopped is kept"
      : "Started on the chosen starting data",
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
export const sampleArgsOf = (tool, tables = null) => {
  const args = (tool?.args || [])
    .map((a) => (typeof a === "string" ? a : a.name))
    .filter(Boolean);
  /* An id that exists in the starting data, so the first try finds it. */
  const idIn = (ref) => {
    const t = tables && Object.keys(tables).find((x) => singular(x) === ref);
    const row = t && tables[t][tables[t].length > 3 ? 3 : 0];
    return row ? Object.values(row)[0] : null;
  };
  const value = (a) => {
    const n = a.toLowerCase();
    if (n.endsWith("_id")) {
      const ref = n.slice(0, -3);
      const hit = idIn(ref);
      if (hit) return hit;
    }
    return /order/.test(n)
      ? "O-104"
      : /customer|user/.test(n)
        ? "C-881"
        : /cents/.test(n)
          ? 4599
          : /amount/.test(n)
            ? 45.99
            : /email/.test(n)
              ? "customer1@sandbox.test"
              : /^from$|start|since/.test(n)
                ? "2026-10-01"
                : /^to$|end|until/.test(n)
                  ? "2026-10-31"
                  : /date|_at$|birth/.test(n)
                    ? "1990-04-12"
                    : /confirm/.test(n)
                      ? true
                      : /reason/.test(n)
                        ? "damaged"
                        : /query|search|q$/.test(n)
                          ? "refund"
                          : `${n.replace(/_/g, " ")} 1`;
  };
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
/* One tool call: the world's answer as a log line, carrying the call itself
   (tool, arguments, read or write, status, time) so the log can show it the
   way a run's Tool calls do. */
export const callToolPatch = (p, opts) => {
  const next = callToolInner(p, opts);
  const req =
    next.network.length > p.network.length
      ? next.network[next.network.length - 1]
      : null;
  const effect = sideEffectOf(opts.tool);
  const last = next.log[next.log.length - 1];
  if (!last) return next;
  const call = {
    tool: opts.tool.name,
    args: opts.args,
    wrote: !(effect === "read" || effect === "fresh"),
    status: req?.status ?? null,
    ms: req?.ms ?? null,
    response: req?.response ?? null,
  };
  return { ...next, log: [...next.log.slice(0, -1), { ...last, call }] };
};

const callToolInner = (p, { env, envState, tool, args }) => {
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
  `fi sim env preview start --environment ${(env?.name || "env").toLowerCase().replace(/[^a-z0-9]+/g, "-")}${pointId && pointId !== "seed" ? ` --from-snapshot ${pointId}` : ""}`;
