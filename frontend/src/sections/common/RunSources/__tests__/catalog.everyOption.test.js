// Every run source the New experiment picker promised
// is listed: what the server runs can be picked (cut 3: HTTP endpoint when
// the server's report says so), and every source this release does not
// build is listed, greyed, with "Not available in this release", and is
// never offered, counted or opened.
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  HELD_SOURCE_CARDS,
  STEP1_COPY,
  allEntries,
  cardUnavailableReason,
  cardsForKind,
  entriesFor,
  heldEntriesFor,
  isOffered,
  orderedCards,
  runSourceCard,
  searchCatalog,
  searchHeldEntries,
  searchStepCards,
  stepCard,
  stepCardsFor,
  stepWayCount,
  stepWayNames,
  tileUnavailableReason,
} from "../catalog";
import {
  FAILED_AVAILABILITY,
  LOADING_AVAILABILITY,
  availabilityOfReport,
  pickReason,
  runningCardReason,
} from "../runSourceAvailability";
import { cardOfKind, opensLevel3, pickerFromParams } from "../sourceModel";
import { FRAMEWORKS, HARNESSES } from "../catalogEntries";

const flags = vi.hoisted(() => ({ running: true }));
vi.mock("src/launch/launchSwitch", async (importOriginal) => ({
  ...(await importOriginal()),
  isSurfaceShown: (id) => id !== "exp.new.runningSources" || flags.running,
}));

beforeEach(() => {
  flags.running = true;
});

const LINE = "Not available in this release";
const A2A_OFF = "A2A experiments are not built in this release.";
const MCP_OFF = "MCP tool experiments are not built in this release.";
const YES = { available: true, reason: null };
// What GET /api/capabilities/ answers in `experiment_run_sources` at cut 3.
const ready = () =>
  availabilityOfReport({
    workspace_id: "00000000-0000-4000-8000-000000000001",
    sources: {
      prompt: YES,
      agent: YES,
      http: YES,
      a2a: { available: false, reason: A2A_OFF },
      mcp_tool: { available: false, reason: MCP_OFF },
    },
  });
const REPORTS = [
  ["ready", ready()],
  ["loading", LOADING_AVAILABILITY],
  ["failed", FAILED_AVAILABILITY],
  ["not passed", undefined],
];
const ids = (list) => list.map((x) => x.id);
const HELD = ["code", "harness", "voice"];

describe("step 1 lists every promised way to run", () => {
  it.each([
    ["rows", ["prompt", "agent", "running"]],
    ["conversations", ["prompt", "agent", "running"]],
    ["scenarios", ["prompt", "agent", "running"]],
    ["task_suite", ["agent", "prompt", "running"]],
  ])(
    "%s: the offered cards, then Your code (SDK / CLI), Agent harness and Voice agent",
    (kind, offered) => {
      for (const [, a] of REPORTS)
        expect(ids(stepCardsFor(kind, null, a).cards)).toEqual([
          ...offered,
          ...HELD,
        ]);
    },
  );

  it("names the held cards, with their icons and what each one is", () => {
    expect(
      HELD_SOURCE_CARDS.map((c) => [c.id, c.kind, c.title, c.icon, c.line]),
    ).toEqual([
      [
        "code",
        "code",
        "Your code (SDK / CLI)",
        "mdi:code-braces",
        "Your Python or TypeScript agent, any framework.",
      ],
      [
        "harness",
        "harness",
        "Agent harness",
        "mdi:console",
        "Claude Code, Codex CLI and more, with your tools.",
      ],
      [
        "voice",
        "voice",
        "Voice agent",
        "mdi:phone-in-talk",
        "Simulated callers talk to your agent.",
      ],
    ]);
  });

  it("names the frameworks behind Your code and the harnesses behind Agent harness", () => {
    const code = stepCard("code");
    expect(code.logoIds).toEqual([
      "langgraph",
      "crewai",
      "openai_agents",
      "llamaindex",
    ]);
    expect(code.more).toBe(FRAMEWORKS.length - 4);
    const harness = stepCard("harness");
    expect(harness.logoIds).toEqual([
      "claude_code",
      "codex_cli",
      "gemini_cli",
      "openhands",
    ]);
    expect(harness.more).toBe(HARNESSES.length - 4);
    expect(stepCard("voice")).toMatchObject({ logoIds: [], more: 0 });
  });

  it.each(REPORTS)(
    "greys every held card with the exact release line (report %s)",
    (_, a) => {
      for (const id of HELD) {
        const card = stepCard(id, a);
        expect(card, id).toBe(HELD_SOURCE_CARDS.find((c) => c.id === id));
        expect(cardUnavailableReason(card, a), id).toBe(LINE);
      }
    },
  );

  it("keeps Prompt, Agent graph and Already running as cut 3 offers them", () => {
    const a = ready();
    for (const id of ["prompt", "agent", "running"])
      expect(cardUnavailableReason(runSourceCard(id, a), a), id).toBeNull();
    expect(runSourceCard("running", a)).toMatchObject({
      line: "An HTTP endpoint you host.",
      tip: "Call an HTTP endpoint per row.",
    });
    expect(stepCardsFor("rows", null, a).rec.card).toBe("prompt");
  });

  it("never prints a runner command on a held card", () => {
    expect(JSON.stringify(HELD_SOURCE_CARDS)).not.toMatch(/futureagi|\brun\b/i);
    HELD_SOURCE_CARDS.forEach((card) =>
      expect(card.tip, card.id).toBeUndefined(),
    );
  });
});

