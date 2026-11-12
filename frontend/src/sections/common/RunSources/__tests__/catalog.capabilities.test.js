import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { describe, expect, it, vi } from "vitest";
import {
  RUN_SOURCE_CARDS,
  STEP1_COPY,
  agentOffered,
  cardUnavailableReason,
  cardsForKind,
  catalogEntry,
  entriesFor,
  inAppWords,
  kindRecommendation,
  offeredWays,
  runSourceCard,
  runningKinds,
  searchCatalog,
  searchStepCards,
  stepCardsFor,
  stepDisambiguation,
  stepWayCount,
  stepWayNames,
} from "../catalog";
import {
  AVAILABILITY_STATUS,
  FAILED_AVAILABILITY,
  FALLBACK_REASON,
  LOADING_AVAILABILITY,
  RUNNING_SOURCE_KINDS,
  UNREPORTED_REASON,
  availabilityOfReport,
  pickReason,
  runningCardReason,
  sourceAvailable,
  sourceUnavailableReason,
} from "../runSourceAvailability";

vi.mock("src/launch/launchSwitch", async (importOriginal) => ({
  ...(await importOriginal()),
  isSurfaceShown: () => true,
}));

// What GET /api/capabilities/ answers in `experiment_run_sources`
// (tfc/capabilities/contracts.py ExperimentRunSourcesSerializer).
const WORKSPACE = "00000000-0000-4000-8000-000000000001";
const A2A_OFF = "A2A experiments are not built in this release.";
const MCP_OFF = "MCP tool experiments are not built in this release.";
const HTTP_OFF =
  "HTTP endpoint experiments are unavailable on this server. Contact your administrator.";
const AGENT_OFF =
  "Agent graph experiments are unavailable on this server. Contact your administrator.";
const YES = { available: true, reason: null };

const report = (sources = {}) => ({
  workspace_id: WORKSPACE,
  sources: {
    prompt: YES,
    agent: YES,
    http: YES,
    a2a: { available: false, reason: A2A_OFF },
    mcp_tool: { available: false, reason: MCP_OFF },
    ...sources,
  },
});
const ready = (sources) => availabilityOfReport(report(sources));
const NOTHING_RUNNING = { http: { available: false, reason: HTTP_OFF } };
const ids = (cards) => cards.map((c) => c.id);
// Owner rule: nothing ships greyed with a promise.
const PROMISE = /coming|soon|not built|later|not available/i;

describe("the server's run-source report", () => {
  it("reads every source and the workspace it was read for", () => {
    const a = ready();
    expect(a.status).toBe(AVAILABILITY_STATUS.READY);
    expect(a.workspaceId).toBe(WORKSPACE);
    expect(a.sources.http).toEqual(YES);
    expect(a.sources.a2a).toEqual({ available: false, reason: A2A_OFF });
    expect(a.sources.mcp_tool).toEqual({ available: false, reason: MCP_OFF });
  });

  it.each([undefined, null, {}, { sources: null }, { sources: "all" }])(
    "reads %j as no report",
    (body) => {
      expect(availabilityOfReport(body)).toBeNull();
    },
  );

  it("needs a literal true to offer a source and always has a reason", () => {
    const a = availabilityOfReport(
      report({
        http: { available: "true", reason: null },
        agent: { available: false, reason: "   " },
      }),
    );
    expect(a.sources.http).toEqual({
      available: false,
      reason: FALLBACK_REASON,
    });
    expect(a.sources.agent).toEqual({
      available: false,
      reason: FALLBACK_REASON,
    });
  });

  it("leaves a kind the report leaves out as not known", () => {
    const body = report();
    delete body.sources.prompt;
    delete body.sources.http;
    const a = availabilityOfReport(body);
    expect(sourceAvailable("prompt", a)).toBe(true);
    expect(sourceUnavailableReason("prompt", a)).toBeNull();
    expect(sourceAvailable("http", a)).toBe(false);
  });
});

