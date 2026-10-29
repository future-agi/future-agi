import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import CodeCard from "../CodeCard";
import { buildTree } from "../FileTree";

const state = { tree: {}, changes: {}, files: {} };

vi.mock("src/api/agents/agentDetail", () => ({
  useCodeTree: () => state.tree,
  useCodeChanges: () => state.changes,
  useCodeFile: (agentId, versionId, path) =>
    state.files[`${versionId}:${path}`] || { data: null, isLoading: false },
}));

vi.mock("@monaco-editor/react", () => ({
  Editor: ({ path, language, value }) => (
    <pre data-testid="editor" data-path={path} data-language={language}>
      {value}
    </pre>
  ),
  DiffEditor: ({ original, modified }) => (
    <div data-testid="diff-editor">
      <pre data-testid="diff-original">{original}</pre>
      <pre data-testid="diff-modified">{modified}</pre>
    </div>
  ),
}));

let currentLocation;
const LocationProbe = () => {
  currentLocation = useLocation();
  return null;
};

const renderCard = (search = "") =>
  render(
    <MemoryRouter initialEntries={[`/agents/a1/definition${search}`]}>
      <Routes>
        <Route
          path="/agents/a1/definition"
          element={<CodeCard agentId="a1" versionId="v2" versionLabel="v2" />}
        />
      </Routes>
      <LocationProbe />
    </MemoryRouter>,
  );

const FILES = [
  { path: "agents/support/agent.py", size: 40, kind: "text" },
  { path: "agents/support/prompts/system.md", size: 9, kind: "text" },
  { path: "agents/support/model.bin", size: 9, kind: "binary" },
];

beforeEach(() => {
  state.tree = {
    data: { subfolder: "agents/support", files: FILES, truncated: false },
    isLoading: false,
    isError: false,
  };
  state.changes = {
    data: {
      against: "v1",
      files: [{ path: "agents/support/agent.py", status: "modified" }],
    },
  };
  state.files = {
    "v2:agents/support/agent.py": {
      data: {
        path: "agents/support/agent.py",
        kind: "text",
        language: "python",
        content: "def run():\n    return 'hello'\n",
      },
      isLoading: false,
    },
    "v1:agents/support/agent.py": {
      data: {
        path: "agents/support/agent.py",
        kind: "text",
        language: "python",
        content: "def run():\n    return 'hi'\n",
      },
      isLoading: false,
    },
    "v2:agents/support/model.bin": {
      data: {
        path: "agents/support/model.bin",
        kind: "binary",
        language: "plaintext",
      },
      isLoading: false,
    },
  };
});

describe("buildTree", () => {
  it("nests files under their folders, relative to the agent's folder", () => {
    const tree = buildTree(FILES, "agents/support");
    expect([...tree.folders.keys()]).toEqual(["prompts"]);
    expect(tree.files.map((f) => f.name)).toEqual(["agent.py", "model.bin"]);
    expect(tree.folders.get("prompts").files[0].name).toBe("system.md");
  });
});

describe("CodeCard", () => {
  it("lists the files, counts them and says what changed", () => {
    renderCard();
    expect(screen.getByText("Files · 3 files · v2")).toBeInTheDocument();
    expect(screen.getByTestId("code-changed-count")).toHaveTextContent(
      "1 changed since the previous version",
    );
    expect(
      screen.getByTestId("file-agents/support/agent.py"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("modified")).toBeInTheDocument();
    expect(screen.getByTestId("file-viewer-empty")).toBeInTheDocument();
  });

  it("opens a file from the tree and keeps it in the URL", () => {
    renderCard();
    fireEvent.click(screen.getByTestId("file-agents/support/agent.py"));
    expect(currentLocation.search).toBe("?file=agents%2Fsupport%2Fagent.py");
    expect(screen.getByTestId("editor")).toHaveAttribute(
      "data-language",
      "python",
    );
    expect(screen.getByTestId("editor")).toHaveTextContent("return 'hello'");
    expect(screen.getByTestId("code-changed-chip")).toHaveTextContent(
      "modified in v2",
    );
  });

  it("lands on the file a link names", () => {
    renderCard("?file=agents%2Fsupport%2Fmodel.bin");
    expect(screen.getByTestId("code-path")).toHaveTextContent(
      "agents/support/model.bin",
    );
    expect(screen.getByTestId("file-viewer-kind")).toHaveTextContent(
      "This file is binary",
    );
  });

  it("diffs both sides in the browser when the diff is on", () => {
    renderCard("?file=agents%2Fsupport%2Fagent.py&diff=1");
    expect(screen.getByTestId("diff-original")).toHaveTextContent(
      "return 'hi'",
    );
    expect(screen.getByTestId("diff-modified")).toHaveTextContent(
      "return 'hello'",
    );
    fireEvent.click(screen.getByRole("button", { name: "Diff vs previous" }));
    expect(currentLocation.search).toBe("?file=agents%2Fsupport%2Fagent.py");
    expect(screen.getByTestId("editor")).toBeInTheDocument();
  });

  it("offers no diff for a first version", () => {
    state.changes = {
      data: { against: null, files: [], unavailable: "no_previous_version" },
    };
    renderCard();
    expect(
      screen.queryByRole("button", { name: "Diff vs previous" }),
    ).toBeNull();
    expect(screen.queryByTestId("code-changed-count")).toBeNull();
  });

  it("says when a version has no code", () => {
    state.tree = { data: null, isLoading: false, isError: false };
    renderCard();
    expect(screen.getByTestId("code-empty")).toHaveTextContent(
      "No code is stored for this version.",
    );
  });
});
