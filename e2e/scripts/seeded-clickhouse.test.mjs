import assert from "node:assert/strict";
import http from "node:http";
import { registerHooks } from "node:module";
import test from "node:test";
import ts from "typescript";

// Offline half of lib/seeded-clickhouse.ts (the live half is
// harness/seeded-clickhouse.spec.ts). The loader is transpiled with the workspace's
// compiler, as state-probe.test.mjs does, and talks to a loopback fake of the
// ClickHouse HTTP interface that models exactly what the loader relies on:
// ReplacingMergeTree FINAL (latest version per sorting key), system.tables sorting
// keys, query parameters, and JSONEachRow inserts.
registerHooks({
  // lib/ imports siblings without an extension (tsconfig moduleResolution Bundler).
  resolve(specifier, context, nextResolve) {
    if (
      specifier === "./env" &&
      /\/e2e\/lib\/seeded-clickhouse\.ts/.test(context.parentURL ?? "")
    ) {
      return nextResolve("./env.ts", context);
    }
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    const loaded = nextLoad(url, context);
    if (!/\/e2e\/lib\/(seeded-clickhouse|env)\.ts(\?|$)/.test(url))
      return loaded;
    return {
      ...loaded,
      format: "module",
      source: ts.transpileModule(String(loaded.source), {
        compilerOptions: {
          target: ts.ScriptTarget.ES2022,
          module: ts.ModuleKind.ESNext,
        },
      }).outputText,
    };
  },
});
const lib = await import("../lib/seeded-clickhouse.ts");

const ORG = "0a0a0a0a-0000-4000-8000-00000000000a";
const PA = "0b0b0b0b-0000-4000-8000-00000000000b";
const PB = "0c0c0c0c-0000-4000-8000-00000000000c";
const PC = "0d0d0d0d-0000-4000-8000-00000000000d";
const PASSWORD = "offline-fake-password";
const NOW = new Date("2026-10-29T12:00:00.000Z");
const GOOD_TABLES = [
  {
    name: "trace_sessions",
    engine: "ReplacingMergeTree",
    sorting_key: "project_id, trace_session_id",
  },
  {
    name: "spans",
    engine: "ReplacingMergeTree",
    sorting_key:
      "project_id, observation_type, service_name, toStartOfHour(start_time), trace_id, id",
  },
];

const plusMicro = (text) => {
  const ms = Date.parse(`${text.slice(0, 10)}T${text.slice(11, 23)}Z`);
  const micros = Number(text.slice(23)) + 1;
  return micros > 999
    ? lib.clickHouseTime(ms + 1, 0)
    : lib.clickHouseTime(ms, micros);
};

