import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import {
  CONNECT_STATES,
  TROUBLE_STATES,
  accessOf,
  checkParams,
  checklistOf,
  countedTries,
  elapsedMs,
  firstResultFast,
  fmtClock,
  heartbeatHealth,
  maskToken,
  needsLocalConfirm,
  oneShaPerRun,
  recipeWidens,
  scenarioId,
  stateFromEvents,
  tokenLeftMs,
  transition,
} from "../connectModel";
import {
  acceptsLine,
  firstResultLine,
  fmtAge,
  gitText,
  handshakeLine,
  heartbeatText,
  notAllowedLine,
  readSummary,
  reloadLine,
  rerunToast,
  resumeLabel,
  stoppedToast,
  stopConfirmOf,
  stripLine,
  tryLineText,
  waitingLine,
} from "../connectCopy";
import { MACHINES, DEMO_MACHINE } from "src/test-fixtures/connection-machines";
import { fmtMs } from "src/sections/common/format/fmt";
import { recipeEchoLine, tryEffectsLine } from "../connectCopy";

const alice = { ...MACHINES[0], you: false, may_use: true };
const ravi = { ...MACHINES[3], you: false, may_use: false };
const demo = { ...DEMO_MACHINE, you: true, may_use: true };
const agent = demo.agents[0];

describe("state machine (per scenario)", () => {
  it("ok: waiting → connecting → connected, heartbeats keep it", () => {
    const ev = [
      { type: "socket_open", at_ms: 0 },
      { type: "registered", at_ms: 900 },
      { type: "heartbeat", at_ms: 5900 },
    ];
    expect(stateFromEvents([])).toBe("waiting");
    expect(stateFromEvents([], { issued: false })).toBe("idle");
    expect(stateFromEvents(ev.slice(0, 1))).toBe("connecting");
    expect(stateFromEvents(ev)).toBe("connected");
  });
  it("firewall: failed open → long-polling fallback → firewall", () => {
    const ev = [
      { type: "socket_open_failed" },
      { type: "fallback" },
      { type: "failed" },
    ];
    expect(stateFromEvents(ev.slice(0, 2))).toBe("connecting");
    expect(stateFromEvents(ev)).toBe("firewall");
  });
  it("old SDK and expired token are rejections", () => {
    expect(
      stateFromEvents([
        { type: "socket_open" },
        { type: "rejected", reason: "version" },
      ]),
    ).toBe("version_mismatch");
    expect(stateFromEvents([{ type: "rejected", reason: "token" }])).toBe(
      "token_expired",
    );
  });
  it("runs, stops, reloads and local confirms", () => {
    let s = "connected";
    s = transition(s, { type: "try_started" });
    expect(s).toBe("running");
    s = transition(s, { type: "stopped" });
    expect(s).toBe("stopped");
    s = transition(s, { type: "resumed" });
    expect(s).toBe("running");
    expect(transition(s, { type: "reload" })).toBe("reloaded");
    expect(transition(s, { type: "local_confirm_needed" })).toBe(
      "waiting_local_confirm",
    );
    expect(
      transition("waiting_local_confirm", { type: "local_confirmed" }),
    ).toBe("running");
    expect(transition("connected", { type: "heartbeat_missed" })).toBe("stale");
    expect(transition("stale", { type: "heartbeat" })).toBe("connected");
  });
  it("every trouble state is a state; scenario aliases", () => {
    TROUBLE_STATES.forEach((s) => expect(CONNECT_STATES).toContain(s));
    expect(scenarioId("old_sdk")).toBe("version_mismatch");
    expect(scenarioId("nope")).toBe("ok");
  });
  it("heartbeat health: stale after 30 s, offline after 10 min", () => {
    expect(heartbeatHealth(2000)).toBeNull();
    expect(heartbeatHealth(30_000)).toBe("stale");
    expect(heartbeatHealth(10 * 60_000)).toBe("offline");
  });
});

