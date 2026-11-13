import { useEffect, useState } from "react";
// Stand-in for the backend's group fix, for dev only. Delete this file and
// every line marked "dummy:" once the real routes exist.
import { VERSION_PIECES, environmentVersionsKey, draftsKey } from "./versions";

const FLAG = "fi.readinessFixDummy";
const END_FLAG = "fi.readinessFixDummy.end";
const RUN_KEY = "fi.readinessFixDummy.run";

export const DUMMY_END_MS = 38000;
// A run's state by time since it started.
export const DUMMY_STEPS = [
  { from: 0, state: "fixing" },
  { from: 8000, state: "waiting_on_you" },
  { from: 20000, state: "fixing" },
  { from: 30000, state: "checking" },
];

const read = (key) => {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
};

export const dummyOn = () => {
  if (!import.meta.env.DEV) return false;
  return read(FLAG) === "1";
};

// "fi.readinessFixDummy.hold" = a step's state (e.g. waiting_on_you) freezes
// every run on that step, for looking at it.
const elapsedSince = (startedAt) => {
  const held = DUMMY_STEPS.find((step) => step.state === read(`${FLAG}.hold`));
  return held ? held.from + 1000 : Date.now() - startedAt;
};

const ending = () => {
  const end = read(END_FLAG);
  return end === "draft" || end === "failed" ? end : "saved";
};

// Kept in memory too, for when storage throws.
let memory = {};
const readRuns = () => {
  try {
    return JSON.parse(window.localStorage.getItem(RUN_KEY) || "{}");
  } catch {
    return memory;
  }
};
const writeRuns = (runs) => {
  memory = runs;
  try {
    window.localStorage.setItem(RUN_KEY, JSON.stringify(runs));
  } catch {
    // memory holds it
  }
};

// Builder rows of the last report seen, so a started run covers them.
const lastBuilderRows = {};
// Each builder row's suggested message, by environment and row id.
const lastMessages = {};

const FAKE_ROW_ID = "dummy_tool_routing";
const fakeRow = () => ({
  id: FAKE_ROW_ID,
  label: "Tool routing",
  area: "tool_binding",
  status: "blocked",
  measured: true,
  reason: "lookup_order never reached the test world (dummy)",
  consequence: "",
  affected: { count: 3 },
  blocked_by: null,
  targets: [],
  fix: {
    type: "tool_routing",
    category: "tool_routing",
    target: "lookup_order",
    action: "Point lookup_order at the test world",
    would: "lookup_order lands in the test world and its probe passes",
    recheck: null,
  },
});

const FAKE_START_ID = "dummy_start";
const fakeStartRow = () => ({
  id: FAKE_START_ID,
  label: "Agent starts",
  area: "start_and_message",
  status: "blocked",
  measured: true,
  reason:
    "Agent preflight failed: Environment variables (missing RETELL_API_KEY) (dummy)",
  consequence: "",
  affected: { count: 3 },
  blocked_by: null,
  targets: [],
  fix: {
    type: "preflight",
    category: "startup",
    target: null,
    action: "Fix the agent's preflight checks (env: provide RETELL_API_KEY)",
    would: "The agent passes its preflight and can start",
    recheck: { scope: "agent", method: "POST", path: "/dummy/" },
  },
});

const fakeFix = (type, action, would) => ({
  type,
  category: "evidence",
  target: null,
  action,
  would,
  recheck: null,
});

const fakeBuilderRows = () => [
  {
    id: "dummy_seed_data",
    label: "Seeded world state",
    area: "evidence",
    status: "blocked",
    measured: true,
    reason:
      "The orders table the agent reads has no copy in the test world (dummy)",
    consequence: "",
    affected: { count: 2 },
    blocked_by: null,
    targets: [],
    fix: fakeFix(
      "seed_data",
      "Give the environment a copy of the orders table",
      "The agent reads seeded orders in the test world",
    ),
  },
];

// A Stale reading on stopping, so the re-check group has a second line.
const fakeRecheckRow = () => ({
  id: "dummy_cancel_stale",
  label: "Stops cleanly",
  area: "cancellation",
  status: "stale",
  measured: true,
  reason: "Checked with another environment version (dummy)",
  consequence: "",
  affected: { count: 0 },
  blocked_by: null,
  targets: [],
  fix: {
    type: "recheck",
    category: null,
    target: null,
    action: "Check this agent with this environment and suite",
    would: "Stopping is read for these versions",
    recheck: { scope: "pair", method: "POST", path: "/dummy/" },
  },
});