/** A loopback fake ClickHouse HTTP endpoint: rows in memory, FINAL by latest version. */
async function fakeClickHouse({
  tables = GOOD_TABLES,
  sessions = [],
  spans = [],
  markerColumn = false,
  beforeTombstone,
} = {}) {
  const state = {
    tables,
    sessions: [...sessions],
    spans: [...spans],
    requests: [],
  };
  const final = () => {
    const latest = new Map();
    for (const row of state.sessions) {
      const key = `${row.project_id}/${row.trace_session_id}`;
      const seen = latest.get(key);
      if (!seen || row.version >= seen.version) latest.set(key, row);
    }
    return [...latest.values()];
  };
  const lines = (rows) =>
    rows.map((r) => JSON.stringify(r)).join("\n") + (rows.length ? "\n" : "");
  const server = http.createServer(async (req, res) => {
    let body = "";
    for await (const chunk of req) body += chunk;
    const url = new URL(req.url, "http://127.0.0.1");
    const p = (name) => url.searchParams.get(`param_${name}`);
    const query = url.searchParams.get("query") ?? body;
    state.requests.push({
      query,
      params: Object.fromEntries(url.searchParams),
      data: url.searchParams.has("query") ? body : null,
      user: req.headers["x-clickhouse-user"],
      key: req.headers["x-clickhouse-key"],
    });
    const answer = (text) => {
      res.writeHead(200, { "content-type": "text/plain" });
      res.end(text);
    };
    if (query.includes("FROM system.tables"))
      return answer(lines(state.tables));
    if (query.includes("FROM system.columns"))
      return answer(
        lines(markerColumn ? [{ name: "e2e_seed_id", type: "String" }] : []),
      );
    if (
      query.startsWith(
        "ALTER TABLE trace_sessions ADD COLUMN IF NOT EXISTS e2e_seed_id",
      )
    ) {
      markerColumn = true;
      return answer("");
    }
    const owned = (row) =>
      row.e2e_seed_id === p("seed") &&
      row.external_session_id === p("ext") &&
      row.first_seen === p("first") &&
      ((row.is_deleted === 0 && row.version === p("v")) ||
        (row.is_deleted === 1 && row.version === plusMicro(p("v"))));
    if (query.startsWith("INSERT INTO spans")) {
      for (const line of body.trim().split("\n")) {
        const version = /"_version":(\d+)}$/.exec(line)[1];
        state.spans.push({ ...JSON.parse(line), _version: version });
      }
      return answer("");
    }
    if (
      query.startsWith("INSERT INTO trace_sessions") &&
      query.includes("addMicroseconds")
    ) {
      beforeTombstone?.(state);
      for (const row of final()) {
        if (
          row.project_id === p("p") &&
          row.trace_session_id === p("s") &&
          row.is_deleted === 0 &&
          (!query.includes("e2e_seed_id = {seed:String}") || owned(row))
        ) {
          state.sessions.push({
            ...row,
            version: plusMicro(row.version),
            is_deleted: 1,
          });
        }
      }
      return answer("");
    }
    if (query.startsWith("INSERT INTO trace_sessions")) {
      for (const line of body.trim().split("\n"))
        state.sessions.push(JSON.parse(line));
      return answer("");
    }
    if (query.includes("AS other_rows")) {
      const projects = [...p("ps").matchAll(/'([0-9a-f-]{36})'/g)].map(
        (m) => m[1],
      );
      const claims = new Map();
      for (const row of state.sessions) {
        if (
          row.trace_session_id !== p("s") ||
          !projects.includes(row.project_id)
        )
          continue;
        const claim = claims.get(row.project_id) ?? {
          project_id: row.project_id,
          ours: 0,
          other_rows: 0,
        };
        const ours = query.includes("ts.e2e_seed_id = {seed:String}")
          ? owned(row)
          : query.includes("countIf(ts.external_session_id") &&
            row.external_session_id === p("ext") &&
            row.version >= p("v");
        if (ours) claim.ours += 1;
        else claim.other_rows += 1;
        claims.set(row.project_id, claim);
      }
      return answer(
        lines(
          [...claims.values()].map((c) => ({
            ...c,
            ours: String(c.ours),
            other_rows: String(c.other_rows),
          })),
        ),
      );
    }
    if (
      query.includes(
        "SELECT DISTINCT toString(project_id) AS project_id, id FROM spans",
      )
    ) {
      const keys = new Map();
      for (const row of state.spans) {
        if (
          row.trace_session_id === p("s") &&
          row.attrs_string?.[p("attr")] === p("seed")
        ) {
          keys.set(`${row.project_id}/${row.id}`, {
            project_id: row.project_id,
            id: row.id,
          });
        }
      }
      return answer(lines([...keys.values()]));
    }
    if (query.includes("FROM trace_sessions AS ts FINAL")) {
      const keyed = query.includes("ts.project_id = {p:UUID}");
      return answer(
        lines(
          final()
            .filter(
              (r) =>
                r.trace_session_id === p("s") &&
                r.is_deleted === 0 &&
                (!keyed || r.project_id === p("p")),
            )
            .map((r) => ({
              project_id: r.project_id,
              trace_session_id: r.trace_session_id,
              external_session_id: r.external_session_id,
            })),
        ),
      );
    }
    res.writeHead(400);
    res.end(`fake ClickHouse cannot answer: ${query.slice(0, 80)}`);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const ch = {
    url: `http://127.0.0.1:${server.address().port}`,
    database: "offline_db",
    password: PASSWORD,
  };
  return {
    state,
    ch,
    final,
    close: () => new Promise((resolve) => server.close(resolve)),
    inserts: () => state.requests.filter((r) => r.query.startsWith("INSERT")),
  };
}

const recorder = () => {
  const attachments = [];
  return {
    attachments,
    info: {
      attachments,
      attach: async (name, options) => {
        attachments.push({ name, ...options });
      },
    },
  };
};

const twoProjects = (extra = {}) =>
  lib.planSharedSession(
    {
      orgId: ORG,
      projects: [{ projectId: PA }, { projectId: PB, turns: 2 }],
      ...extra,
    },
    NOW,
  );

test("offline: a plan mints one session id for every project and distinct traces and spans", () => {
  const plan = twoProjects();
  assert.match(
    plan.traceSessionId,
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
  );
  assert.match(plan.seedId, /^seed-[0-9a-f-]{36}$/);
  assert.match(plan.externalSessionId, /^e2e-seeded-[0-9a-f]{8}$/);
  assert.deepEqual(
    plan.projects.map((p) => [p.projectId, p.turns.length, p.spanIds.length]),
    [
      [PA, 1, 2],
      [PB, 2, 4],
    ],
  );
  const spans = plan.projects.flatMap((p) => p.spanIds);
  const traces = plan.projects.flatMap((p) => p.traceIds);
  assert.equal(new Set(spans).size, 6);
  assert.equal(new Set(traces).size, 3);
  for (const id of spans) assert.match(id, /^[0-9a-f]{16}$/);
  // version text and nanoseconds name one instant; first_seen is the first root start.
  const versionMs = Date.parse(
    `${plan.version.slice(0, 10)}T${plan.version.slice(11, 23)}Z`,
  );
  assert.equal(versionMs, NOW.getTime());
  assert.equal(
    plan.versionNs,
    String(
      BigInt(versionMs) * 1_000_000n +
        BigInt(Number(plan.version.slice(23))) * 1_000n,
    ),
  );
  assert.equal(plan.firstSeen, lib.clickHouseTime(NOW.getTime() - 60_000));
  for (const project of plan.projects) {
    assert.equal(project.turns[0].rootStart, plan.firstSeen);
    for (const turn of project.turns) {
      assert.ok(
        turn.childStart > turn.rootStart && turn.childEnd <= turn.rootEnd,
      );
      assert.ok(turn.input.startsWith(plan.externalSessionId));
    }
  }
  // Every input is the project's own, so a per-project read can tell them apart.
  const inputs = plan.projects.flatMap((p) =>
    p.turns.flatMap((t) => [t.input, t.childInput]),
  );
  assert.equal(new Set(inputs).size, inputs.length);
  assert.notEqual(twoProjects().traceSessionId, plan.traceSessionId);
});

test("offline: a plan refuses ids, projects and turns it cannot seed faithfully", () => {
  const bad = [
    [{ orgId: "not-a-uuid" }, /orgId must be/],
    [{ orgId: "00000000-0000-0000-0000-000000000000" }, /orgId must be/],
    [{ projects: [] }, /1-10 projects/],
    [
      {
        projects: Array.from({ length: 11 }, (_, i) => ({
          projectId: `0e0e0e0e-0000-4000-8000-${String(i).padStart(12, "0")}`,
        })),
      },
      /1-10 projects/,
    ],
    [
      { projects: [{ projectId: PA }, { projectId: PA.toUpperCase() }] },
      /repeats/,
    ],
    [{ projects: [{ projectId: "x" }] }, /projectId must be/],
    [{ projects: [{ projectId: PA, turns: 0 }] }, /turns must be/],
    [{ projects: [{ projectId: PA, turns: 21 }] }, /turns must be/],
    [{ projects: [{ projectId: PA, turns: 1.5 }] }, /turns must be/],
    [{ traceSessionId: "nope" }, /traceSessionId must be/],
    [{ externalSessionId: "" }, /externalSessionId/],
    [{ startedAt: new Date(NOW.getTime() + 1) }, /startedAt/],
  ];
  for (const [patch, error] of bad) {
    assert.throws(
      () =>
        lib.planSharedSession(
          { orgId: ORG, projects: [{ projectId: PA }], ...patch },
          NOW,
        ),
      error,
    );
  }
  const given = lib.planSharedSession(
    {
      orgId: ORG.toUpperCase(),
      traceSessionId: PC.toUpperCase(),
      externalSessionId: "chat-7",
      projects: [{ projectId: PB.toUpperCase() }],
    },
    NOW,
  );
  assert.deepEqual(
    [
      given.orgId,
      given.traceSessionId,
      given.externalSessionId,
      given.projects[0].projectId,
    ],
    [ORG, PC, "chat-7", PB],
  );
});

test("offline: rows carry the shared id, the seeded label and an exact UInt64 version", () => {
  const plan = twoProjects();
  const lines = lib.spanRowLines(plan);
  assert.equal(lines.length, 6);
  const rows = lines.map((line) => ({
    ...JSON.parse(line),
    rawVersion: /"_version":(\d+)}$/.exec(line)[1],
  }));
  for (const row of rows) {
    assert.equal(row.rawVersion, plan.versionNs); // never rounded through a JS number
    assert.deepEqual(
      [
        row.trace_session_id,
        row.org_id,
        row.attrs_string,
        row.is_deleted,
        row.service_name,
      ],
      [
        plan.traceSessionId,
        ORG,
        { [lib.SEEDED_ATTRIBUTE]: plan.seedId },
        0,
        "e2e-seeded",
      ],
    );
  }
  assert.deepEqual(
    rows.map((r) => r.project_id),
    [PA, PA, PB, PB, PB, PB],
  );
  for (const project of plan.projects) {
    for (const turn of project.turns) {
      const root = rows.find((r) => r.id === turn.rootSpanId);
      const child = rows.find((r) => r.id === turn.childSpanId);
      assert.deepEqual(
        [root.parent_span_id, root.trace_id, root.input],
        ["", turn.traceId, turn.input],
      );
      assert.deepEqual(
        [
          child.parent_span_id,
          child.trace_id,
          child.observation_type,
          child.input,
        ],
        [turn.rootSpanId, turn.traceId, "llm", turn.childInput],
      );
    }
  }
  const only = new Set([`${PB}/${plan.projects[1].spanIds[3]}`]);
  assert.deepEqual(
    lib.spanRowLines(plan, only).map((l) => JSON.parse(l).id),
    [plan.projects[1].spanIds[3]],
  );
  const sessions = lib.sessionRowLines(plan).map((l) => JSON.parse(l));
  assert.deepEqual(
    sessions.map((s) => s.e2e_seed_id),
    [plan.seedId, plan.seedId],
  );
  assert.deepEqual(
    sessions.map((s) => [
      s.project_id,
      s.trace_session_id,
      s.external_session_id,
      s.first_seen,
      s.version,
      s.is_deleted,
    ]),
    [PA, PB].map((p) => [
      p,
      plan.traceSessionId,
      plan.externalSessionId,
      plan.firstSeen,
      plan.version,
      0,
    ]),
  );
  assert.throws(
    () => lib.spanRowLines({ ...plan, versionNs: "1.5e18" }),
    /decimal nanoseconds/,
  );
});