describe("timers and formatting", () => {
  it("fmtClock floors whole seconds and never goes negative", () => {
    expect(fmtClock(12_400)).toBe("0:12");
    expect(fmtClock(552_000)).toBe("9:12");
    expect(fmtClock(-5)).toBe("0:00");
    expect(waitingLine(12_999)).toBe("Waiting for your agent… 0:12");
  });
  it("elapsed and token time are computed from timestamps", () => {
    const t0 = Date.UTC(2026, 9, 17, 9, 0, 0);
    expect(elapsedMs(new Date(t0).toISOString(), t0 + 64_000)).toBe(64_000);
    expect(elapsedMs(t0 + 5, t0)).toBe(0);
    expect(tokenLeftMs(new Date(t0 + 600_000).toISOString(), t0 + 48_000)).toBe(
      552_000,
    );
  });
  it("ages, heartbeat and first result", () => {
    expect(fmtAge(4200)).toBe("4 s");
    expect(fmtAge(2 * 3600_000)).toBe("2 h");
    expect(heartbeatText(2100)).toBe("Heartbeat 2 s ago");
    expect(firstResultLine(64_000, { simulated: true })).toBe(
      "Simulated · first result 1 m 04 s after you opened this",
    );
    expect(firstResultLine(64_000)).toBe(
      "First result 1 m 04 s after you opened this",
    );
    expect(firstResultFast(64_000)).toBe(true);
    expect(firstResultFast(130_000)).toBe(false);
  });
  it("tokens are masked on screen", () => {
    expect(maskToken("fic_live_3b9d0a1c5e7f2a4b6c8d7Q2k")).toBe(
      "fic_live_…7Q2k",
    );
  });
});

describe("copy", () => {
  it("handshake lines per kind", () => {
    expect(handshakeLine(agent, demo)).toBe(
      "Connected · maya@macbook-air · support_agent (LangGraph 0.4.8 · Python 3.12) · git 9f2c1a7 · 2 files changed",
    );
    expect(handshakeLine(MACHINES[0].agents[1], demo)).toBe(
      "Connected · maya@macbook-air · Claude Code 2.0 headless · Docker 27.1 · records every message and tool call, as a trace · API key",
    );
    expect(handshakeLine(MACHINES[1].agents[0], demo)).toBe(
      "Connected · maya@macbook-air · LiveKit worker acme-voice-v2 · wss://acme.livekit.cloud · 5 at a time",
    );
    expect(gitText({ commit: "9f2c1a7", dirty: false, files: [] })).toBe(
      "git 9f2c1a7",
    );
  });
  it("read summary counts what the handshake declared", () => {
    expect(readSummary(agent)).toBe(
      "We read 4 parameters, 7 tools and 2 models from your agent.",
    );
    const fewer = { ...agent, tools: agent.tools.slice(0, 3) };
    expect(readSummary(MACHINES[0].agents[1])).toBe(
      "We read 3 parameters, 1 MCP server (12 tools) and 2 models from your agent.",
    );
    expect(
      checklistOf({ state: "connected", agent: MACHINES[0].agents[1] })[1]
        .label,
    ).toBe("Parameters and tools read (3 · 12)");
    expect(readSummary(fewer)).toBe(
      "We read 4 parameters, 3 tools and 2 models from your agent.",
    );
  });
  it("checklist ticks itself", () => {
    expect(checklistOf({ state: "waiting" }).map((i) => i.done)).toEqual([
      false,
      false,
      false,
    ]);
    const c = checklistOf({ state: "connected", agent });
    expect(c.map((i) => i.label)).toEqual([
      "Agent connected",
      "Parameters and tools read (4 · 7)",
      "First result",
    ]);
    expect(
      checklistOf({
        state: "connected",
        agent,
        firstResult: { passed: true },
      }).every((i) => i.done),
    ).toBe(true);
  });
  it("access panel lines", () => {
    expect(acceptsLine(alice.agents[0])).toBe(
      "Accepts from the UI: system_prompt, model, temperature, retriever_k (declared in agent.py). Anything else is refused by the worker.",
    );
    expect(notAllowedLine(ravi)).toMatch(
      /^Only Ravi Menon can run on ravi@thinkpad\./,
    );
  });
});