// Puts a fake row into its area, adding the area when the report has none.
const withRow = (areas, row) =>
  areas.some((area) => area.id === row.area)
    ? areas.map((area) =>
        area.id === row.area
          ? { ...area, status: "blocked", rows: [...(area.rows || []), row.id] }
          : area,
      )
    : [...areas, { id: row.area, status: "blocked", rows: [row.id] }];

const groupOf = (row) => {
  if (!row.fix) return null;
  if (row.fix.type === "preflight") return "needs_you";
  if (row.fix.type === "recheck" || row.fix.type === "agent_tools_unread") {
    return "recheck";
  }
  return "builder";
};

const stateAt = (elapsed) =>
  [...DUMMY_STEPS].reverse().find((step) => elapsed >= step.from);

const ENDED = {
  saved: { state: "done" },
  draft: { state: "done" },
  failed: { state: "failed" },
};

// The run as the backend would read it now.
// A line added to a running fix waits this long before the builder takes it.
const PICKUP_MS = 6000;

const describeRun = (run) => {
  const elapsed = elapsedSince(run.startedAt);
  const added = run.added || [];
  const lastAdded = Math.max(
    0,
    ...added.map((item) => item.at - run.startedAt),
  );
  // Lines added late keep the run going until the builder has worked them in.
  const endMs = Math.max(DUMMY_END_MS, lastAdded + 15000);
  const ended = elapsed >= endMs;
  const end = ending();
  const step = stateAt(elapsed);
  let state = step.state;
  if (ended) state = ENDED[end].state;
  else if (elapsed >= DUMMY_STEPS[3].from && elapsed < endMs - 8000) {
    state = "fixing";
  }
  const pickedUp = (item) => Date.now() - item.at >= PICKUP_MS;
  return {
    id: run.id,
    group: "builder",
    state,
    queue: added
      .filter((item) => !pickedUp(item))
      .map(({ message, rows }) => ({ message, rows })),
    picked: added
      .filter(pickedUp)
      .map(({ message, rows }) => ({ message, rows })),
    started_at: new Date(run.startedAt).toISOString(),
    // The step's start, never now, so a poll doesn't look like a change.
    updated_at: new Date(
      run.startedAt + (ended ? DUMMY_END_MS : step.from),
    ).toISOString(),
    finished_at: ended ? new Date(run.startedAt + endMs).toISOString() : null,
    pairing: Object.fromEntries(
      VERSION_PIECES.map((piece) => [
        `${piece}_version_id`,
        run.picks?.[piece] || null,
      ]),
    ),
    rows: [...run.rows, ...added.flatMap((item) => item.rows)],
    message_id: `${run.id}-message`,
    message: run.message || "Point lookup_order at the test world (dummy)",
    saved:
      ended && end === "saved"
        ? { environment: run.newest || null, suite: null }
        : null,
    draft: ended && end === "draft",
    error:
      ended && end === "failed"
        ? {
            code: "chat_gave_up",
            message: "The builder chat couldn't make this change.",
          }
        : null,
  };
};

export const dummyFixRun = (envId) => {
  const run = readRuns()[envId];
  return run ? describeRun(run) : null;
};

export const dummyStartFix = async (
  envId,
  { picks = {}, rows = null, newest = null, hasDrafts = false } = {},
) => {
  if (hasDrafts) {
    throw {
      statusCode: 409,
      error: "unsaved_changes",
      message: "Save or discard the unsaved changes first.",
    };
  }
  const current = dummyFixRun(envId);
  const runs = readRuns();
  if (current && !["done", "failed", "stopped"].includes(current.state)) {
    // A running fix takes the line too; its message queues in the same chat.
    const picked = rows?.length ? rows : [];
    runs[envId] = {
      ...runs[envId],
      added: [
        ...(runs[envId].added || []),
        {
          rows: picked,
          message: picked
            .map((id) => lastMessages[envId]?.[id])
            .filter(Boolean)
            .join("\n"),
          at: Date.now(),
        },
      ],
    };
    writeRuns(runs);
    return { fix: dummyFixRun(envId) };
  }
  runs[envId] = {
    id: `dummy-${Date.now().toString(36)}`,
    startedAt: Date.now(),
    picks,
    rows: rows?.length ? rows : lastBuilderRows[envId] || [FAKE_ROW_ID],
    message: (rows?.length ? rows : lastBuilderRows[envId] || [])
      .map((id) => lastMessages[envId]?.[id])
      .filter(Boolean)
      .join("\n"),
    newest,
  };
  writeRuns(runs);
  return { fix: dummyFixRun(envId) };
};