test("offline: seeding writes both stores once, values only as parameters; a re-run writes nothing", async (t) => {
  const fake = await fakeClickHouse();
  t.after(fake.close);
  const plan = twoProjects();
  const { attachments, info } = recorder();
  const first = await lib.seedSharedSession(info, plan, fake.ch);
  assert.deepEqual(first.receipt.tables.spans, {
    label: "attrs_string['e2e.seeded'] = seedId",
    planned: 6,
    inserted: 6,
    alreadyPresent: 0,
  });
  assert.deepEqual(
    [
      first.receipt.tables.trace_sessions.planned,
      first.receipt.tables.trace_sessions.inserted,
      first.receipt.tables.trace_sessions.alreadyPresent,
      first.receipt.tables.trace_sessions.version,
    ],
    [2, 2, 0, plan.version],
  );
  assert.deepEqual(
    fake
      .inserts()
      .map((r) => [r.query.split(" (")[0], r.data.trim().split("\n").length]),
    [
      ["INSERT INTO spans", 6],
      ["INSERT INTO trace_sessions", 2],
    ],
  );
  const values = [
    plan.traceSessionId,
    plan.seedId,
    plan.externalSessionId,
    PA,
    PB,
    ORG,
  ];
  for (const request of fake.state.requests) {
    assert.deepEqual(
      [request.user, request.key, request.params.database],
      ["default", PASSWORD, "offline_db"],
    );
    for (const value of values)
      assert.ok(!request.query.includes(value), `SQL text carries ${value}`);
  }
  assert.equal(attachments.length, 1);
  assert.equal(
    attachments[0].name,
    `seeded-clickhouse-seed-${plan.seedId}-1.json`,
  );
  assert.ok(!attachments[0].body.includes(PASSWORD));
  assert.deepEqual(
    JSON.parse(attachments[0].body).projects.map((p) => [
      p.projectId,
      p.spanIds.length,
    ]),
    [
      [PA, 2],
      [PB, 4],
    ],
  );
  // One internal id, two projects: the keyed read sees its own row, the unkeyed read both.
  assert.deepEqual(
    (await lib.liveSessionRows(plan.traceSessionId, { ch: fake.ch })).map(
      (r) => r.project_id,
    ),
    [PA, PB].sort(),
  );
  for (const project of [PA, PB]) {
    assert.deepEqual(
      (
        await lib.liveSessionRows(plan.traceSessionId, {
          projectId: project,
          ch: fake.ch,
        })
      ).map((r) => r.project_id),
      [project],
    );
  }
  const before = fake.inserts().length;
  const again = await lib.seedSharedSession(info, plan, fake.ch);
  assert.equal(fake.inserts().length, before);
  assert.deepEqual(
    [
      again.receipt.tables.spans.inserted,
      again.receipt.tables.spans.alreadyPresent,
      again.receipt.tables.trace_sessions.inserted,
      again.receipt.tables.trace_sessions.alreadyPresent,
    ],
    [0, 6, 0, 2],
  );
  assert.equal(fake.state.spans.length, 6);
  assert.equal(fake.state.sessions.length, 2);
});

