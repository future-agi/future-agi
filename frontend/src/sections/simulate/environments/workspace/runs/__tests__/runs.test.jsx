import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import RunsPanel from "../RunsPanel";
import { RUNS_COPY } from "../runs.constants";
import {
  LIBRARY_VOICE_EVAL,
  NO_MISSELLING,
  selectedEntry,
} from "../../evals/__tests__/fixtures/evalEntries";

const env = { id: "env-1", name: "Refund Support", surface: "voice" };

const scenarios = Array.from({ length: 12 }, (_, i) => ({
  id: `s${i}`,
  critical: i < 3,
}));

// A fully set-up environment: an agent is connected and there are scenarios, so
// the pre-flight passes and "Start simulation" is enabled.
const readyState = { agent: { typeId: "chat", name: "Support agent" }, scenarios, evals: [] };

// Only the agent is missing, so exactly one pre-flight item is not-ok — which
// keeps the single "Fix" button unambiguous.
const agentMissingState = { agent: null, scenarios, evals: [] };

function renderPanel(overrides = {}) {
  const props = {
    env,
    envState: readyState,
    runs: [],
    onStart: vi.fn(),
    onOpenRun: vi.fn(),
    onGo: vi.fn(),
    ...overrides,
  };
  render(<RunsPanel {...props} />);
  return props;
}

describe("RunsPanel pre-flight", () => {
  it("shows the environment name as the Environment item", () => {
    renderPanel();
    expect(screen.getByText(env.name)).toBeInTheDocument();
  });

  it("marks the agent Not connected and routes Fix to the overview tab", () => {
    const onGo = vi.fn();
    renderPanel({ envState: agentMissingState, onGo });
    expect(screen.getByText(RUNS_COPY.agentNotConnected)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: RUNS_COPY.fix }));
    expect(onGo).toHaveBeenCalledWith("overview");
  });

  it("shows the scenario count as N tasks", () => {
    renderPanel();
    expect(screen.getByText(RUNS_COPY.tasks(12))).toBeInTheDocument();
  });

  it("marks Evals Optional and warns when none are applied", () => {
    renderPanel();
    expect(screen.getByText(RUNS_COPY.optional)).toBeInTheDocument();
  });
});

describe("RunsPanel estimates", () => {
  it("renders duration, concurrency and cost for 12 scenarios", () => {
    renderPanel();
    // Designer formula max(2, ceil(12 * 0.7)) = 9; flat 4-parallel; 12 * 0.08.
    expect(screen.getByText("~9 min")).toBeInTheDocument();
    expect(screen.getByText("4 parallel")).toBeInTheDocument();
    expect(screen.getByText("$0.96")).toBeInTheDocument();
  });
});

describe("RunsPanel start gating", () => {
  it("enables Start simulation once the environment is ready", () => {
    renderPanel();
    expect(screen.getByRole("button", { name: RUNS_COPY.start })).toBeEnabled();
  });

  it("disables Start simulation until an agent and scenarios exist", () => {
    renderPanel({ envState: agentMissingState });
    expect(screen.getByRole("button", { name: RUNS_COPY.start })).toBeDisabled();
  });

  it("fires onStart when clicked", () => {
    const onStart = vi.fn();
    renderPanel({ onStart });
    fireEvent.click(screen.getByRole("button", { name: RUNS_COPY.start }));
    expect(onStart).toHaveBeenCalledTimes(1);
  });
});

describe("RunsPanel empty state", () => {
  it("shows the empty-history copy when there are no runs", () => {
    renderPanel({ runs: [] });
    expect(screen.getByText(RUNS_COPY.empty.title)).toBeInTheDocument();
    expect(screen.getByText(RUNS_COPY.empty.body)).toBeInTheDocument();
  });
});

describe("RunsPanel evals-applied tile", () => {
  // A backed environment's applied set arrives already overlaid on
  // `envState` by the workspace: `evals` is §5's `evaluations.selected[]`,
  // each row a §1 entry plus its config id. Both halves of the tile read
  // that one list.
  //
  // One environment has one `agent_type`, so its `selected[]` can never
  // hold both a voice eval and a text eval — LIBRARY_VOICE_EVAL is a second
  // voice eval used here for exactly that reason.
  const selected = [
    selectedEntry(NO_MISSELLING, "cfg-1"),
    selectedEntry(LIBRARY_VOICE_EVAL, "cfg-2"),
  ];

  it("counts the §5 selected evals and names them under the count", () => {
    renderPanel({ envState: { ...readyState, evals: selected } });

    expect(screen.getByText(RUNS_COPY.applied(2))).toBeInTheDocument();
    expect(screen.getByText("no_misselling, off_topic_detection")).toBeInTheDocument();
    // The count and the sub-line come from the same list, so "2 applied" can
    // never sit over "Optional".
    expect(screen.queryByText(RUNS_COPY.optional)).toBeNull();
  });
});

// The populated Runs tab (runs > 0) delegates to RunsSummary; that surface is
// covered in summary/__tests__/RunsSummary.test.jsx, which mounts the chart +
// query client it needs.