// What the fix hook needs from the cache: the newest active environment
// version (standing in for the one the backend would save) and open drafts.
export const dummyContext = (queryClient, envId) => {
  const versions = queryClient.getQueryData(environmentVersionsKey(envId));
  const drafts = queryClient.getQueryData(draftsKey(envId));
  const newest = (versions?.environment || [])
    .filter((v) => (v.status || "active") === "active")
    .sort((a, b) => new Date(b.created_at) - new Date(a.created_at))[0];
  return {
    newest: newest ? { id: newest.id, label: newest.label || null } : null,
    hasDrafts: Boolean(drafts?.environment || drafts?.suite),
  };
};

const samePicks = (a = {}, b = {}) =>
  VERSION_PIECES.every((piece) => (a?.[piece] || "") === (b?.[piece] || ""));

const progressFor = (row, run, fix, index) => {
  const base = { reason: null, waiting_on: null, fix_id: run.id };
  if ((fix.queue || []).some((item) => item.rows.includes(row.id))) {
    return { ...base, state: "queued" };
  }
  if (fix.state === "done" && fix.saved) {
    return { ...base, state: "fixed" };
  }
  if (fix.state === "done") {
    return {
      ...base,
      state: "waiting_on_you",
      reason: {
        code: "draft_unsaved",
        message: "The fix is in unsaved changes. Save them to run.",
      },
    };
  }
  if (fix.state === "failed") {
    return { ...base, state: "could_not_fix", reason: fix.error };
  }
  if (fix.state === "waiting_on_you") {
    return { ...base, state: index === 0 ? "waiting_on_you" : "fixing" };
  }
  if (row.blocked_by && elapsedSince(run.startedAt) < DUMMY_STEPS[1].from) {
    return { ...base, state: "waiting_on_other", waiting_on: row.blocked_by };
  }
  return { ...base, state: "fixing" };
};

export const augmentReport = (envId, picks, report) => {
  let rows = (report.rows || []).map((row) => ({
    ...row,
    fix_group: groupOf(row),
  }));
  let areas = report.areas || [];
  // Several chat-fixable lines, one per area, so Fix all has a group to fix.
  [fakeRow(), ...fakeBuilderRows()]
    .filter((fake) => !rows.some((row) => row.id === fake.id))
    .forEach((fake) => {
      const row = { ...fake, fix_group: "builder" };
      rows = [...rows, row];
      areas = withRow(areas, row);
    });
  if (!rows.some((row) => row.id === "dummy_cancel_stale")) {
    const fake = { ...fakeRecheckRow(), fix_group: "recheck" };
    rows = [...rows, fake];
    areas = withRow(areas, fake);
  }
  if (!rows.some((row) => row.fix_group === "needs_you")) {
    const fake = { ...fakeStartRow(), fix_group: "needs_you" };
    rows = [...rows, fake];
    areas = withRow(areas, fake);
  }
  // The message the backend would write for each builder line's fix.
  rows = rows.map((row) =>
    row.fix_group === "builder" && row.fix && !row.fix.message
      ? { ...row, fix: { ...row.fix, message: `${row.fix.action} (dummy)` } }
      : row,
  );
  const builder = rows.filter((row) => row.fix_group === "builder");
  lastBuilderRows[envId] = builder.map((row) => row.id);
  lastMessages[envId] = Object.fromEntries(
    builder.map((row) => [row.id, row.fix?.message]),
  );

  const run = readRuns()[envId];
  const fix = run ? describeRun(run) : null;
  const matches =
    fix &&
    (samePicks(picks, run.picks) ||
      (fix.saved &&
        report.pairing?.environment_version?.id === fix.saved.environment?.id));
  const covered = matches ? new Set(fix.rows) : new Set();

  rows = rows
    .filter((row) => {
      // A pair the run didn't touch no longer shows the fake row as blocked.
      return !(row.id === FAKE_ROW_ID && fix?.saved && !matches);
    })
    .map((row) => {
      if (!covered.has(row.id)) return { ...row, fix_progress: null };
      const progress = progressFor(row, run, fix, fix.rows.indexOf(row.id));
      return {
        ...row,
        status: progress.state === "fixed" ? "supported" : row.status,
        fix_progress: progress,
      };
    });
  // The backend re-checks the pair after the auto-save, which clears the Stale
  // readings in the areas the fix mended.
  const areaOf = new Map(
    areas.flatMap((area) => (area.rows || []).map((id) => [id, area.id])),
  );
  const mended = new Set(
    rows
      .filter((row) => row.fix_progress?.state === "fixed")
      .map((row) => areaOf.get(row.id)),
  );
  rows = rows.map((row) =>
    row.fix_group === "recheck" && mended.has(areaOf.get(row.id))
      ? { ...row, status: "supported", fix: null, fix_group: null }
      : row,
  );
  const kept = new Set(rows.map((row) => row.id));
  areas = areas.map((area) => ({
    ...area,
    rows: (area.rows || []).filter((id) => kept.has(id)),
  }));

  return {
    ...report,
    // The backend's re-check after a saved fix gives a fresh report.
    ...(matches && fix.finished_at && { computed_at: fix.finished_at }),
    rows,
    areas,
    fix_groups: {
      builder: {
        message: `Fix these so the run can go ahead (dummy): ${builder
          .map((row) => row.fix.action)
          .join("; ")}`,
        rows: builder.map((row) => row.id),
      },
    },
  };
};

