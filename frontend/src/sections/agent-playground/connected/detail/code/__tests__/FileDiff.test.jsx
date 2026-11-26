import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import FileDiff from "../FileDiff";

const state = { files: {} };

vi.mock("src/api/agents/agentDetail", () => ({
  useCodeFile: (agentId, versionId, path) =>
    state.files[`${versionId}:${path}`] || { data: null, isLoading: false },
}));

vi.mock("@monaco-editor/react", async () => {
  const PropTypes = (await import("prop-types")).default;
  const DiffEditor = ({ original, modified }) => (
    <div data-testid="diff-editor">
      <pre data-testid="diff-original">{original}</pre>
      <pre data-testid="diff-modified">{modified}</pre>
    </div>
  );
  DiffEditor.propTypes = {
    original: PropTypes.string,
    modified: PropTypes.string,
  };
  return { DiffEditor };
});

const text = (content) => ({
  data: { kind: "text", content, language: "python" },
  isLoading: false,
  isError: false,
});

const failed = (message) => ({
  data: null,
  isLoading: false,
  isError: true,
  error: { message },
  refetch: vi.fn(),
});

const show = () =>
  render(
    <FileDiff agentId="a" versionId="v2" againstId="v1" path="agent.py" />,
  );

beforeEach(() => {
  state.files = {};
});

describe("FileDiff", () => {
  it("diffs the two versions of the file", () => {
    state.files["v2:agent.py"] = text("print('two')");
    state.files["v1:agent.py"] = text("print('one')");

    show();

    expect(screen.getByTestId("diff-original")).toHaveTextContent(
      "print('one')",
    );
    expect(screen.getByTestId("diff-modified")).toHaveTextContent(
      "print('two')",
    );
  });

  it("shows the failure instead of diffing against an empty file", () => {
    state.files["v2:agent.py"] = text("print('two')");
    const previous = failed("Server exploded");
    state.files["v1:agent.py"] = previous;

    show();

    expect(screen.queryByTestId("diff-editor")).toBeNull();
    expect(screen.getByText("Couldn't load this file")).toBeInTheDocument();
    expect(screen.getByText("Server exploded")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(previous.refetch).toHaveBeenCalledTimes(1);
  });
});
