import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";

import BuildingStage from "../building/BuildingStage";
import PanelBoundary from "../building/PanelBoundary";
import { DERIVING_LABEL, BUILDING_TABS } from "../build.constants";

// The timer-driven internals of the hero animation and the timeline are covered
// by T13 (buildingVisuals.test.jsx). Here we only care that BuildingPane wires
// the right label and pipeline through, so stub both to print their props.
vi.mock("../building/DerivingAnimation", () => ({
  default: ({ label }) => <div data-testid="deriving">{label}</div>,
}));
vi.mock("../building/PipelineChecks", () => ({
  default: ({ pipeline }) => (
    <div data-testid="checks">{pipeline.filter((s) => s.status === "done").length} done</div>
  ),
}));

beforeAll(() => {
  // jsdom has no layout; the console auto-scrolls to the latest turn.
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
});

const makeProgress = (over = {}) => ({
  done: [],
  running: false,
  failure: null,
  turns: [],
  chips: [],
  send: vi.fn(),
  onChip: vi.fn(),
  ...over,
});

describe("BuildingStage", () => {
  it("renders the four muted tab labels and the console", () => {
    render(<BuildingStage progress={makeProgress()} />);

    BUILDING_TABS.forEach((t) => {
      expect(screen.getByText(t.label)).toBeInTheDocument();
    });
    expect(screen.getByPlaceholderText("Reply to the builder…")).toBeInTheDocument();
  });

  it("labels the deriving hero 'understand' when nothing is done yet", () => {
    render(<BuildingStage progress={makeProgress({ done: [] })} />);
    expect(screen.getByTestId("deriving")).toHaveTextContent(DERIVING_LABEL.understand);
  });

  it("advances the label to 'build' once understand has landed", () => {
    render(<BuildingStage progress={makeProgress({ done: ["understand"] })} />);
    expect(screen.getByTestId("deriving")).toHaveTextContent(DERIVING_LABEL.build);
  });

  it("advances the label to 'scenarios' once build has landed", () => {
    render(<BuildingStage progress={makeProgress({ done: ["understand", "build"] })} />);
    expect(screen.getByTestId("deriving")).toHaveTextContent(DERIVING_LABEL.scenarios);
  });

  it("shows the loading label and seven done checks when all three milestones land", () => {
    render(
      <BuildingStage progress={makeProgress({ done: ["understand", "build", "scenarios"] })} />,
    );
    expect(screen.getByTestId("deriving")).toHaveTextContent(DERIVING_LABEL.loading);
    expect(screen.getByTestId("checks")).toHaveTextContent("7 done");
  });

  it("null-guards a mid-init progress without crashing", () => {
    render(<BuildingStage progress={null} />);
    expect(screen.getByTestId("deriving")).toHaveTextContent(DERIVING_LABEL.understand);
    expect(screen.getByText(BUILDING_TABS[0].label)).toBeInTheDocument();
  });
});

describe("PanelBoundary", () => {
  it("renders the crash card when a child throws and Retry resets it", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    let crash = true;
    function Boom() {
      if (crash) throw new Error("panel exploded");
      return <div>panel recovered</div>;
    }

    render(
      <PanelBoundary>
        <Boom />
      </PanelBoundary>,
    );

    expect(screen.getByText("The right-side panel crashed")).toBeInTheDocument();

    crash = false;
    fireEvent.click(screen.getByText("Retry"));

    expect(screen.queryByText("The right-side panel crashed")).not.toBeInTheDocument();
    expect(screen.getByText("panel recovered")).toBeInTheDocument();

    spy.mockRestore();
  });

  it("shows the message but never the stack trace", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    function Boom() {
      const err = new Error("panel exploded");
      err.stack = "Error: panel exploded\n    at SecretInternalFrame (/srv/app/secret.js:1:1)";
      throw err;
    }

    render(
      <PanelBoundary>
        <Boom />
      </PanelBoundary>,
    );

    expect(screen.getByText(/panel exploded/)).toBeInTheDocument();
    expect(screen.queryByText(/SecretInternalFrame/)).toBeNull();

    spy.mockRestore();
  });
});
