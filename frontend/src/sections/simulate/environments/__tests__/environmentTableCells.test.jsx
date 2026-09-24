import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";
import { AgentTypeCell } from "../components/environmentTableCells";

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