test("offline: a partial earlier seed is completed, never duplicated", async (t) => {
  const plan = twoProjects();
  const half = lib
    .spanRowLines(plan)
    .slice(0, 3)
    .map((l) => ({ ...JSON.parse(l), _version: plan.versionNs }));
  const fake = await fakeClickHouse({ spans: half });
  t.after(fake.close);
  const seeded = await lib.seedSharedSession(recorder().info, plan, fake.ch);
  assert.deepEqual(
    [
      seeded.receipt.tables.spans.inserted,
      seeded.receipt.tables.spans.alreadyPresent,
    ],
    [3, 3],
  );
  assert.equal(new Set(fake.state.spans.map((s) => s.id)).size, 6);
  assert.equal(fake.state.spans.length, 6);
});

test("offline: refusals happen before any write", async (t) => {
  const plan = twoProjects();
  const collectorRow = {
    project_id: PA,
    trace_session_id: plan.traceSessionId,
    external_session_id: "chat-7",
    first_seen: lib.clickHouseTime(NOW.getTime() - 3_600_000),
    version: lib.clickHouseTime(NOW.getTime() - 3_600_000),
    is_deleted: 0,
  };
  const foreign = await fakeClickHouse({ sessions: [collectorRow] });
  t.after(foreign.close);
  await assert.rejects(
    lib.seedSharedSession(recorder().info, plan, foreign.ch),
    /not seeded by this plan/,
  );
  assert.equal(foreign.inserts().length, 0);
  assert.equal(
    foreign.state.requests.filter((r) => r.query.startsWith("ALTER")).length,
    0,
  );
  await assert.rejects(
    lib.tombstoneSeededSession(recorder().info, plan, PA, foreign.ch),
    /holds no row seeded/,
  );
  assert.equal(foreign.inserts().length, 0);

  for (const tables of [
    [{ ...GOOD_TABLES[0], sorting_key: "trace_session_id" }, GOOD_TABLES[1]],
    [{ ...GOOD_TABLES[0], engine: "MergeTree" }, GOOD_TABLES[1]],
    [GOOD_TABLES[1]],
  ]) {
    const unkeyed = await fakeClickHouse({ tables });
    t.after(unkeyed.close);
    await assert.rejects(
      lib.seedSharedSession(recorder().info, plan, unkeyed.ch),
      /cannot hold one session id in two projects/,
    );
    assert.equal(unkeyed.inserts().length, 0);
  }
  const spansUnkeyed = await fakeClickHouse({
    tables: [
      GOOD_TABLES[0],
      { ...GOOD_TABLES[1], sorting_key: "trace_id, id" },
    ],
  });
  t.after(spansUnkeyed.close);
  await assert.rejects(
    lib.seedSharedSession(recorder().info, plan, spansUnkeyed.ch),
    /project-keyed/,
  );
  assert.equal(spansUnkeyed.inserts().length, 0);

  const remote = await fakeClickHouse();
  t.after(remote.close);
  const port = new URL(remote.ch.url).port;
  for (const host of ["clickhouse.example.invalid", "10.0.0.5", "0.0.0.0"]) {
    await assert.rejects(
      lib.seedSharedSession(recorder().info, plan, {
        ...remote.ch,
        url: `http://${host}:${port}`,
      }),
      /refuses .*loopback/,
    );
  }
  assert.equal(remote.state.requests.length, 0);
});

