// The Run details `Source` block (R2-SPEC §5.3.7): one line per fact, for
// all seven run kinds, plus the all-runs traces line. Pure.
import { runKindOf } from "src/sections/common/Runs/runKind";
import { tracingWord } from "src/sections/common/Runs/runTracing";
import { runModelOf, sandboxText } from "src/sections/common/Runs/runWhere";
import { fmtCost } from "src/sections/common/format/fmt";
import { SIDE_EFFECT_LABELS } from "src/sections/common/Runs/provenanceModel";
import { mcpEnvSecrets } from "src/sections/common/RunSources/endpoint/requestModel";

const join = (parts) => parts.filter(Boolean).join(" · ");

const fmt = (n) => Number(n || 0).toLocaleString("en-US");
// S5 money: 2 significant figures, at least 3 decimals below $0.10.
const money = (v) =>
  Number.isFinite(Number(v)) && Number(v) > 0 ? fmtCost(Number(v)) : null;

const hostOf = (url) => {
  try {
    return new URL(String(url)).host || null;
  } catch {
    return null;
  }
};

// Per-tool modes (`6 tools (mock)` / `6 tools: 4 mock, 2 live`) and the live
// endpoint's host when a live tool names one.
const toolsText = (tools = []) => {
  if (!tools.length) return null;
  const counts = new Map();
  tools.forEach((t) => {
    if (t?.mode) counts.set(t.mode, (counts.get(t.mode) ?? 0) + 1);
  });
  const noun = `${tools.length} ${tools.length === 1 ? "tool" : "tools"}`;
  const modes = [...counts.entries()];
  const live = [
    ...new Set(
      tools
        .filter((t) => t?.mode === "live")
        .map((t) => hostOf(t.endpoint?.url ?? t.url))
        .filter(Boolean),
    ),
  ];
  const head =
    modes.length === 1
      ? `${noun} (${modes[0][0]})`
      : modes.length
        ? `${noun}: ${modes.map(([m, k]) => `${k} ${m}`).join(", ")}`
        : noun;
  return live.length ? `${head} · live ${live.join(", ")}` : head;
};

// Changed params of a run (`model gpt-4o-mini · temperature 0.9`): values
// that differ from the declared default, `model` first.
export function changedParamsLine(c = {}) {
  const schema = c.params?.schema ?? [];
  const values = c.params?.values ?? {};
  const defaults = Object.fromEntries(
    schema.filter((p) => p?.name).map((p) => [p.name, p.default]),
  );
  const keys = Object.keys(values).filter(
    (k) =>
      values[k] != null &&
      values[k] !== "" &&
      (k in defaults
        ? String(values[k]) !== String(defaults[k])
        : schema.length === 0 && k === "model"),
  );
  keys.sort((a, b) => (a === "model" ? -1 : b === "model" ? 1 : 0));
  return keys.length ? keys.map((k) => `${k} ${values[k]}`).join(" · ") : null;
}

const REPLAY_WORDS = {
  score_only: "replay · score only",
  regenerate_turn: "replay · regenerate the failing turn",
  failing_turn: "replay · regenerate the failing turn",
  rollout: "replay · roll out",
  full: "replay · full re-run",
};
const replayText = (c = {}) =>
  c.replay?.mode
    ? REPLAY_WORDS[c.replay.mode] ?? `replay · ${c.replay.mode}`
    : null;

const PLATFORM = "Runs in Future AGI";

const instructionsText = (value) => {
  const v = String(value ?? "").trim();
  if (!v) return null;
  // Seeded configs carry `CLAUDE.md (412 words)`; created ones the text.
  const m = /^(\S+\.md)\s*\((\d[\d,]*) words\)$/.exec(v);
  if (m) return `${m[1]} ${m[2]} words`;
  const words = v.split(/\s+/).filter(Boolean).length;
  return `${fmt(words)} words of instructions`;
};

/**
 * The run's source line (first line of the block).
 * @param {object} run  canonical Run with `config` / `source` (R1)
 * @param {{taskSuite?: boolean, lastUploadText?: string,
 *   matchedRows?: number, totalRows?: number}} [ctx]
 */
export function sourceLine(run, ctx = {}) {
  const line = kindSourceLine(run, ctx);
  // options-truth L7b G4 (exp:output-format): the output format the run
  // recorded (a media run's `json`), on its source line.
  const format = run?.config?.output_format;
  return format ? join([line, `Output ${format}`]) : line;
}