describe("a held card is found by search, never picked or counted", () => {
  it.each([
    ["sdk", "code"],
    ["SDK", "code"],
    ["cli", "code"],
    ["python", "code"],
    ["typescript", "code"],
    ["your code", "code"],
    ["harness", "harness"],
    ["claude code", "harness"],
    ["codex", "harness"],
    ["voice", "voice"],
    ["phone", "voice"],
  ])("%s finds %s", (q, id) => {
    expect(searchStepCards(q, {}, ready()).map((h) => h.card)).toContain(id);
  });

  it("lists offered hits before held ones", () => {
    // Agent graph (title), Already running (`my agent`), then the held
    // Agent harness and Voice agent.
    expect(searchStepCards("agent", {}, ready()).map((h) => h.card)).toEqual([
      "agent",
      "running",
      "harness",
      "voice",
    ]);
  });

  it("leaves the searches that name an offered card as they were", () => {
    const a = ready();
    expect(searchStepCards("endpoint", {}, a)).toEqual([
      { card: "running", why: null },
    ]);
    expect(searchStepCards("graph", {}, a)).toEqual([
      { card: "agent", why: null },
    ]);
    expect(searchStepCards("prompt", {}, a)).toEqual([
      { card: "prompt", why: null },
    ]);
  });

  it("counts and suggests only what can be picked", () => {
    const a = ready();
    expect(stepWayCount(a)).toBe(3);
    expect(stepWayNames(a)).toEqual(["Prompt", "Agent graph", "HTTP endpoint"]);
    expect(STEP1_COPY.searchPlaceholder(stepWayCount(a), a)).toBe(
      "Search 3 ways to run, prompts, graphs, models",
    );
    expect(ids(cardsForKind("rows", a))).toEqual([
      "prompt",
      "agent",
      "running",
    ]);
    expect(ids(orderedCards("rows", null, a))).toEqual([
      "prompt",
      "agent",
      "running",
    ]);
  });

  it.each(HELD)("opens no %s card from a lookup, key or link", (id) => {
    expect(runSourceCard(id)).toBeNull();
    expect(runSourceCard(id, ready())).toBeNull();
    expect(cardOfKind(id)).toBeNull();
    expect(pickerFromParams({ src: id })).toMatchObject({
      level: 1,
      card: null,
    });
    expect(pickerFromParams({ new_experiment: "1", source: id })).toMatchObject(
      { level: 1, card: null, entryId: null },
    );
  });
});

describe("Already running lists Traces I already have and Upload results", () => {
  it("behind the card, after the endpoints, greyed with the release line", () => {
    const held = heldEntriesFor("running");
    expect(held.map((e) => [e.id, e.name])).toEqual([
      ["traces", "Traces I already have"],
      ["upload", "Upload results"],
    ]);
    for (const [, a] of REPORTS)
      held.forEach((e) => expect(tileUnavailableReason(e, a), e.id).toBe(LINE));
    expect(heldEntriesFor("prompt")).toEqual([]);
    expect(heldEntriesFor("agent")).toEqual([]);
  });

  it("is found by a step-1 search", () => {
    expect(ids(searchHeldEntries("traces"))).toEqual(["traces"]);
    expect(ids(searchHeldEntries("Upload"))).toEqual(["upload"]);
    expect(searchHeldEntries("zzqx")).toEqual([]);
    expect(searchHeldEntries("  ")).toEqual([]);
  });

  it("is never offered: no lookup, search, count or link adds one", () => {
    const a = ready();
    for (const id of ["traces", "upload"]) {
      expect(isOffered(id), id).toBe(false);
      expect(opensLevel3(id), id).toBe(false);
      expect(searchCatalog(id), id).toEqual([]);
      expect(searchCatalog(id, a), id).toEqual([]);
      expect(
        pickerFromParams({ new_experiment: "1", entry: id }),
        id,
      ).toMatchObject({ level: 1, entryId: null });
    }
    expect(ids(allEntries())).toEqual(["http", "a2a", "mcp_tool"]);
    expect(ids(entriesFor("running"))).toEqual(["http", "a2a", "mcp_tool"]);
    expect(ids(entriesFor("running", a))).toEqual(["http"]);
  });

  it("keeps HTTP as the server reports it, and A2A and MCP with the server's reason", () => {
    const a = ready();
    expect(pickReason("http", a)).toBeNull();
    expect(pickReason("a2a", a)).toBe(A2A_OFF);
    expect(pickReason("mcp_tool", a)).toBe(MCP_OFF);
  });
});

describe("A2A and MCP say 'Not available in this release' until the server answers", () => {
  it.each([
    ["loading", LOADING_AVAILABILITY],
    ["failed", FAILED_AVAILABILITY],
    ["not passed", undefined],
  ])("report %s", (_, a) => {
    expect(pickReason("a2a", a)).toBe(LINE);
    expect(pickReason("mcp_tool", a)).toBe(LINE);
    // The endpoint is in this release; the card behind it waits for the
    // server with cut 3's words.
    expect(pickReason("http", a)).toBe("Not available in this deployment");
    expect(runningCardReason(a)).toBe("Not available in this deployment");
  });
});

describe("the held options follow the Already running launch gate", () => {
  it("a build that hides Already running hides them with it", () => {
    flags.running = false;
    expect(ids(stepCardsFor("rows").cards)).toEqual(["prompt", "agent"]);
    expect(stepCard("code")).toBeNull();
    expect(searchStepCards("sdk")).toEqual([]);
    expect(heldEntriesFor("running")).toEqual([]);
    expect(searchHeldEntries("traces")).toEqual([]);
  });
});
