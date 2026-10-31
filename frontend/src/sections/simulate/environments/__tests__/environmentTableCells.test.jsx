import { describe, it, expect } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import {
  AgentTypeCell,
  NameCell,
  AgentCell,
  LastRunCell,
  OwnerCell,
  RunsCell,
} from "../components/environmentTableCells";
import { NOT_PROVIDED } from "../myEnvironments.constants";

const cell = (value) => render(<AgentTypeCell getValue={() => value} />);

describe("AgentTypeCell", () => {
  it("labels a known modality", () => {
    cell("voice");
    expect(screen.getByText("Voice")).toBeInTheDocument();
  });

  it("labels the text modality as Chat", () => {
    cell("text");
    expect(screen.getByText("Chat")).toBeInTheDocument();
  });

  it("shows Not identified for a missing type instead of defaulting to Chat", () => {
    cell(undefined);
    expect(screen.getByText("Not identified")).toBeInTheDocument();
    expect(screen.queryByText("Chat")).toBeNull();
  });

  it("shows Not identified for an unrecognised type", () => {
    cell("some_backend_type_we_dont_map");
    expect(screen.getByText("Not identified")).toBeInTheDocument();
    expect(screen.queryByText("Chat")).toBeNull();
  });
});

const rowCell = (Cell, original) =>
  render(<Cell row={{ original }} getValue={() => undefined} />);
const valueCell = (Cell, value) =>
  render(<Cell row={{ original: {} }} getValue={() => value} />);

describe("NameCell", () => {
  it("shows the name, the description under it and a placeholder version", () => {
    rowCell(NameCell, {
      name: "Support Line",
      description: "Handles inbound billing calls",
      envVersion: NOT_PROVIDED,
    });
    expect(screen.getByText("Support Line")).toBeInTheDocument();
    expect(
      screen.getByText("Handles inbound billing calls"),
    ).toBeInTheDocument();
    expect(screen.getByText("Version soon")).toBeInTheDocument();
  });
});

describe("AgentCell", () => {
  it("shows a placeholder when the API has no agent field", () => {
    rowCell(AgentCell, { agent: NOT_PROVIDED, agentTags: NOT_PROVIDED });
    expect(screen.getByText("Agent soon")).toBeInTheDocument();
  });

  it("shows a dash for an environment with no agent", () => {
    rowCell(AgentCell, { agent: null, agentTags: NOT_PROVIDED });
    expect(screen.getByText("-")).toBeInTheDocument();
  });

  it("shows the agent's name and a placeholder for its tags", () => {
    rowCell(AgentCell, {
      agent: { id: "a1", name: "Billing" },
      agentTags: NOT_PROVIDED,
    });
    expect(screen.getByText("Billing")).toBeInTheDocument();
    expect(screen.getByText("Tags soon")).toBeInTheDocument();
  });
});

describe("LastRunCell", () => {
  it("shows a placeholder when the API has no last run field", () => {
    rowCell(LastRunCell, { lastRun: NOT_PROVIDED });
    expect(screen.getByText("Last run soon")).toBeInTheDocument();
  });

  it("shows a dash before any scored run", () => {
    rowCell(LastRunCell, { lastRun: null });
    expect(screen.getByText("-")).toBeInTheDocument();
  });

  it("shows the rate and an up move", () => {
    rowCell(LastRunCell, { lastRun: { rate: 75, delta: 5 } });
    expect(screen.getByText("75%")).toBeInTheDocument();
    expect(screen.getByText("▲5")).toBeInTheDocument();
  });

  it("shows a down move", () => {
    rowCell(LastRunCell, { lastRun: { rate: 75, delta: -3 } });
    expect(screen.getByText("▼3")).toBeInTheDocument();
  });

  it("shows only the rate when nothing moved", () => {
    rowCell(LastRunCell, { lastRun: { rate: 75, delta: 0 } });
    expect(screen.getByText("75%")).toBeInTheDocument();
    expect(screen.queryByText(/[▲▼]/)).toBeNull();
  });

  it("shows a placeholder when the API has no previous rate", () => {
    rowCell(LastRunCell, { lastRun: { rate: 75, delta: NOT_PROVIDED } });
    expect(screen.getByText("75%")).toBeInTheDocument();
    expect(screen.getByText("Trend soon")).toBeInTheDocument();
  });
});

describe("OwnerCell", () => {
  it("shows a placeholder when the API has no owner field", () => {
    valueCell(OwnerCell, NOT_PROVIDED);
    expect(screen.getByText("Owner soon")).toBeInTheDocument();
  });

  it("shows a dash for an unknown owner", () => {
    valueCell(OwnerCell, null);
    expect(screen.getByText("-")).toBeInTheDocument();
  });

  it("shows the owner's initials", () => {
    valueCell(OwnerCell, { id: "u1", name: "Vel Shah" });
    expect(screen.getByText("VS")).toBeInTheDocument();
  });
});

describe("RunsCell", () => {
  it("shows the run count", () => {
    valueCell(RunsCell, 3);
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("shows a dash when the count is unknown", () => {
    valueCell(RunsCell, null);
    expect(screen.getByText("-")).toBeInTheDocument();
  });
});
