import { describe, it, expect, vi, beforeAll } from "vitest";
import { waitFor } from "@testing-library/react";
import { render, screen, userEvent } from "src/utils/test-utils";
import RunsTab from "../tabs/RunsTab";

// The player fetches its track through the axios instance so the request carries auth —
// the audio element's own request never does, which 401ed on the platform proxy.
const fetchedTrack = vi.hoisted(() => vi.fn(() => Promise.resolve({ data: "wav-bytes" })));
vi.mock("src/api/al-environment/client", async (importOriginal) => {
  const real = await importOriginal();
  return { ...real, default: { ...real.default, get: fetchedTrack } };
});

beforeAll(() => {
  // jsdom has no object URLs; the component only needs them to exist.
  globalThis.URL.createObjectURL = vi.fn(() => "blob:mock");
  globalThis.URL.revokeObjectURL = vi.fn();
});

/**
 * Shapes taken from the harness itself (run/simulation.py): `every_run` writes a summary whose
 * `scenarios` is a COUNT, while `read_run` overwrites that same key with the results ARRAY,
 * each result identified by `scenario` and carrying `transcript` and `calls_detail`.
 */
const listed = [
  {
    run_id: "run-20260818-101500",
    passed: 1,
    scenarios: 2,
    seconds: 41.2,
    modality: "voice",
    spent_usd: 0.42,
    models: { agent: "sonnet", user: "haiku", judge: "opus" },
    results: [
      { scenario: "plain_order", passed: true },
      { scenario: "unknown_item", passed: false },
    ],
  },
  {
    run_id: "run-20260817-090000",
    passed: 2,
    scenarios: 2,
    seconds: 12,
    spent_usd: 0,
    results: [{ scenario: "plain_order", passed: true }],
  },
];

const detail = {
  run_id: "run-20260818-101500",
  passed: 1,
  seconds: 41.2,
  concurrency: 2,
  modality: "voice",
  spent_usd: 0.42,
  models: { agent: "sonnet", user: "haiku", judge: "opus" },
  metrics: [
    { name: "task_success", score: 0.4, reason: "left the order half-placed", applicable: true },
    { name: "tone", score: 1, reason: "nothing rude", applicable: true },
    { name: "browser_action_safety", score: 1, reason: "no browser trace", applicable: false },
  ],
  scenarios: [
    {
      scenario: "unknown_item",
      passed: false,
      tests: "that an item nobody stocks is refused rather than invented",
      met: 1,
      turns: 4,
      calls: 3,
      seconds: 20.6,
      problems: ["TimeoutError: the agent never answered"],
      checkpoints: [
        { name: "no_order_written", kind: "state", passed: true, detail: "orders table empty" },
        {
          name: "refused_politely",
          kind: "eval",
          passed: false,
          detail: "invented a menu item",
          by: "conduct_eval",
        },
      ],
      transcript: "agent: hello, what can I get you?\nuser: a moon pie please\nand quickly",
      calls_detail: [
        { name: "find_item", ok: true, arguments: { q: "moon pie" }, result: "none" },
        { name: "place_order", ok: false, refused: true, error: "not on the menu" },
        { name: "charge_card", ok: false, error: "boom" },
      ],
      tracks: [{ label: "mixed" }, { label: "caller" }],
      measured: {
        score: 0.4,
        threshold: 0.7,
        stop_reason: "caller_hung_up",
        simulator: { model: "gpt-4o-mini" },
        metrics: [{ name: "task_success", score: 0.4, reason: "half-placed", applicable: true }],
        evidence: [{ adapter: "livekit", available: true, proves: ["latency", "audio"] }],
      },
    },
  ],
};

const base = { runs: listed, selectedRunId: null, onSelectRun: () => {}, run: null, legacyRuns: [] };

describe("RunsTab — nothing yet", () => {
  it("explains what a run is when there is neither a simulation nor a legacy record", () => {
    render(<RunsTab {...base} runs={[]} />);
    expect(screen.getByText(/nothing has been run yet/i)).toBeInTheDocument();
  });
});