// RED-FIRST CH-SEEDED-R2: external identity and chronology are not ownership.
for (const action of ["seed", "tombstone"]) {
  for (const variant of [
    "newer collector",
    "exact signature without marker",
    "another plan marker",
  ]) {
    test(`offline: ${action} refuses a ${variant} row before any INSERT`, async (t) => {
      const plan = twoProjects({ externalSessionId: "collector-shared" });
      const row = JSON.parse(lib.sessionRowLines(plan, [PA])[0]);
      delete row.e2e_seed_id;
      if (variant === "newer collector")
        row.version = lib.clickHouseTime(NOW.getTime() + 1);
      if (variant === "another plan marker")
        row.e2e_seed_id = twoProjects().seedId;
      const fake = await fakeClickHouse({
        sessions: [row],
        markerColumn: true,
      });
      t.after(fake.close);
      const run = () =>
        action === "seed"
          ? lib.seedSharedSession(recorder().info, plan, fake.ch)
          : lib.tombstoneSeededSession(recorder().info, plan, PA, fake.ch);
      await assert.rejects(run, /not seeded by this plan|holds no row seeded/);
      assert.equal(fake.inserts().length, 0);
      assert.deepEqual(fake.final(), [row]);
      assert.equal(fake.state.spans.length, 0);
    });
  }
}