describe("what each kind may do", () => {
  it.each([
    ["loading", LOADING_AVAILABILITY],
    ["failed", FAILED_AVAILABILITY],
    ["not passed", undefined],
  ])("offers no Already running source while the report is %s", (_, a) => {
    for (const kind of ["http", "a2a", "mcp_tool"]) {
      expect(sourceAvailable(kind, a), kind).toBe(false);
      expect(sourceUnavailableReason(kind, a), kind).toBeNull();
    }
    // Prompt and Agent graph keep working; create and run still check.
    for (const kind of ["prompt", "agent"]) {
      expect(sourceAvailable(kind, a), kind).toBe(true);
      expect(sourceUnavailableReason(kind, a), kind).toBeNull();
    }
  });

  it("follows the server for every kind once it answers", () => {
    const a = ready({ agent: { available: false, reason: AGENT_OFF } });
    expect(sourceAvailable("prompt", a)).toBe(true);
    expect(sourceAvailable("http", a)).toBe(true);
    expect(sourceAvailable("a2a", a)).toBe(false);
    expect(sourceAvailable("mcp_tool", a)).toBe(false);
    expect(sourceAvailable("agent", a)).toBe(false);
    expect(sourceUnavailableReason("agent", a)).toBe(AGENT_OFF);
  });

  it.each(["code", "harness", "traces", "upload", "external"])(
    "leaves %s, which the report does not cover, unchanged",
    (kind) => {
      expect(sourceAvailable(kind, ready(NOTHING_RUNNING))).toBe(true);
      expect(sourceAvailable(kind, LOADING_AVAILABILITY)).toBe(true);
    },
  );
});