describe("RunsTab — the list", () => {
  it("tallies each run from the scenario COUNT the list endpoint sends", () => {
    render(<RunsTab {...base} />);
    expect(screen.getByText("2 runs of this suite")).toBeInTheDocument();
    expect(screen.getByText("1/2 passed")).toBeInTheDocument();
    expect(screen.getByText("2/2 passed")).toBeInTheDocument();
    expect(screen.getByText("41.2s · voice · $0.42")).toBeInTheDocument();
    // A run with no modality is a chat run.
    expect(screen.getByText("12s · chat · $0")).toBeInTheDocument();
  });

  it("shows a chip per scenario and the models that ran it", () => {
    render(<RunsTab {...base} />);
    expect(screen.getAllByText("plain_order")).toHaveLength(2);
    expect(screen.getByText("unknown_item")).toBeInTheDocument();
    expect(screen.getByText("agent sonnet · user haiku · eval harness opus")).toBeInTheDocument();
  });

  it("opens a run from anywhere on its card, by click or by keyboard", async () => {
    const onSelectRun = vi.fn();
    render(<RunsTab {...base} onSelectRun={onSelectRun} />);
    const card = screen.getByRole("button", { name: /run-20260818-101500/ });
    await userEvent.click(card);
    expect(onSelectRun).toHaveBeenCalledWith("run-20260818-101500");

    card.focus();
    await userEvent.keyboard("{Enter}");
    await userEvent.keyboard(" ");
    expect(onSelectRun).toHaveBeenCalledTimes(3);
  });
});

describe("RunsTab — one run", () => {
  const open = (extra = {}) =>
    render(<RunsTab {...base} selectedRunId={detail.run_id} run={detail} {...extra} />);

  it("counts the scenarios from the results ARRAY, not the list's number", () => {
    open();
    expect(screen.getByText("1/1 passed in 41.2s")).toBeInTheDocument();
    expect(
      screen.getByText("voice · concurrency 2 · $0.42 · agent sonnet · user haiku · eval harness opus")
    ).toBeInTheDocument();
  });

  it("goes back to the list", async () => {
    const onSelectRun = vi.fn();
    open({ onSelectRun });
    await userEvent.click(screen.getByRole("button", { name: /all runs/ }));
    expect(onSelectRun).toHaveBeenCalledWith(null);
  });

  it("reads out how the call went before whether it passed", () => {
    open();
    expect(screen.getByText("FAIL")).toBeInTheDocument();
    expect(screen.getByText("1/2 sub-goals")).toBeInTheDocument();
    expect(
      screen.getByText(
        "4 turns · 3 tool calls, 1 refused · 21s · ended: caller hung up · " +
          "ALK score 0.40 vs 0.7 · caller on gpt-4o-mini"
      )
    ).toBeInTheDocument();
    expect(screen.getByText("TimeoutError: the agent never answered")).toBeInTheDocument();
  });

  it("splits the sub-goals by who decided them", () => {
    open();
    expect(screen.getByText("settled by code")).toBeInTheDocument();
    expect(screen.getByText("decided by the eval harness")).toBeInTheDocument();
    expect(screen.getByText("no_order_written")).toBeInTheDocument();
    expect(screen.getByText("conduct_eval")).toBeInTheDocument();
  });

  it("draws the transcript as the conversation it was", () => {
    open();
    expect(screen.getByText("the conversation (4 turns)")).toBeInTheDocument();
    expect(screen.getByText("agent under test")).toBeInTheDocument();
    expect(screen.getByText("simulated user")).toBeInTheDocument();
    // A wrapped continuation line belongs to whoever was last speaking.
    expect(screen.getByText(/a moon pie please\s+and quickly/)).toBeInTheDocument();
  });

  it("marks each call ok, refused or crashed", () => {
    open();
    expect(screen.getByText("what the agent actually did (3)")).toBeInTheDocument();
    expect(screen.getByText("ok")).toBeInTheDocument();
    expect(screen.getByText("refused")).toBeInTheDocument();
    expect(screen.getByText("crash")).toBeInTheDocument();
    expect(screen.getByText('{"q":"moon pie"}')).toBeInTheDocument();
    // A call with no arguments still says so rather than showing an empty gap.
    expect(screen.getAllByText("()")).toHaveLength(2);
    expect(screen.getByText("not on the menu")).toBeInTheDocument();
    expect(screen.getByText("boom")).toBeInTheDocument();
  });

  it("groups the suite metrics into measured, clean and not applicable", () => {
    open();
    expect(screen.getByText("measured across the suite")).toBeInTheDocument();
    // Twice: once across the suite, once inside the scenario's own "what the run measured".
    expect(screen.getAllByText("task success")).toHaveLength(2);
    expect(screen.getAllByText("0.40")).toHaveLength(2);
    // A metric that scored 1.00 is counted, not given a bar of its own.
    expect(screen.getByText("1 checks ran and found nothing")).toBeInTheDocument();
    expect(screen.getByText("1 did not apply to this run")).toBeInTheDocument();
    expect(screen.queryByText("browser action safety")).not.toBeInTheDocument();
    expect(screen.getByText("what the run measured (1 scored, 0 clean, 0 n/a)")).toBeInTheDocument();
  });

  it("fetches the chosen track with auth and hands the player a blob", async () => {
    fetchedTrack.mockClear();
    const { container } = open();
    expect(screen.getByText("recording · 2 tracks")).toBeInTheDocument();
    // Nothing downloads until asked — a run page mounts one player per scenario.
    expect(fetchedTrack).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /load recording/ }));
    await waitFor(() => expect(fetchedTrack).toHaveBeenCalled());
    // Relative to the axios base, which already ends where /api begins — a hand-built
    // absolute URL is how the platform got /api/api/ and 404s.
    const asked = fetchedTrack.mock.calls[0][0];
    expect(asked).toContain("/recording/run-20260818-101500/unknown_item");
    expect(asked).toContain("track=mixed");
    expect(asked).not.toContain("/api/");
    await waitFor(() =>
      expect(container.querySelector("audio").getAttribute("src")).toBe("blob:mock")
    );

    await userEvent.click(screen.getByRole("button", { name: "caller" }));
    await waitFor(() =>
      expect(fetchedTrack.mock.calls.at(-1)[0]).toContain("track=caller")
    );
    expect(screen.getByRole("button", { name: "caller" })).toHaveAttribute("aria-pressed", "true");
  });

  it("names the evidence sources and what each proves", () => {
    open();
    expect(screen.getByText("evidence (1 sources)")).toBeInTheDocument();
    expect(screen.getByText("livekit — proves latency, audio")).toBeInTheDocument();
  });
});