test("offline: tombstone's INSERT refuses a foreign row arriving after preflight", async (t) => {
  const plan = twoProjects();
  const foreign = {
    ...JSON.parse(lib.sessionRowLines(plan, [PA])[0]),
    version: lib.clickHouseTime(NOW.getTime() + 1),
  };
  delete foreign.e2e_seed_id;
  const fake = await fakeClickHouse({
    beforeTombstone: (state) => state.sessions.push(foreign),
  });
  t.after(fake.close);
  await lib.seedSharedSession(recorder().info, plan, fake.ch);
  await assert.rejects(
    lib.tombstoneSeededSession(recorder().info, plan, PA, fake.ch),
    /did not win under FINAL|holds no row seeded/,
  );
  assert.deepEqual(
    fake.final().find((r) => r.project_id === PA),
    foreign,
  );
  assert.equal(fake.state.sessions.filter((r) => r.is_deleted === 1).length, 0);
});

test("offline: a tombstone wins under FINAL for the plan's own row only", async (t) => {
  const fake = await fakeClickHouse();
  t.after(fake.close);
  const plan = twoProjects();
  const { attachments, info } = recorder();
  await lib.seedSharedSession(info, plan, fake.ch);
  assert.deepEqual(await lib.tombstoneSeededSession(info, plan, PA, fake.ch), {
    tombstoned: true,
  });
  assert.deepEqual(
    (await lib.liveSessionRows(plan.traceSessionId, { ch: fake.ch })).map(
      (r) => r.project_id,
    ),
    [PB],
  );
  assert.deepEqual(
    await lib.liveSessionRows(plan.traceSessionId, {
      projectId: PA,
      ch: fake.ch,
    }),
    [],
  );
  const tombstone = fake.final().find((row) => row.project_id === PA);
  assert.equal(tombstone.e2e_seed_id, plan.seedId);
  assert.equal(tombstone.version, plusMicro(plan.version));
  // Compaction can remove the live row; the marked tombstone must still be ours.
  fake.state.sessions = fake.final();
  assert.deepEqual(await lib.tombstoneSeededSession(info, plan, PA, fake.ch), {
    tombstoned: false,
  });
  // Re-seeding the same plan never resurrects the deleted row.
  const inserts = fake.inserts().length;
  await lib.seedSharedSession(info, plan, fake.ch);
  assert.equal(fake.inserts().length, inserts);
  assert.deepEqual(
    (await lib.liveSessionRows(plan.traceSessionId, { ch: fake.ch })).map(
      (r) => r.project_id,
    ),
    [PB],
  );
  await assert.rejects(
    lib.tombstoneSeededSession(info, plan, PC, fake.ch),
    /not in seed/,
  );
  assert.deepEqual(
    attachments.map((a) => a.name.split("-").slice(0, 3).join("-")),
    [
      "seeded-clickhouse-seed",
      "seeded-clickhouse-tombstone",
      "seeded-clickhouse-tombstone",
      "seeded-clickhouse-seed",
    ],
  );
});