describe("step 1 shows every source and lets only what runs be picked", () => {
  it.each([
    ["loading", LOADING_AVAILABILITY],
    ["failed", FAILED_AVAILABILITY],
    ["unavailable", ready(NOTHING_RUNNING)],
  ])("shows Already running greyed while the report is %s", (_, a) => {
    expect(ids(cardsForKind("rows", a))).toEqual([
      "prompt",
      "agent",
      "running",
    ]);
    const running = runSourceCard("running", a);
    expect(running).toBe(RUN_SOURCE_CARDS.find((c) => c.id === "running"));
    expect(cardUnavailableReason(running, a)).toBe(
      "Not available in this deployment",
    );
    for (const kind of RUNNING_SOURCE_KINDS)
      expect(pickReason(kind, a), kind).toBeTruthy();
    // Every tile behind the card stays listed for the picker to grey.
    expect(ids(entriesFor("running"))).toEqual(["http", "a2a", "mcp_tool"]);
    expect(searchStepCards("endpoint", {}, a)).toEqual([
      { card: "running", why: null },
    ]);
    // Offers, counts and suggestions name only what can be picked.
    expect(runningKinds(a)).toEqual([]);
    expect(entriesFor("running", a)).toEqual([]);
    for (const q of ["http", "a2a", "mcp", "endpoint"])
      expect(searchCatalog(q, a), q).toEqual([]);
    expect(stepWayCount(a)).toBe(2);
    expect(stepWayNames(a)).toEqual(["Prompt", "Agent graph"]);
    expect(stepDisambiguation(a)).toBe(
      "Prompt and Agent graph run in Future AGI, on every row.",
    );
    expect(STEP1_COPY.searchPlaceholder(stepWayCount(a), a)).toBe(
      "Search prompts, graphs, models",
    );
  });

  it("offers the HTTP endpoint, greys A2A and MCP with the server's reasons", () => {
    const a = ready();
    expect(ids(cardsForKind("rows", a))).toEqual([
      "prompt",
      "agent",
      "running",
    ]);
    const running = runSourceCard("running", a);
    expect(running).toMatchObject({
      id: "running",
      title: "Already running",
      kind: "http",
      subTiles: ["http", "a2a", "mcp_tool"],
      line: "An HTTP endpoint you host.",
      tip: "Call an HTTP endpoint per row.",
    });
    expect(`${running.line} ${running.tip}`).not.toMatch(/A2A|MCP/);
    expect(cardUnavailableReason(running, a)).toBeNull();
    expect(pickReason("http", a)).toBeNull();
    expect(pickReason("a2a", a)).toBe(A2A_OFF);
    expect(pickReason("mcp_tool", a)).toBe(MCP_OFF);
    expect(ids(entriesFor("running"))).toEqual(["http", "a2a", "mcp_tool"]);
    expect(runningKinds(a)).toEqual(["http"]);
    expect(ids(entriesFor("running", a))).toEqual(["http"]);
    expect(ids(searchCatalog("http", a))).toEqual(["http"]);
    expect(searchCatalog("a2a", a)).toEqual([]);
    expect(searchCatalog("mcp", a)).toEqual([]);
    expect(searchStepCards("endpoint", {}, a)).toEqual([
      { card: "running", why: null },
    ]);
    expect(stepWayCount(a)).toBe(3);
    expect(stepWayNames(a)).toEqual(["Prompt", "Agent graph", "HTTP endpoint"]);
    expect(stepDisambiguation(a)).toBe(
      "Prompt and Agent graph run in Future AGI. Already running = an endpoint you host.",
    );
    expect(STEP1_COPY.searchPlaceholder(3, a)).toBe(
      "Search 3 ways to run, prompts, graphs, models",
    );
    for (const text of [
      running.line,
      running.tip,
      stepDisambiguation(a),
      ...stepWayNames(a),
    ])
      expect(text).not.toMatch(PROMISE);
  });

  it("names two sources when the server runs two", () => {
    const a = ready({ mcp_tool: YES });
    expect(runSourceCard("running", a)).toMatchObject({
      subTiles: ["http", "a2a", "mcp_tool"],
      line: "An HTTP endpoint or an MCP tool you host.",
      tip: "Call an HTTP endpoint or an MCP server tool per row.",
    });
    expect(ids(entriesFor("running", a))).toEqual(["http", "mcp_tool"]);
  });

  it("keeps the full card and copy when every grouped source can run", () => {
    const a = ready({ a2a: YES, mcp_tool: YES });
    expect(runSourceCard("running", a)).toBe(
      RUN_SOURCE_CARDS.find((c) => c.id === "running"),
    );
    expect(stepDisambiguation(a)).toBe(STEP1_COPY.disambiguation);
    expect(stepWayCount(a)).toBe(5);
  });

  it("shows the server's reason on Agent graph and recommends a card that runs", () => {
    const a = ready({ agent: { available: false, reason: AGENT_OFF } });
    expect(cardUnavailableReason(runSourceCard("agent", a), a)).toBe(AGENT_OFF);
    expect(cardUnavailableReason(runSourceCard("prompt", a), a)).toBeNull();
    expect(kindRecommendation("task_suite", a).card).toBe("prompt");
    const suite = stepCardsFor("task_suite", null, a);
    expect(suite.rec.card).toBe("prompt");
    // The held cards follow, greyed (never recommended, never keyed).
    expect(ids(suite.cards)).toEqual([
      "prompt",
      "agent",
      "running",
      "code",
      "harness",
      "voice",
    ]);
    // A door that recommends a card the server cannot run is not followed.
    const door = stepCardsFor("rows", { card: "agent", reason: "x" }, a);
    expect(door.rec.card).toBe("prompt");
    // Agent graph is still listed (with its reason), never hidden.
    expect(searchStepCards("graph", {}, a)).toEqual([
      { card: "agent", why: null },
    ]);
  });

  it("shows the reason the server gives for Prompt", () => {
    const reason = "Select a workspace to create an experiment.";
    const a = ready({ prompt: { available: false, reason } });
    expect(cardUnavailableReason(runSourceCard("prompt", a), a)).toBe(reason);
  });

  it.each([
    ["loading", LOADING_AVAILABILITY],
    ["failed", FAILED_AVAILABILITY],
  ])(
    "keeps Agent graph and its recommendation while the report is %s",
    (_, a) => {
      expect(cardUnavailableReason(runSourceCard("agent", a), a)).toBeNull();
      expect(kindRecommendation("task_suite", a).card).toBe("agent");
    },
  );

  it("keeps the whole catalog for lookups that pass no report", () => {
    expect(ids(cardsForKind("rows"))).toEqual(["prompt", "agent", "running"]);
    expect(ids(entriesFor("running"))).toEqual(["http", "a2a", "mcp_tool"]);
    expect(runningKinds()).toEqual(["http", "a2a", "mcp_tool"]);
    expect(catalogEntry("a2a")).toMatchObject({ id: "a2a", kind: "a2a" });
    expect(runSourceCard("running")).toBe(
      RUN_SOURCE_CARDS.find((c) => c.id === "running"),
    );
  });
});