/**
 * `MCP · acme-support-sandbox (stdio) · lookup_order · env
 * ACME_SANDBOX_TOKEN ← secret FI_PROJECT_KEY · Stubbed side effects`.
 */
export function mcpSourceLine(c = {}) {
  const m = c.mcp_tool ?? {};
  const server = m.server ?? {};
  const env = mcpEnvSecrets(server).filter((x) => x.name && x.secret);
  const name = server.name ?? server.url ?? server.command ?? null;
  return join([
    "MCP",
    name ? `${name}${server.transport ? ` (${server.transport})` : ""}` : null,
    m.tool ?? null,
    env.length
      ? `env ${env.map((x) => `${x.name} ← secret ${x.secret}`).join(", ")}`
      : null,
    SIDE_EFFECT_LABELS[c.safety?.side_effects ?? m.side_effects] ?? null,
  ]);
}

/** `A2A · Globex Help Desk · skill refund_request`. */
export function a2aSourceLine(c = {}) {
  const a = c.a2a ?? {};
  return join([
    "A2A",
    a.card?.name ?? hostOf(a.card_url) ?? null,
    a.skill_id ? `skill ${a.skill_id}` : null,
  ]);
}

function kindSourceLine(run, ctx = {}) {
  const c = run?.config ?? {};
  const kind = runKindOf(run);
  switch (kind) {
    case "prompt": {
      const tools = c.tools ?? [];
      return join([
        tools.length ? "Prompt + tools" : "Prompt",
        [
          [c.prompt?.name ?? run?.target?.prompt_name, c.prompt?.version]
            .filter(Boolean)
            .join(" "),
          c.model?.id ?? run?.target?.model,
        ]
          .filter(Boolean)
          .join(" × "),
        toolsText(tools),
        tools.length && c.max_tool_turns
          ? `max ${c.max_tool_turns} tool ${Number(c.max_tool_turns) === 1 ? "turn" : "turns"}`
          : null,
        replayText(c),
        PLATFORM,
      ]);
    }
    case "agent": {
      const a = c.agent ?? {};
      const mode = (c.tools ?? [])[0]?.mode;
      return join([
        "Agent",
        [
          a.name ?? run?.target?.graph_name,
          a.version ?? run?.target?.version_name,
        ]
          .filter(Boolean)
          .join(" ") + (a.status ? ` (${a.status})` : ""),
        a.nodes?.length ? `${a.nodes.length} nodes` : null,
        mode
          ? `tools ${mode}${mode === "live" && ctx.taskSuite ? " in sandbox" : ""}`
          : null,
        replayText(c),
        PLATFORM,
      ]);
    }
    case "code": {
      const code = c.code ?? {};
      // dataset integration FE-0: no command and no SDK version (neither exists yet;
      // FE-1 prints the real `futureagi run` line behind optionTruth).
      if (code.mode === "sdk_preview")
        return join([
          "Your code",
          code.host,
          code.commit
            ? `git ${code.commit}${code.dirty?.files?.length ? " (dirty)" : ""}`
            : null,
        ]);
      const done =
        (Number(run?.progress?.done) || 0) * (Number(run?.trials) || 1);
      return join([
        "Your code",
        `uploaded ${fmt(done)} ${done === 1 ? "result" : "results"}`,
        // options-truth L2 R1: the tile's word (traceAI / OpenTelemetry /
        // manual spans), never traceAI for every framework.
        tracingWord(run),
        sandboxText(c),
        changedParamsLine(c),
        ctx.lastUploadText ? `last upload ${ctx.lastUploadText}` : null,
      ]);
    }
    case "traces": {
      const t = c.traces ?? {};
      const match = t.match ?? {};
      const by =
        match.by === "input"
          ? `input = ${match.column ?? "column"}`
          : match.by === "session"
            ? `session id = ${match.column ?? "column"}`
            : match.key ?? "row_id";
      const where = [t.project, t.version].filter(Boolean).join(" › ");
      return join([
        "Traces I already have",
        where || null,
        `matched by ${by}`,
        Number.isFinite(ctx.matchedRows) && ctx.totalRows
          ? `${fmt(ctx.matchedRows)} of ${fmt(ctx.totalRows)} rows`
          : null,
      ]);
    }
    case "harness": {
      const h = c.harness ?? {};
      const l = c.limits ?? {};
      // The engine's effective limits (G1 writes them to config.limits).
      const perTry = money(l.max_cost_per_try_usd ?? h.max_cost_usd);
      const timeout = Number(l.timeout_s ?? h.timeout_s) || null;
      return join([
        [h.name, runModelOf(run) ?? h.model].filter(Boolean).join(" × "),
        sandboxText(c) ??
          (typeof h.sandbox === "string"
            ? String(h.sandbox).replace(" · ", " ")
            : null),
        h.max_turns ? `${h.max_turns} turns` : null,
        perTry ? `${perTry} per try` : null,
        timeout ? `timeout ${timeout} s` : null,
        instructionsText(h.instructions_file),
      ]);
    }
    case "http": {
      // options-truth L7b G4 (knob:mcp:env-secret): an MCP server tool or an
      // A2A agent run names what it called, in Review's words (runUses
      // endpoint.js), never `Endpoint · POST stdio:…`.
      if (c.kind === "mcp_tool") return mcpSourceLine(c);
      if (c.kind === "a2a") return a2aSourceLine(c);
      const t = run?.target ?? {};
      return join(["Endpoint", `${t.method ?? "POST"} ${t.url ?? ""}`.trim()]);
    }
    default: {
      const t = run?.target ?? {};
      return join([
        "Upload",
        t.branch && t.commit ? `${t.branch}@${t.commit}` : t.branch ?? null,
        "results uploaded by your code",
      ]);
    }
  }
}