describe("access rules (D21)", () => {
  it("owner, teammate allowed, teammate not allowed, viewer", () => {
    expect(accessOf(demo)).toMatchObject({
      isOwner: true,
      mayUse: true,
      canToggle: true,
    });
    expect(accessOf(alice)).toMatchObject({
      isOwner: false,
      mayUse: true,
      canToggle: false,
      reason: "Only Alice Park can change this",
    });
    expect(accessOf(ravi)).toMatchObject({ isOwner: false, mayUse: false });
    expect(accessOf({ ...ravi, may_use: undefined })).toMatchObject({
      mayUse: false,
    });
    expect(accessOf(demo, { readOnly: true })).toMatchObject({
      canToggle: false,
      reason: "Viewers can't change runners",
    });
  });
  it("the worker accepts only declared params, in range", () => {
    const r = checkParams(agent.params_schema, {
      temperature: 0.5,
      retriever_k: 50,
      max_tokens: 900,
      model: "gpt-4o-mini",
    });
    expect(r.accepted).toEqual({ temperature: 0.5, model: "gpt-4o-mini" });
    expect(r.refused).toEqual([
      { name: "retriever_k", reason: "above the declared range" },
      { name: "max_tokens", reason: "not declared" },
    ]);
  });
  it("recipe widening needs a local confirm unless allowed", () => {
    const cur = {
      permissions: "edit",
      mcp_servers: [{ name: "acme-support" }],
    };
    expect(recipeWidens({ permissions: "full_auto" }, cur)).toEqual({
      widens: true,
      reasons: ["full_auto"],
    });
    expect(recipeWidens({ permissions: "read_only" }, cur).widens).toBe(false);
    expect(
      recipeWidens(
        { mcp_servers: [{ name: "acme-support" }, { name: "github" }] },
        cur,
      ).reasons,
    ).toEqual(["mcp_server"]);
    const w = recipeWidens({ permissions: "full_auto" }, cur);
    expect(needsLocalConfirm(alice, w)).toBe(true);
    expect(needsLocalConfirm({ ...alice, allow_recipe_changes: true }, w)).toBe(
      false,
    );
  });
});

describe("live run strip", () => {
  const a = {
    state: "running",
    machine: alice,
    run_label: "#3G",
    slots: 4,
    done: 41,
    total: 90,
    failed: 2,
    running: 4,
    left: 49,
    sha: "9f2c1a7",
  };
  it("strip, stop confirm, resume", () => {
    expect(stripLine(a)).toBe(
      "Running on alice@mbp-14 · 4 at a time · 41/90 · 2 failed",
    );
    expect(stopConfirmOf(a)).toEqual({
      title: "Stop #3G on alice@mbp-14?",
      body: "4 tries in progress finish as Stopped. 49 tries won't start.",
    });
    expect(resumeLabel(a)).toBe("Resume · 49 left");
  });
  it("reload keeps the run's code; rerun toast", () => {
    const now = Date.UTC(2026, 9, 17, 9, 0, 4);
    const reload = {
      at: new Date(Date.UTC(2026, 9, 17, 9, 0, 0)).toISOString(),
      sha: "a41c2e0",
      files: [{ path: "agent.py", plus: 6, minus: 2 }],
    };
    expect(
      reloadLine(
        { ...a, failed: 6, failed_rows: 5, row_noun: "task" },
        reload,
        now,
      ),
    ).toBe(
      "agent.py changed 4 s ago · this run keeps 9f2c1a7 · Rerun the 5 failed tasks with the new code",
    );
    expect(
      stoppedToast(a, { lines: [{ stopped: true }, { stopped: true }] }),
    ).toBe("Stopped #3G on alice@mbp-14 · 2 tries marked Stopped");
    expect(
      rerunToast({ run_label: "#3H", sha: "a41c2e0", rerun: 6, carried: 84 }),
    ).toBe("Started #3H with a41c2e0 · 6 tries rerun, 84 carried");
  });
  it("try lines carry the sha; stopped tries never count; one sha per run", () => {
    const lines = [
      {
        row_label: "T3",
        trial: 1,
        passed: true,
        duration_ms: 9200,
        code_sha: "9f2c1a7",
        trace_id: "7f2c0000-0001",
      },
      {
        row_label: "T4",
        trial: 1,
        passed: null,
        stopped: true,
        code_sha: "9f2c1a7",
      },
      { row_label: "T5", trial: 1, passed: false, code_sha: "9f2c1a7" },
    ];
    expect(tryLineText(lines[0], { fmtMs })).toBe(
      "T3 · try 1 · passed · 9.2 s · 9f2c1a7 · trace 7f2c…",
    );
    expect(tryLineText(lines[1])).toBe("T4 · try 1 · Stopped by you · 9f2c1a7");
    expect(countedTries(lines)).toHaveLength(2);
    expect(oneShaPerRun(lines, "9f2c1a7")).toBe(true);
    expect(oneShaPerRun([...lines, { code_sha: "a41c2e0" }], "9f2c1a7")).toBe(
      false,
    );
  });
});