// What the builder chat would show for a dummy fix run, step by step in time.
export const dummyChatTurns = (fix) => {
  if (!fix) return [];
  const elapsed = elapsedSince(new Date(fix.started_at).getTime());
  const at = (ms) => elapsed >= ms;
  const steps = [
    {
      kind: "think",
      text: "Reading how lookup_order is called in the agent's tools.",
    },
  ];
  if (at(3000)) {
    steps.push({
      kind: "tool",
      label: "Found lookup_order",
      result: "calls orders.example.com",
      state: "completed",
    });
  }
  if (at(DUMMY_STEPS[1].from)) {
    steps.push({
      kind: "note",
      text: "Should lookup_order read the seeded orders table, or a copy of the agent's own store?",
    });
  }
  const turns = [
    { id: fix.message_id, role: "user", text: fix.message },
    { id: `${fix.id}-b1`, role: "builder", steps },
    ...(fix.picked || []).flatMap((item, i) => [
      { id: `${fix.id}-a${i}`, role: "user", text: item.message },
      {
        id: `${fix.id}-ab${i}`,
        role: "builder",
        steps: [
          { kind: "think", text: "Taking this on after the current change." },
        ],
      },
    ]),
  ];
  if (!at(DUMMY_STEPS[2].from)) return turns;
  const later = [
    {
      kind: "tool",
      label: "Bound lookup_order to the seeded orders table",
      state: "completed",
    },
  ];
  if (at(DUMMY_STEPS[3].from)) {
    later.push({
      kind: "tool",
      label: "Saved as a new environment version",
      state: "completed",
    });
    later.push({
      kind: "tool",
      label: "Re-checking the pair",
      state: fix.state === "checking" ? "running" : "completed",
    });
  }
  if (fix.state === "failed") {
    later.push({
      kind: "error",
      text: "The builder chat couldn't make this change.",
    });
  } else if (fix.state === "done") {
    later.push({
      kind: "completed",
      text: "Fix applied and the re-check passed.",
    });
  }
  return [...turns, { id: `${fix.id}-b2`, role: "builder", steps: later }];
};

// The builder chat's turns with the dummy fix's appended while the dummy is on.
// Replies still go to the real builder chat.
export const useDummyChat = (fix, turns = []) => {
  // Steps appear over time, so redraw every second while a run is going.
  const [, setTick] = useState(0);
  const going =
    Boolean(fix) && !["done", "failed", "stopped"].includes(fix.state);
  useEffect(() => {
    if (!going || !dummyOn()) return undefined;
    const timer = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, [going]);
  return dummyOn() ? [...turns, ...dummyChatTurns(fix)] : turns;
};

// The default agent version as if its start check failed on a missing key, so
// the agent start line offers the credentials form. Swaps in copies, in place.
export const addMissingKeys = (versions) => {
  if (!dummyOn()) return;
  const at = versions.findIndex((version) => version.isDefault);
  if (at < 0) return;
  versions[at] = {
    ...versions[at],
    preflightStatus: "failed",
    preflightReport: {
      checks: [
        {
          id: "env",
          label: "Environment variables",
          status: "failed",
          missing: ["RETELL_API_KEY"],
        },
      ],
    },
  };
};