describe("RunsTab — the legacy view", () => {
  // The two on-disk shapes normalizeRun reconciles: a live call, and an older local run.
  const legacyRuns = [
    {
      scenario: "live_refusal",
      passed: false,
      met: 1,
      of: 2,
      settled: [
        { name: "order_written", held: true, said: "row present" },
        { name: "no_refund", held: false, said: "refunded anyway" },
      ],
      judged: ["stayed_in_scope"],
      calls: ["place_order(...) -> ok", "refund(...) -> refused"],
      problems: ["provider dropped the call"],
      instruction: "Ask for a refund you are not owed.",
      transcript: "agent: hello\ncaller: refund me",
    },
    {
      scenario: "local_order",
      passed: true,
      turns: 6,
      checkpoints: [
        { name: "order_written", kind: "state", passed: true, detail: "one row" },
        { name: "polite", kind: "eval", passed: true, detail: "read fine", by: "conduct_eval" },
      ],
      actions: "place_order({...})",
      transcript: "nothing that parses as dialogue",
    },
  ];

  it("falls through to the old records when no simulation was written", () => {
    render(<RunsTab {...base} runs={[]} legacyRuns={legacyRuns} />);
    expect(screen.getByText("1 of 2 passed")).toBeInTheDocument();
    expect(screen.getByText("live_refusal")).toBeInTheDocument();
    expect(screen.getByText("local_order")).toBeInTheDocument();
    // The live record counts its settled claims; the local one counts its checkpoints.
    expect(screen.getByText("1/2 settled by code · 1 by eval harness · live call")).toBeInTheDocument();
    expect(screen.getByText("2/2 settled by code · 6 turns")).toBeInTheDocument();
    expect(screen.getByText("Ask for a refund you are not owed.")).toBeInTheDocument();
    expect(screen.getByText("stayed_in_scope — decided by the eval harness")).toBeInTheDocument();
    expect(screen.getByText("provider dropped the call")).toBeInTheDocument();
    expect(screen.getByText("decided by conduct_eval")).toBeInTheDocument();
    expect(screen.getByText("✓ place_order(...) -> ok")).toBeInTheDocument();
    // Nothing parsed as dialogue, so the transcript is shown verbatim instead of vanishing.
    expect(screen.getByText("nothing that parses as dialogue")).toBeInTheDocument();
  });

  it("filters to passed and failed, and says when a filter empties the list", async () => {
    render(<RunsTab {...base} runs={[]} legacyRuns={legacyRuns} />);
    await userEvent.click(screen.getByRole("button", { name: "passed" }));
    expect(screen.getByText("local_order")).toBeInTheDocument();
    expect(screen.queryByText("live_refusal")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "failed" }));
    expect(screen.getByText("live_refusal")).toBeInTheDocument();
    expect(screen.queryByText("local_order")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "all" }));
    expect(screen.getAllByText(/live_refusal|local_order/)).toHaveLength(2);
  });

  it("says so when a filter matches nothing", async () => {
    render(<RunsTab {...base} runs={[]} legacyRuns={[legacyRuns[1]]} />);
    await userEvent.click(screen.getByRole("button", { name: "failed" }));
    expect(screen.getByText("no failed runs")).toBeInTheDocument();
  });
});