describe("C10: copy templates carry no scripted digits", () => {
  it("connectCopy.js template literals hold only computed numbers", () => {
    const src = readFileSync(
      resolve(process.cwd(), "src/sections/common/Connect/connectCopy.js"),
      "utf8",
    )
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "");
    const templates = src.match(/`[^`]*`/g) ?? [];
    const bad = templates
      .map((t) => t.replace(/\$\{[^}]*\}/g, ""))
      .filter((t) =>
        /\d/.test(
          t.replace(/\b(0|1|100|443)\b/g, "").replace(/0\.9(\.\d)?/g, ""),
        ),
      );
    expect(bad).toEqual([]);
  });
  it("a sentence's numbers move with the data", () => {
    const one = stripLine({
      state: "running",
      machine: alice,
      slots: 4,
      done: 41,
      total: 90,
      failed: 2,
    });
    const two = stripLine({
      state: "running",
      machine: alice,
      slots: 4,
      done: 42,
      total: 90,
      failed: 3,
    });
    expect(one).not.toBe(two);
    expect(two).toContain("42/90 · 3 failed");
  });
});

describe("handshake record", () => {
  it("prints declared fields only, never null", async () => {
    const { handshakeRecord } = await import("../connectModel");
    const r = handshakeRecord({
      ...agent,
      running: null,
      recent_runs: [],
      livekit_url: null,
      capture: { model_calls: "gateway", steps: null, auth: "api_key" },
    });
    expect(r.capture).toEqual({ model_calls: "gateway", auth: "api_key" });
    expect(r).not.toHaveProperty("running");
    expect(r).not.toHaveProperty("recent_runs");
    expect(r).not.toHaveProperty("livekit_url");
    expect(r.params_schema).toHaveLength(4);
    expect(JSON.stringify(r)).not.toMatch(/null/);
  });
});

// options-truth L3 G5 (L2, L4): a Try's own lines.
describe("Try result lines", () => {
  it("echoes the recipe a harness try ran with", () => {
    expect(
      recipeEchoLine({
        model: "gpt-5",
        max_turns: 12,
        permissions: "read_only",
        mcp_servers: ["acme-support"],
      }),
    ).toBe("Ran with gpt-5 · Max turns 12 · Read only · MCP acme-support");
    expect(recipeEchoLine({ model: "o4-mini", permissions: "edit" })).toBe(
      "Ran with o4-mini · Edit files",
    );
    expect(recipeEchoLine(null)).toBe("");
  });
  it("puts what the knobs did on its own line", () => {
    expect(tryEffectsLine(["Read 9 records (Retriever k)"])).toBe(
      "What your settings did: Read 9 records (Retriever k)",
    );
    expect(tryEffectsLine([])).toBe("");
  });
});