test("offline: shadowing a collector session leaves the collector's row alone", async (t) => {
  const sessionId = "0f0f0f0f-0000-5000-8000-00000000000f";
  const collectorRow = {
    project_id: PC,
    trace_session_id: sessionId,
    external_session_id: "chat-7",
    first_seen: lib.clickHouseTime(NOW.getTime() - 5_000),
    version: lib.clickHouseTime(NOW.getTime() - 5_000),
    is_deleted: 0,
  };
  const fake = await fakeClickHouse({ sessions: [collectorRow] });
  t.after(fake.close);
  const shadow = lib.planSharedSession(
    {
      orgId: ORG,
      traceSessionId: sessionId,
      externalSessionId: "chat-7",
      projects: [{ projectId: PB }],
    },
    NOW,
  );
  await lib.seedSharedSession(recorder().info, shadow, fake.ch);
  assert.deepEqual(
    (await lib.liveSessionRows(sessionId, { ch: fake.ch })).map(
      (r) => r.project_id,
    ),
    [PB, PC].sort(),
  );
  assert.deepEqual(
    fake.final().find((r) => r.project_id === PC),
    collectorRow,
  );
  await assert.rejects(
    lib.tombstoneSeededSession(
      recorder().info,
      lib.planSharedSession(
        {
          orgId: ORG,
          traceSessionId: sessionId,
          externalSessionId: "chat-7",
          projects: [{ projectId: PC }],
        },
        NOW,
      ),
      PC,
      fake.ch,
    ),
    /holds no row seeded/,
  );
  assert.deepEqual(
    fake.final().find((r) => r.project_id === PC),
    collectorRow,
  );
});

test("offline: the unkeyed-resolver probe names the planned project whose traces it served", async () => {
  const plan = twoProjects();
  const calls = [];
  const api = (traceIds) => ({
    post: async (path, data) => {
      calls.push({ path, data });
      return { result: { response: traceIds.map((id) => ({ trace_id: id })) } };
    },
  });
  assert.equal(
    await lib.projectServedByUnkeyedSessionRead(
      api(plan.projects[1].traceIds),
      plan,
    ),
    PB,
  );
  assert.equal(
    await lib.projectServedByUnkeyedSessionRead(
      api([plan.projects[0].traceIds[0]]),
      plan,
    ),
    PA,
  );
  // Traces of no seeded project (shadow mode: the collector's) or of both read as null.
  assert.equal(
    await lib.projectServedByUnkeyedSessionRead(
      api(["00000000-0000-4000-8000-000000000001"]),
      plan,
    ),
    null,
  );
  assert.equal(
    await lib.projectServedByUnkeyedSessionRead(
      api(plan.projects.flatMap((p) => p.traceIds)),
      plan,
    ),
    null,
  );
  assert.deepEqual(calls[0], {
    path: `/tracer/trace-session/${plan.traceSessionId}/query/`,
    data: { page_number: 0, page_size: 50 },
  });
});

test("offline: every receipt is its own attachment", async (t) => {
  const fake = await fakeClickHouse();
  t.after(fake.close);
  const plan = twoProjects();
  const { attachments, info } = recorder();
  await lib.seedSharedSession(info, plan, fake.ch);
  await lib.seedSharedSession(info, plan, fake.ch);
  await lib.tombstoneSeededSession(info, plan, PA, fake.ch);
  assert.deepEqual(
    attachments.map((a) => a.name),
    [
      `seeded-clickhouse-seed-${plan.seedId}-1.json`,
      `seeded-clickhouse-seed-${plan.seedId}-2.json`,
      `seeded-clickhouse-tombstone-${plan.seedId}-3.json`,
    ],
  );
  assert.deepEqual(
    attachments.map((a) => JSON.parse(a.body).tables.spans.inserted),
    [6, 0, 0],
  );
});
