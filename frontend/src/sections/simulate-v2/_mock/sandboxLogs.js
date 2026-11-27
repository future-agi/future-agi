/**
 * Sandbox logs: what ran inside each sandbox, step by step.
 *
 * Shaped like what the backend already keeps for every sandbox attempt
 * (simulate/services/hosted_harness_diagnostics.py): the entrypoint command's
 * output, the build, world and worker *.log files, the exit code, and the
 * `log` events (debug / info / warning / error), all redacted before they are
 * stored. Sample data here; the shape is the contract.
 *
 * One log = { kind, sandbox, steps }:
 *   sandbox — id, size, image, started / ended / cleaned-up times, kept days
 *   steps   — [{ id, label, status, startMs, ms, lines }]
 *   lines   — [{ id, t, level, source, text, trialKey?, turn?, world? }]
 *             t is ms from sandbox start; source is "agent" (your code's
 *             stdout / stderr), "system" (our harness) or "world" (the
 *             simulated world and its actors).
 *
 * A live log is cut at `now`: steps after it are pending, the current one is
 * running and shows only the lines written so far.
 */

import { definitionOf, PROVIDER_LABELS } from "./agentRegistry";
import { hashSeed } from "./runStream";
import { causeOf } from "./decoupled";
import { versionBuildProgress } from "./versionBuild";

export const LOG_KEPT_DAYS = 30;

/* ── small helpers ───────────────────────────────────────────────────────── */