describe("copy around step 1 points only at what can be picked", () => {
  const AGENT_REFUSED = { agent: { available: false, reason: AGENT_OFF } };

  it("names no Agent graph once the server refuses it", () => {
    const a = ready(AGENT_REFUSED);
    expect(agentOffered(a)).toBe(false);
    expect(stepWayNames(a)).toEqual(["Prompt", "HTTP endpoint"]);
    expect(stepWayCount(a)).toBe(2);
    expect(STEP1_COPY.searchPlaceholder(stepWayCount(a), a)).toBe(
      "Search 2 ways to run, prompts, models",
    );
    expect(stepDisambiguation(a)).toBe(
      "A prompt runs in Future AGI. Already running = an endpoint you host.",
    );
    expect(inAppWords(a)).toEqual(["a prompt"]);
    expect(offeredWays(a)).toBe("prompts or endpoints");
    // The card itself stays, with the server's reason (never hidden).
    expect(ids(cardsForKind("rows", a))).toEqual([
      "prompt",
      "agent",
      "running",
    ]);
  });

  it("names no Agent graph and no running source when only Prompt runs", () => {
    const a = ready({ ...AGENT_REFUSED, ...NOTHING_RUNNING });
    expect(stepWayNames(a)).toEqual(["Prompt"]);
    expect(stepWayCount(a)).toBe(1);
    expect(STEP1_COPY.searchPlaceholder(1, a)).toBe(
      "Search prompts and models",
    );
    expect(stepDisambiguation(a)).toBe(
      "A prompt runs in Future AGI, on every row.",
    );
    expect(offeredWays(a)).toBe("prompts");
  });

  it("suggests no way at all when the server refuses every source", () => {
    const reason = "Select a workspace to create an experiment.";
    const no = { available: false, reason };
    const a = availabilityOfReport({
      workspace_id: null,
      sources: { prompt: no, agent: no, http: no, a2a: no, mcp_tool: no },
    });
    expect(stepWayNames(a)).toEqual([]);
    expect(stepWayCount(a)).toBe(0);
    expect(cardUnavailableReason(runSourceCard("prompt", a), a)).toBe(reason);
  });

  it.each([
    ["loading", LOADING_AVAILABILITY],
    ["failed", FAILED_AVAILABILITY],
  ])("keeps Agent graph in the copy while the report is %s", (_, a) => {
    expect(agentOffered(a)).toBe(true);
    expect(stepWayNames(a)).toEqual(["Prompt", "Agent graph"]);
    expect(offeredWays(a)).toBe("prompts and agent graphs");
    expect(inAppWords(a)).toEqual(["a prompt", "an agent graph"]);
  });

  it("names every way, in the shipped words, when the server runs them all", () => {
    const a = ready({
      a2a: { available: true, reason: null },
      mcp_tool: { available: true, reason: null },
    });
    expect(offeredWays(a)).toBe(
      "prompts, agent graphs, endpoints, A2A agents or MCP tools",
    );
    expect(offeredWays(ready())).toBe("prompts, agent graphs or endpoints");
  });
});

describe("the Already running card's reason", () => {
  it("is the server's reason when it refuses every kind with one reason", () => {
    const reason = "Select a workspace to create an experiment.";
    const no = { available: false, reason };
    const a = ready({ prompt: no, agent: no, http: no, a2a: no, mcp_tool: no });
    expect(runningCardReason(a)).toBe(reason);
    expect(cardUnavailableReason(runSourceCard("running", a), a)).toBe(reason);
    expect(pickReason("a2a", a)).toBe(reason);
  });

  it("is the shipped wording when the reasons differ or are not known", () => {
    expect(runningCardReason(ready(NOTHING_RUNNING))).toBe(UNREPORTED_REASON);
    expect(runningCardReason(LOADING_AVAILABILITY)).toBe(UNREPORTED_REASON);
    expect(runningCardReason(FAILED_AVAILABILITY)).toBe(UNREPORTED_REASON);
    expect(UNREPORTED_REASON).toBe("Not available in this deployment");
    expect(pickReason("http", LOADING_AVAILABILITY)).toBe(UNREPORTED_REASON);
  });

  it("is none exactly when the server runs a kind behind the card", () => {
    expect(runningCardReason(ready())).toBeNull();
    expect(runningCardReason(ready({ http: { available: false } }))).toBe(
      UNREPORTED_REASON,
    );
    expect(runningCardReason(ready({ a2a: { available: true } }))).toBeNull();
  });

  it("leaves Prompt and Agent graph to the server's reason only", () => {
    expect(pickReason("prompt", LOADING_AVAILABILITY)).toBeNull();
    expect(pickReason("agent", ready())).toBeNull();
    expect(pickReason("code", LOADING_AVAILABILITY)).toBeNull();
  });
});

describe("availability has one source: the server's report", () => {
  it("draws the Already running card from the kinds the report covers", () => {
    expect(RUN_SOURCE_CARDS.find((c) => c.id === "running").subTiles).toBe(
      RUNNING_SOURCE_KINDS,
    );
  });

  it("keeps no availability table of its own in catalog.js", () => {
    const source = readFileSync(
      resolve(process.cwd(), "src/sections/common/RunSources/catalog.js"),
      "utf8",
    );
    expect(source).not.toMatch(
      /SOURCE_CAPABILITIES|Not available in this deployment|\b(?:http|a2a|mcp_tool|prompt|agent):\s*(?:true|false)\b/,
    );
  });
});
