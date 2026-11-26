import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import CompareCard from "../CompareCard";

const state = { compare: {}, patches: {}, requested: [], near: false };

vi.mock("src/api/agents/agentDetail", () => ({
  useCodeCompare: () => state.compare,
  useCodePatches: (agentId, versionId, against, batches) => {
    state.requested = batches;
    return batches.map((paths) => ({
      data: { files: paths.map((path) => state.patches[path]).filter(Boolean) },
      isError: false,
    }));
  },
  useCodeFile: () => ({ data: null, isLoading: false }),
}));

vi.mock("src/hooks/use-in-view", () => ({
  useInView: () => [() => {}, state.near],
}));

const VERSIONS = [
  {
    id: "v3",
    number: 3,
    label: "v3",
    source: { kind: "github", repository: "acme/agent", commit_sha: "ccc" },
  },
  {
    id: "v2",
    number: 2,
    label: "v2",
    source: { kind: "github", repository: "acme/agent", commit_sha: "bbb" },
  },
  {
    id: "v1",
    number: 1,
    label: "v1",
    isActive: true,
    source: { kind: "github", repository: "acme/agent", commit_sha: "aaa" },
  },
];

const file = (path, extra = {}) => ({
  path,
  previous_path: null,
  status: "modified",
  kind: "text",
  additions: 1,
  deletions: 1,
  collapsed: false,
  ...extra,
});

const summary = (files, extra = {}) => ({
  data: {
    version: "v3",
    against: "v2",
    same_code: false,
    env_changed: false,
    files,
    total_files: files.length,
    additions: files.reduce((sum, one) => sum + (one.additions ?? 0), 0),
    deletions: files.reduce((sum, one) => sum + (one.deletions ?? 0), 0),
    truncated: false,
    ...extra,
  },
  isLoading: false,
  isError: false,
});

const onChange = vi.fn();

const renderCard = (props = {}) =>
  render(
    <CompareCard
      agentId="a1"
      versions={VERSIONS}
      headId="v3"
      baseId="v2"
      onChange={onChange}
      onViewFile={vi.fn()}
      {...props}
    />,
  );

beforeEach(() => {
  state.compare = summary([file("agent.py")]);
  state.patches = {};
  state.requested = [];
  state.near = false;
  onChange.mockReset();
});

describe("CompareCard", () => {
  it("sums up the change and links the same compare on GitHub", () => {
    renderCard();
    expect(screen.getByTestId("compare-summary")).toHaveTextContent(
      "1 file changed · +1 -1",
    );
    expect(screen.getByTestId("compare-on-github")).toHaveAttribute(
      "href",
      "https://github.com/acme/agent/compare/bbb...ccc",
    );
  });

  it("says only env values changed when the code is the same", () => {
    state.compare = summary([], { same_code: true, env_changed: true });
    renderCard();
    expect(screen.getByTestId("compare-summary")).toHaveTextContent(
      "Only environment values changed in this version; the code is the same.",
    );
    expect(screen.queryByTestId("file-diff-block")).not.toBeInTheDocument();
  });

  it("loads no diff until a file comes near, then loads its batch", () => {
    state.patches["agent.py"] = {
      path: "agent.py",
      kind: "text",
      unchanged: false,
      hunks: [
        {
          old_start: 1,
          old_lines: 1,
          new_start: 1,
          new_lines: 1,
          lines: ["-old line", "+new line"],
        },
      ],
    };
    const { unmount } = renderCard();
    expect(state.requested).toEqual([]);
    unmount();

    state.near = true;
    renderCard();
    expect(state.requested).toEqual([["agent.py"]]);
    const block = screen.getByTestId("file-diff-block");
    expect(within(block).getByText("old line")).toBeInTheDocument();
    expect(within(block).getByText("new line")).toBeInTheDocument();
  });

  it("asks for a large diff only when Load diff is pressed", () => {
    state.compare = summary([
      file("big.py", { additions: 900, deletions: 0, collapsed: true }),
    ]);
    state.near = true;
    renderCard();
    expect(state.requested).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Load diff" }));

    expect(state.requested).toEqual([["big.py"]]);
  });

  it("shows binary and too large files without a diff", () => {
    state.compare = summary([
      file("model.bin", { kind: "binary", additions: null, deletions: null }),
      file("huge.py", { kind: "too_large", additions: 30000, deletions: 0 }),
    ]);
    renderCard();
    expect(screen.getByText("Binary file not shown.")).toBeInTheDocument();
    expect(
      screen.getByText("This diff is too large to show."),
    ).toBeInTheDocument();
  });

  it("says a file moved without changes was renamed", () => {
    state.compare = summary([
      file("new_name.py", {
        previous_path: "old_name.py",
        status: "renamed",
        additions: 0,
        deletions: 0,
      }),
    ]);
    state.patches["new_name.py"] = {
      path: "new_name.py",
      kind: "text",
      unchanged: false,
      hunks: [],
    };
    state.near = true;
    renderCard();
    expect(screen.getByText("old_name.py → new_name.py")).toBeInTheDocument();
    expect(
      screen.getByText("File renamed without changes."),
    ).toBeInTheDocument();
  });

  it("keeps the older version on the left whichever side is picked", () => {
    renderCard();
    fireEvent.mouseDown(screen.getAllByRole("combobox")[0]);
    fireEvent.click(screen.getByRole("option", { name: "v3" }));
    expect(onChange).toHaveBeenCalledWith("v3", "v3");

    onChange.mockReset();
    fireEvent.mouseDown(screen.getAllByRole("combobox")[1]);
    fireEvent.click(screen.getByRole("option", { name: /v1/ }));
    expect(onChange).toHaveBeenCalledWith("v2", "v1");
  });
});