/**
 * The all-runs line: `Traces: 90 · in "Support ops tasks · sandbox · runs"
 * · each cell's trace chip opens one`.
 * @param {object} run
 * @param {{datasetName?: string, matchedRows?: number}} [ctx]
 */
export function tracesLine(run, ctx = {}) {
  const kind = runKindOf(run);
  const trials = Number(run?.trials) || 1;
  // L7c g2 (source:running:http): Future AGI calls an endpoint, A2A agent
  // or MCP tool itself, so every try records its own trace (the default
  // count below); only an upload depends on the uploader's trace ids.
  if (kind === "upload")
    return "Traces: only when the upload carries a trace id";
  if (kind === "traces") {
    const project = run?.config?.traces?.project;
    const n = Number.isFinite(ctx.matchedRows)
      ? ctx.matchedRows
      : Number(run?.progress?.done) || 0;
    return join([
      `Traces: ${fmt(n)}`,
      project ? `in "${project}"` : null,
      "each cell's trace chip opens one",
    ]);
  }
  const done = (Number(run?.progress?.done) || 0) * trials;
  return join([
    `Traces: ${fmt(done)}`,
    ctx.datasetName ? `in "${ctx.datasetName} · runs"` : null,
    "each cell's trace chip opens one",
  ]);
}

/**
 * A harness run's live line while it is active (R2-SPEC §5.3.4):
 * `Starting sandboxes 5 of 5 · Running 12 rows · Checking 3`. Null once the
 * run is terminal.
 * @param {object} run  canonical Run (status, status_detail, progress,
 *   concurrency)
 * @param {{infra?: number}} [opts]  setup failures so far (not counted)
 */
export function harnessLiveLine(run, { infra = 0 } = {}) {
  if (runKindOf(run) !== "harness") return null;
  const status = String(run?.status ?? "");
  if (/Completed|Fail|Cancel|Stopped/i.test(status)) return null;
  const total = Number(run?.progress?.total ?? run?.examples) || 0;
  const done = Number(run?.progress?.done) || 0;
  const scored = Number(run?.progress?.scored ?? done) || 0;
  const slots = Math.max(1, Math.min(Number(run?.concurrency) || 5, 5));
  const left = Math.max(0, total - done);
  const infraText = infra ? `${fmt(infra)} setup failed (not counted)` : null;
  if (
    /Starting sandboxes/i.test(run?.status_detail ?? "") ||
    /Queued/i.test(status)
  ) {
    const k = Math.min(slots, left || slots);
    return join([
      `Starting sandboxes ${fmt(k)} of ${fmt(k)}`,
      `${fmt(total)} rows queued`,
      infraText,
    ]);
  }
  const running = Math.min(left, Number(run?.concurrency) || left);
  return join([
    `Starting sandboxes ${fmt(slots)} of ${fmt(slots)}`,
    `Running ${fmt(running)} ${running === 1 ? "row" : "rows"}`,
    `Checking ${fmt(Math.max(0, done - scored))}`,
    infraText,
  ]);
}