const rngOf = (seed) => {
  let a = hashSeed(String(seed)) >>> 0;
  return () => {
    a += 0x6d2b79f5;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
};
const pick = (rand, list) =>
  list[Math.floor(rand() * list.length) % list.length];
const hex = (rand, n) =>
  Array.from(
    { length: n },
    () => "0123456789abcdef"[Math.floor(rand() * 16)],
  ).join("");
const toMs = (v) => (v ? new Date(v).getTime() : null);

/** Where the agent's own code runs during a run or build. */
export function agentHostOf(agent) {
  const v = agent?.values || {};
  const def = definitionOf(agent);
  if (v.provider && v.provider !== "other" && v.agentId)
    return {
      kind: "platform",
      label: PROVIDER_LABELS[v.provider] || "its platform",
    };
  if (v.endpoint || v.sdkEndpoint || v.phoneNumber || v.mcpUrl || v.dialOut)
    return {
      kind: "endpoint",
      label: v.phoneNumber ? "its phone number" : "its own server",
      where: v.endpoint || v.sdkEndpoint || v.mcpUrl || v.phoneNumber || "",
    };
  if (["repo", "upload", "framework"].includes(def.kind))
    return { kind: "sandbox", label: "this sandbox" };
  return { kind: "builtin", label: "Future AGI" };
}

/* What a code agent looks like on disk: language, entry, dependencies. */
function codeOf(agent) {
  const def = definitionOf(agent);
  const v = agent?.values || {};
  const repo = def.repoUrl
    ? def.repoUrl.replace(/^https?:\/\/github\.com\//, "")
    : null;
  const fw = String(def.framework || v.framework || "").toLowerCase();
  const node = /mastra|vercel|typescript|node/.test(fw);
  const deps = node
    ? ["@mastra/core", "zod", "openai", "dotenv"]
    : fw.includes("crew")
      ? ["crewai", "openai", "pydantic", "httpx"]
      : fw.includes("adk") || fw.includes("google")
        ? ["google-adk", "google-genai", "pydantic", "httpx"]
        : ["langgraph", "langchain-openai", "pydantic", "httpx"];
  return {
    repo,
    ref: def.ref || "main",
    folder: v.folderName || (repo ? repo.split("/").pop() : "agent"),
    node,
    entry: def.entry || v.entry || (node ? "src/index.ts" : "agent/main.py"),
    deps,
    keys: (v.envNames?.length ? v.envNames : ["OPENAI_API_KEY"]).slice(0, 3),
  };
}

/* Builds a step list on one timeline. Each step: [id, label, ms, lines(t0)]. */
function timeline(specs, startAt = 0) {
  let t = startAt;
  let n = 0;
  return specs.map(([id, label, ms, make, extra]) => {
    const startMs = t;
    const lines = (make ? make(startMs, ms) : []).map((l) => ({
      id: `${id}-${(n += 1)}`,
      level: "info",
      source: "system",
      ...l,
    }));
    t += ms;
    return { id, label, startMs, ms, status: "done", lines, ...extra };
  });
}

/* Spreads texts evenly over a step's span. */
const spread = (t0, ms, rows) =>
  rows.map((r, i) => ({
    t: Math.round(t0 + ((i + 0.5) / rows.length) * ms),
    ...(typeof r === "string" ? { text: r } : r),
  }));

/**
 * Cuts a finished-shape log at `elapsed` ms for a live view, or marks a step
 * failed. `failStepId` halts the log there with its error lines kept.
 */
function settle(steps, { elapsed = null, failStepId = null } = {}) {
  let halted = false;
  return steps.map((s) => {
    /* Clean-up runs even after a failure: the sandbox is always removed. */
    if (halted && /^clean/i.test(s.label) && elapsed == null) return s;
    if (halted) return { ...s, status: "pending", lines: [] };
    if (failStepId && s.id === failStepId) {
      halted = true;
      return { ...s, status: "failed" };
    }
    if (elapsed == null) return s;
    if (elapsed >= s.startMs + s.ms) return s;
    if (elapsed < s.startMs) return { ...s, status: "pending", lines: [] };
    return {
      ...s,
      status: "running",
      lines: s.lines.filter((l) => l.t <= elapsed),
    };
  });
}

const sandboxFacts = (seed, startedAt, totalMs, { live, failed, size }) => {
  const rand = rngOf(seed);
  const started = startedAt || Date.now() - totalMs - 60000;
  const ended = live ? null : started + totalMs;
  return {
    id: `sbx_${hex(rand, 10)}`,
    cpu: size?.cpu || 2,
    memoryGb: size?.memoryGb || 4,
    diskGb: size?.diskGb || 10,
    startedAt: started,
    endedAt: ended,
    cleanedAt: ended ? ended + 4000 : null,
    exitCode: live ? null : failed ? 1 : 0,
    keptDays: LOG_KEPT_DAYS,
  };
};

/* Lines for starting the customer's code in a sandbox. */
function startAgentSteps(agent, rand, { failAt = null } = {}) {
  const code = codeOf(agent);
  const installCmd = code.node
    ? "npm ci --omit=dev"
    : "pip install -r requirements.txt";
  const fail = (id) => failAt === id;
  return [
    [
      "fetch",
      code.repo ? "Clone repository" : "Unpack upload",
      code.repo ? 2400 : 1200,
      (t0, ms) =>
        spread(
          t0,
          ms,
          code.repo
            ? [
                `git clone --depth 1 --branch ${code.ref} https://github.com/${code.repo}.git /work/agent`,
                `Checked out ${code.ref} @ ${hex(rand, 7)}`,
                {
                  text: `${80 + (hashSeed(code.repo || code.folder) % 200)} files · secrets files left out`,
                  source: "system",
                },
              ]
            : [
                `Unpacking ${code.folder}.tar.gz → /work/agent`,
                `${60 + (hashSeed(code.folder) % 120)} files · .env and node_modules left out`,
              ],
        ),
    ],
    [
      "install",
      "Install dependencies",
      fail("install") ? 9000 : 14000,
      (t0, ms) =>
        spread(t0, ms, [
          { text: `$ ${installCmd}`, source: "agent" },
          ...code.deps.map((d) => ({
            text: code.node
              ? `added ${d}@${1 + Math.floor(rand() * 4)}.${Math.floor(rand() * 20)}.${Math.floor(rand() * 9)}`
              : `Collecting ${d}`,
            source: "agent",
          })),
          ...(fail("install")
            ? [
                {
                  text: code.node
                    ? "npm ERR! code ERESOLVE · could not resolve peer dependency zod@^4"
                    : "ERROR: Could not find a version that satisfies the requirement langgraph==0.9.12",
                  level: "error",
                  source: "agent",
                },
                { text: "Exited with code 1", level: "error" },
              ]
            : [
                {
                  text: code.node
                    ? "audited 412 packages in 9s"
                    : "Successfully installed 37 packages",
                  source: "agent",
                },
                {
                  text: code.node
                    ? "npm WARN deprecated inflight@1.0.6: not supported"
                    : "WARNING: pip is being invoked by an old script wrapper",
                  level: "warn",
                  source: "agent",
                },
              ]),
        ]),
    ],
    [
      "start",
      "Start agent",
      fail("start") ? 5000 : 3600,
      (t0, ms) =>
        spread(t0, ms, [
          {
            text: `$ ${code.node ? `node ${code.entry}` : `python ${code.entry}`}`,
            source: "agent",
          },
          {
            text: `Loaded ${code.keys.map((k) => `${k}=••••••`).join(", ")}`,
            source: "system",
          },
          ...(fail("start")
            ? [
                {
                  text: code.node
                    ? "Error: Cannot find module 'shopify-api-node'"
                    : "ModuleNotFoundError: No module named 'shopify'",
                  level: "error",
                  source: "agent",
                },
                {
                  text: code.node
                    ? `    at Object.<anonymous> (${code.entry}:3:17) · not in package.json`
                    : `  File "/work/agent/${code.entry}", line 3 · not in requirements.txt`,
                  level: "error",
                  source: "agent",
                },
                {
                  text: "Agent exited with code 1 before it was ready",
                  level: "error",
                },
              ]
            : [
                {
                  text: code.node
                    ? "Agent server listening on :8080"
                    : "INFO:     Uvicorn running on http://0.0.0.0:8080",
                  source: "agent",
                },
              ]),
        ]),
    ],
    [
      "ready",
      "Readiness check",
      1200,
      (t0, ms) =>
        spread(t0, ms, ["GET /health → 200 in 38 ms", "Agent is ready"]),
    ],
  ];
}

/* ── connect check (preflight) and agent scan ──────────────────────────── */

/**
 * The check New agent runs before connecting a code agent: fetch, install,
 * start, readiness. `failAt` ("install" | "start") stops it there.
 */
export function checkLogOf({
  agent,
  startedAt = null,
  failAt = null,
  seed = "check",
}) {
  const rand = rngOf(`${seed}:${definitionOf(agent).repoUrl || "upload"}`);
  const steps = timeline([
    [
      "sandbox",
      "Start sandbox",
      1800,
      (t0, ms) => spread(t0, ms, ["Sandbox started · 2 CPU · 4 GB"]),
    ],
    ...startAgentSteps(agent, rand, { failAt }),
    [
      "cleanup",
      "Clean up",
      900,
      (t0, ms) => spread(t0, ms, ["Sandbox removed"]),
    ],
  ]);
  const failStepId = failAt;
  const total = steps.reduce((n, s) => n + s.ms, 0);
  return {
    kind: "check",
    title: "Connection check",
    sandbox: sandboxFacts(`${seed}:sbx`, startedAt, total, {
      failed: !!failAt,
    }),
    steps: settle(steps, { failStepId }),
  };
}

/** Reading the code for agents (New agent → Find agents). */
export function scanLogOf({
  agent,
  found = [],
  startedAt = null,
  seed = "scan",
}) {
  const code = codeOf(agent);
  const rand = rngOf(`${seed}:${code.folder}`);
  const names = found.length ? found : [code.entry];
  const steps = timeline([
    [
      "sandbox",
      "Start sandbox",
      1500,
      (t0, ms) =>
        spread(t0, ms, ["Sandbox started · 1 CPU · 2 GB · no network"]),
    ],
    startAgentSteps(agent, rand)[0],
    [
      "read",
      "Read the code",
      5200,
      (t0, ms) =>
        spread(t0, ms, [
          "Reading the source files",
          `Looking for agent entry points in ${code.node ? "TypeScript" : "Python"} files`,
          ...names.map((n) => `Found agent entry ${n}`),
          {
            text: "tests/fixtures/agent_stub.py looks like an agent · skipped (test file)",
            level: "warn",
          },
          `Found ${names.length} agent${names.length === 1 ? "" : "s"}`,
        ]),
    ],
    [
      "cleanup",
      "Clean up",
      800,
      (t0, ms) => spread(t0, ms, ["Sandbox removed"]),
    ],
  ]);
  const total = steps.reduce((n, s) => n + s.ms, 0);
  return {
    kind: "scan",
    title: "Reading the code",
    sandbox: sandboxFacts(`${seed}:sbx`, startedAt, total, {
      size: { cpu: 1, memoryGb: 2 },
    }),
    steps,
  };
}

/* ── environment build ─────────────────────────────────────────────────── */

const BUILD_STOP_STEP = {
  "fail-world": "s4",
  "fail-scenarios": "s7",
  "fail-checks": "s8",
  "fail-ours": "s11",
};

/**
 * A first (or version) build of an environment, from its `versionBuild`
 * record. Live while the build runs; halts at the step a stopped build
 * stopped at, with that step's error lines.
 */
export function buildLogOf({ build, agent, now = Date.now() }) {
  if (!build?.steps?.length) return null;
  const host = agentHostOf(agent);
  const stopCase = build.stop?.case || null;
  const lineFor = (step, i) => {
    const kind = step.kind;
    const name = step.label;
    const total = step.total || 1;
    const base = [`${name} · ${step.detail || "started"}`];
    if (kind === "rules" || kind === "world")
      return [
        ...base,
        `Read ${total} rule${total === 1 ? "" : "s"} from the contract`,
        "Wrote world schema · 6 tables",
      ];
    if (kind === "tools")
      return [
        ...base,
        ...Array.from({ length: Math.min(total, 4) }, (_, k) => ({
          text: `handler ${k + 1}/${total} written`,
          source: "world",
        })),
      ];
    if (kind === "world-check")
      return [
        ...base,
        {
          text: `${total} tool${total === 1 ? "" : "s"} answered on real state`,
          source: "world",
        },
        ...(stopCase === "fail-world"
          ? [
              {
                text: "lookup_order returned a record that isn't in the orders table",
                level: "error",
                source: "world",
              },
              {
                text: "World check failed · 1 tool invents data",
                level: "error",
              },
            ]
          : []),
      ];
    if (kind === "tables")
      return [
        ...base,
        {
          text: "Seeded edge-case rows: refunds over limit, expired cards",
          source: "world",
        },
      ];
    if (kind === "scenarios")
      return [...base, `Drafted ${total} scenarios with sub-goals`];
    if (kind === "scenario-check")
      return [
        ...base,
        ...(stopCase === "fail-scenarios"
          ? [
              {
                text: `3 of ${total} scenarios can't be solved with these tools`,
                level: "error",
              },
            ]
          : [{ text: `${total} scenarios ready, solvable, not vacuous` }]),
      ];
    /* release checks: connect / run / grade / upload / clean up */
    if (name === "Connecting agent") {
      if (host.kind === "sandbox")
        return stopCase === "fail-checks"
          ? [
              { text: "$ pip install -r requirements.txt", source: "agent" },
              { text: "Successfully installed 37 packages", source: "agent" },
              { text: "$ python agent/main.py", source: "agent" },
              {
                text: "WARNING: PORT not set, listening on 127.0.0.1:5000",
                level: "warn",
                source: "agent",
              },
              "GET :8080/health → connection refused (try 1 of 3)",
              "GET :8080/health → connection refused (try 3 of 3)",
              {
                text: "Couldn't reach the agent on :8080 within 60 s",
                level: "error",
              },
            ]
          : [
              { text: "$ pip install -r requirements.txt", source: "agent" },
              { text: "Successfully installed 37 packages", source: "agent" },
              {
                text: "INFO:     Uvicorn running on http://0.0.0.0:8080",
                source: "agent",
              },
              "Agent is ready",
            ];
      return stopCase === "fail-checks"
        ? [{ text: `${host.label} didn't answer within 30 s`, level: "error" }]
        : [`Calling your agent on ${host.label}`, "First reply in 812 ms"];
    }
    if (name === "Running")
      return [
        `Playing ${total} scenarios`,
        { text: "Worlds w1–w4 started", source: "world" },
      ];
    if (name === "Grading") return [`Grading ${total} trials`];
    if (name === "Uploading artifacts")
      return stopCase === "fail-ours"
        ? [
            {
              text: "Upload to storage timed out after 3 retries",
              level: "error",
            },
          ]
        : ["Saved traces, transcripts and tool logs"];
    if (name === "Cleaning up") return ["Sandbox removed"];
    return [
      ...base,
      i % 3 === 0 ? { text: "debug: checkpoint saved", level: "debug" } : null,
    ].filter(Boolean);
  };
  const specs = [
    [
      "s0",
      "Start sandbox",
      1500,
      (t0, ms) => spread(t0, ms, ["Sandbox started · 4 CPU · 8 GB"]),
    ],
    ...build.steps.map((s, i) => [
      `s${s.no || i + 2}`,
      s.label,
      s.ms || 2000,
      (t0, ms) => spread(t0, ms, lineFor(s, i)),
    ]),
  ];
  const steps = timeline(specs);
  const total = steps.reduce((n, s) => n + s.ms, 0);
  const failStepId = stopCase ? BUILD_STOP_STEP[stopCase] || null : null;
  /* Where the build is, read the way its progress strip reads it, so the
     log and the strip always name the same step. */
  const prog = versionBuildProgress(build, now);
  const live = !!prog && !prog.done && !prog.stopped && !build.stoppedAt;
  const at = live ? steps[prog.step + 1] : null;
  const elapsed = at ? at.startMs + prog.fraction * at.ms : null;
  return {
    kind: "build",
    title: `Build ${build.shownLabel || build.label || ""}`.trim(),
    live,
    sandbox: sandboxFacts(`build:${build.at}:sbx`, build.at - 1500, total, {
      live,
      failed: !!failStepId,
      size: { cpu: 4, memoryGb: 8 },
    }),
    steps: settle(steps, { elapsed, failStepId }),
  };
}

/* ── run ───────────────────────────────────────────────────────────────── */

export const trialKeyOf = (taskId, idx = 0) => `${taskId}#${idx}`;

const AGENT_TURN_LINES = [
  (r) => `POST /chat 200 in ${300 + Math.floor(r() * 900)} ms`,
  (r) =>
    `tool call lookup_order(order_id="A-${10000 + Math.floor(r() * 9000)}")`,
  (r) => `model gpt-4o · ${400 + Math.floor(r() * 1600)} tokens`,
  () => "tool call check_refund_policy()",
];
/* A voice agent's turn: speech in, model, speech out. */
const VOICE_TURN_LINES = [
  (r) => `STT final in ${150 + Math.floor(r() * 250)} ms`,
  (r) => `LLM reply in ${400 + Math.floor(r() * 900)} ms`,
  (r) => `TTS first audio in ${250 + Math.floor(r() * 400)} ms`,
  (r) =>
    `tool call lookup_order(order_id="A-${10000 + Math.floor(r() * 9000)}")`,
];
const INFRA_ERRORS = {
  "inc-infra": [
    "sandbox: worker exited with code 137 (out of memory)",
    "harness: lost contact with the trial process",
  ],
  "inc-transport": [
    "ConnectionResetError: connection to the agent dropped",
    "harness: no reply in 30 s · giving up on this turn",
  ],
};
const AGENT_ERRORS = [
  [
    "Traceback (most recent call last):",
    '  File "/work/agent/tools.py", line 88, in issue_refund',
    "KeyError: 'amount'",
  ],
  ["openai.RateLimitError: 429 · retrying in 2 s"],
  ["TimeoutError: tool lookup_order took longer than 15 s"],
];

/**
 * A run's sandbox: setup (fetch, install, start the agent and the worlds),
 * every trial's lines tagged with its trial and turn, then grading, upload
 * and clean-up. `tasks` are the run page's scenario rows.
 */
export function runLogOf({ run, agent, tasks = [], now = Date.now() }) {
  if (!run) return null;
  const rand = rngOf(`run:${run.id}`);
  const host = agentHostOf(agent);
  const live =
    run.status === "running" ||
    (!run.finishedAt && run.status !== "failed" && run.status !== "completed");
  const lanes = Math.max(1, Math.min(4, Number(run.parallelism) || 4));
  const voice = run.surface === "voice" || agent?.values?.contact?.mode;
  const turnLines = voice ? VOICE_TURN_LINES : AGENT_TURN_LINES;

  /* One entry per trial, scheduled across the world lanes. */
  const trials = [];
  tasks.forEach((task) => {
    const n = Math.max(1, (task.samples || []).length || 1);
    for (let k = 0; k < n; k += 1) {
      const sample = (task.samples || [])[k];
      const failed = sample ? sample !== "passed" : task.status === "failed";
      const cause = causeOf(task);
      trials.push({
        infra: INFRA_ERRORS[cause] ? cause : null,
        key: trialKeyOf(task.id, k),
        title: task.title || task.name || task.id,
        index: k,
        count: n,
        failed: failed || !!INFRA_ERRORS[cause],
        turns: Math.max(
          2,
          (task.steps || []).filter((s) => s.role === "agent").length || 4,
        ),
        ms: Math.round(task.durationMs || 60000),
      });
    }
  });

  const setupSpecs = [
    [
      "sandbox",
      "Start sandbox",
      1800,
      (t0, ms) => spread(t0, ms, ["Sandbox started · 4 CPU · 8 GB"]),
    ],
    ...(host.kind === "sandbox"
      ? startAgentSteps(agent, rand)
      : [
          [
            "reach",
            "Reach your agent",
            1600,
            (t0, ms) =>
              spread(t0, ms, [
                host.kind === "platform"
                  ? `Your agent runs on ${host.label} · its own logs stay there`
                  : host.kind === "endpoint"
                    ? `Your agent runs on ${host.label}${host.where ? ` (${host.where})` : ""} · we log each call to it`
                    : "Your agent runs in Future AGI on our model",
                "First reply in 640 ms",
              ]),
          ],
        ]),
    [
      "worlds",
      "Start worlds",
      2600,
      (t0, ms) =>
        spread(t0, ms, [
          ...Array.from({ length: lanes }, (_, i) => ({
            text: `World w${i + 1} ready · seeded from release`,
            source: "world",
          })),
          {
            text: `${trials.length} trials on ${lanes} worlds`,
            source: "system",
          },
        ]),
    ],
  ];
  const setup = timeline(setupSpecs);
  const setupMs = setup.reduce((n, s) => n + s.ms, 0);

  /* Trials: each one's world and start come from its own key, not from the
     other trials, so one trial's lines read the same on the run page and in
     its own drawer. */
  const trialLines = [];
  const trialSpans = {};
  let trialsEndAt = setupMs + 1000;
  trials.forEach((tr) => {
    const slot = rngOf(`${run.id}:${tr.key}:slot`);
    const lane = Math.floor(slot() * lanes);
    const t0 = setupMs + Math.round(slot() * 12000);
    const world = `w${lane + 1}`;
    const per = tr.ms / (tr.turns + 1);
    const r = rngOf(`${run.id}:${tr.key}`);
    /* An infrastructure failure ends the conversation, so it lands on the
       last turn; an agent error can come at any turn after the first. */
    const errTurn = tr.infra
      ? tr.turns - 1
      : tr.failed && r() < 0.6
        ? 1 + Math.floor(r() * (tr.turns - 1))
        : -1;
    const err =
      errTurn >= 0
        ? tr.infra
          ? INFRA_ERRORS[tr.infra]
          : pick(r, AGENT_ERRORS)
        : null;
    const push = (l) => trialLines.push({ world, trialKey: tr.key, ...l });
    push({
      t: t0,
      text: `Trial started · ${tr.title}${tr.count > 1 ? ` · trial ${tr.index + 1} of ${tr.count}` : ""}`,
    });
    for (let turn = 0; turn < tr.turns; turn += 1) {
      const t = Math.round(t0 + per * (turn + 1));
      push({
        t: t - 40,
        turn,
        source: "world",
        text: voice
          ? `Caller spoke · turn ${turn + 1}`
          : `Caller turn ${turn + 1} sent`,
      });
      if (host.kind === "sandbox" || host.kind === "endpoint")
        push({
          t,
          turn,
          source: host.kind === "sandbox" ? "agent" : "system",
          text: pick(r, turnLines)(r),
        });
      if (turn === errTurn && err)
        err.forEach((text, k) =>
          push({
            t: t + 20 + k,
            turn,
            level: text.includes("retrying") ? "warn" : "error",
            source: tr.infra || host.kind !== "sandbox" ? "system" : "agent",
            text,
          }),
        );
    }
    const end = Math.round(t0 + tr.ms);
    push({
      t: end,
      level: tr.failed ? "warn" : "info",
      text: `Trial ended · ${tr.failed ? "failed" : "passed"} · ${(tr.ms / 1000).toFixed(1)} s`,
    });
    trialSpans[tr.key] = { startMs: t0, endMs: end, world, errTurn };
    trialsEndAt = Math.max(trialsEndAt, end + 400);
  });
  const trialsEnd = trialsEndAt;
  const trialStep = {
    id: "trials",
    label: `Run ${trials.length} trial${trials.length === 1 ? "" : "s"}`,
    startMs: setupMs,
    ms: trialsEnd - setupMs,
    status: "done",
    lines: trialLines
      .sort((a, b) => a.t - b.t)
      .map((l, i) => ({
        id: `tr-${i}`,
        level: "info",
        source: "system",
        ...l,
      })),
  };
  const tail = timeline(
    [
      [
        "grade",
        "Grade",
        3200,
        (t0, ms) => spread(t0, ms, [`Graded ${trials.length} trials`]),
      ],
      [
        "upload",
        "Upload results",
        1500,
        (t0, ms) => spread(t0, ms, ["Saved traces, transcripts and tool logs"]),
      ],
      [
        "cleanup",
        "Clean up",
        900,
        (t0, ms) => spread(t0, ms, ["Sandbox removed · cleanup verified"]),
      ],
    ],
    trialsEnd,
  );
  const steps = [...setup, trialStep, ...tail];
  const total = steps.reduce((n, s) => n + s.ms, 0);
  const started = toMs(run.startedAt) || Date.now() - total;
  const elapsed = live ? Math.max(0, now - started) : null;
  return {
    kind: "run",
    title: run.label || `Run #${run.ordinal || ""}`.trim(),
    live: !!live,
    host,
    trialSpans,
    sandbox: sandboxFacts(`run:${run.id}:sbx`, started, total, {
      live,
      size: { cpu: 4, memoryGb: 8 },
    }),
    steps: settle(steps, { elapsed }),
  };
}

/** One trial's lines out of its run's log, with the turn each one belongs to. */
export function trialLogOf(runLog, taskId, idx = 0) {
  if (!runLog) return null;
  const key = trialKeyOf(taskId, idx);
  const span = runLog.trialSpans?.[key];
  if (!span) return null;
  const lines = runLog.steps
    .flatMap((s) => s.lines)
    .filter((l) => l.trialKey === key);
  return {
    kind: "trial",
    title: "This trial",
    live: runLog.live,
    host: runLog.host,
    span,
    sandbox: runLog.sandbox,
    steps: [
      {
        id: "trial",
        label: `Trial on world ${span.world}`,
        startMs: span.startMs,
        ms: span.endMs - span.startMs,
        status: lines.some((l) => l.level === "error") ? "failed" : "done",
        lines,
      },
    ],
  };
}

/** Turns of a trial that logged an error: { [turn]: firstErrorLineId }. */
export function errorTurnsOf(trialLog) {
  const out = {};
  (trialLog?.steps || []).forEach((s) =>
    s.lines.forEach((l) => {
      if (l.level === "error" && l.turn != null && out[l.turn] == null)
        out[l.turn] = l.id;
    }),
  );
  return out;
}

/* ── Epoch engine ──────────────────────────────────────────────────────── */

/**
 * The improvement engine's sandbox for one Epoch: it reads the agent, tries
 * changes, builds each candidate and runs the held-back scenarios on it.
 */
export function engineLogOf({
  improvement,
  candidates = [],
  startedAt = null,
  live = false,
  now = Date.now(),
  /* Ended on our side: the last step it reached stops on an error. */
  failed = false,
}) {
  const id = improvement?.id || "epoch";
  const rand = rngOf(`engine:${id}`);
  const cands = candidates.length
    ? candidates
    : [{ name: "Candidate A" }, { name: "Candidate B" }];
  const steps = timeline([
    [
      "sandbox",
      "Start sandbox",
      1800,
      (t0, ms) =>
        spread(t0, ms, ["Sandbox started · 4 CPU · 8 GB · repo read-only"]),
    ],
    [
      "read",
      "Read the agent",
      6000,
      (t0, ms) =>
        spread(t0, ms, [
          "Reading prompts, tools and guardrails",
          "Baseline run on the failing scenarios",
        ]),
    ],
    ...cands.map((c, i) => [
      `cand-${i}`,
      `Try ${c.name || `candidate ${i + 1}`}`,
      9000,
      (t0, ms) =>
        spread(t0, ms, [
          `Editing ${c.file || c.changes?.[0]?.file || "prompts/system.md"}`,
          { text: "$ pytest -q tests/", source: "agent" },
          {
            text: `${20 + Math.floor(rand() * 20)} passed in 4.1s`,
            source: "agent",
          },
          "Running the target and held-back scenarios",
          /* Its own result, as the Candidates tab reads it. */
          c.why
            ? { text: `Not kept: ${c.why}`, level: "warn" }
            : c.score != null
              ? `Scored ${c.score}%${c.delta ? ` (${c.delta > 0 ? "+" : ""}${c.delta} pts)` : ""}`
              : "Scores saved for this candidate",
        ]),
    ]),
    [
      "cleanup",
      "Clean up",
      900,
      (t0, ms) => spread(t0, ms, ["Sandbox removed"]),
    ],
  ]);
  if (failed) {
    const fs = steps.find((s) => s.id === failStepIdOf(steps));
    const at = fs.lines.length
      ? fs.lines[fs.lines.length - 2]?.t ?? fs.startMs
      : fs.startMs;
    fs.lines = [
      ...fs.lines.filter((l) => l.t <= at),
      ...[
        "sandbox: worker exited with code 137 (out of memory)",
        "harness: lost contact with the build process · stopping this epoch",
      ].map((text, k) => ({
        id: `${fs.id}-err-${k}`,
        t: at + 200 + k * 40,
        level: "error",
        source: "system",
        text,
      })),
    ];
  }
  const total = steps.reduce((n, s) => n + s.ms, 0);
  const started = toMs(startedAt) || Date.now() - total - 120000;
  return {
    kind: "engine",
    title: "Improvement engine",
    live,
    sandbox: sandboxFacts(`engine:${id}:sbx`, started, total, {
      live,
      size: { cpu: 4, memoryGb: 8 },
    }),
    steps: settle(steps, {
      elapsed: live ? Math.max(0, now - started) : null,
      failStepId: failed ? failStepIdOf(steps) : null,
    }),
  };
}

/* The engine step an infrastructure failure lands on: the last candidate. */
function failStepIdOf(steps) {
  const tries = steps.filter((s) => s.id.startsWith("cand-"));
  return (tries[tries.length - 1] || steps[1] || steps[0]).id;
}

/* ── reading a log ─────────────────────────────────────────────────────── */

/** The first error line of a log, with its step, or null. */
export function firstErrorOf(log) {
  for (const s of log?.steps || [])
    for (const l of s.lines)
      if (l.level === "error") return { step: s, line: l };
  return null;
}

/** A short excerpt around a log's first error, for failure banners. */
export function errorExcerptOf(log, before = 4, after = 2) {
  const hit = firstErrorOf(log);
  if (!hit) return null;
  const { step, line } = hit;
  const i = step.lines.indexOf(line);
  return {
    step,
    lines: step.lines.slice(Math.max(0, i - before), i + 1 + after),
  };
}

/** Plain text of a log, as the download gives it. */
export function logText(log, startedAt = log?.sandbox?.startedAt || 0) {
  const stamp = (t) => new Date(startedAt + t).toISOString();
  return (log?.steps || [])
    .flatMap((s) => [
      `## ${s.label} (${s.status})`,
      ...s.lines.map(
        (l) =>
          `${stamp(l.t)} ${l.level.toUpperCase().padEnd(5)} [${l.source}] ${l.text}`,
      ),
    ])
    .join("\n");
}
