import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  renderWithRouter,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import SkillCompareView from "./SkillCompareView";

const mocks = vi.hoisted(() => ({
  skill: vi.fn(),
  labels: vi.fn(),
  compare: vi.fn(),
  restore: vi.fn(),
  push: vi.fn(),
}));
vi.mock("src/api/skills", () => ({
  useSkill: mocks.skill,
  useSkillLabels: mocks.labels,
  useSkillCompare: mocks.compare,
  useRestoreSkillVersion: () => ({
    mutateAsync: mocks.restore,
    isPending: false,
  }),
}));
vi.mock("src/routes/hooks", () => ({
  useParams: () => ({ skillId: "skill-1" }),
  useRouter: () => ({ push: mocks.push }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/skills/components/VersionSelect", () => {
  function VersionSelect({ value, onChange }) {
    return (
      <select
        aria-label="Version"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {[1, 2, "draft", "label:production", "label:missing"].map((ref) => (
          <option key={ref} value={ref}>
            {ref}
          </option>
        ))}
      </select>
    );
  }
  VersionSelect.propTypes = {
    value: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
    onChange: PropTypes.func,
  };
  return { default: VersionSelect };
});
vi.mock("src/sections/skills/components/TextDiff", () => {
  function TextDiff({ fromText, toText, fromTitle, toTitle }) {
    return (
      <div data-testid="text-diff">
        <span>
          {fromTitle} → {toTitle}
        </span>
        <pre data-testid="from-text">{fromText}</pre>
        <pre data-testid="to-text">{toText}</pre>
      </div>
    );
  }
  TextDiff.propTypes = {
    fromText: PropTypes.string,
    toText: PropTypes.string,
    fromTitle: PropTypes.string,
    toTitle: PropTypes.string,
  };
  return { default: TextDiff };
});

const comparison = {
  from: 1,
  to: 2,
  summary: { added: 1, removed: 1, modified: 1, unchanged: 1 },
  files: [
    { path: "z-unchanged.md", status: "unchanged" },
    {
      path: "SKILL.md",
      status: "modified",
      fromText: "Old instruction",
      toText: "New instruction",
    },
    { path: "added.md", status: "added", toText: "Added content" },
    { path: "removed.md", status: "removed", fromText: "Removed content" },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.skill.mockReturnValue({
    data: { id: "skill-1", name: "release-review", latestVersion: 2 },
    refetch: vi.fn(),
  });
  mocks.labels.mockReturnValue({
    data: [{ name: "production", version: 1 }],
    refetch: vi.fn(),
  });
  mocks.compare.mockImplementation((id) =>
    id ? { data: comparison, refetch: vi.fn() } : { isLoading: false },
  );
  mocks.restore.mockResolvedValue({ unchanged: false, version: { number: 3 } });
});

const renderCompare = (query = "from=1&to=2") =>
  renderWithRouter(<SkillCompareView />, {
    route: `/dashboard/skills/skill-1/compare?${query}`,
  });
const chooseFile = (name) =>
  fireEvent.click(
    within(screen.getByRole("list", { name: "Compared files" })).getByText(
      name,
    ),
  );

describe("SkillCompareView", () => {
  it("renders every file status and passes the correct texts to the shared diff", () => {
    renderCompare();
    const files = within(screen.getByRole("list", { name: "Compared files" }));
    for (const status of ["Added", "Removed", "Modified", "Unchanged"])
      expect(files.getByText(status)).toBeInTheDocument();
    chooseFile("SKILL.md");
    expect(screen.getByTestId("from-text")).toHaveTextContent(
      "Old instruction",
    );
    expect(screen.getByTestId("to-text")).toHaveTextContent("New instruction");
    chooseFile("added.md");
    expect(screen.getByTestId("from-text")).toBeEmptyDOMElement();
    expect(screen.getByTestId("to-text")).toHaveTextContent("Added content");
    chooseFile("removed.md");
    expect(screen.getByTestId("from-text")).toHaveTextContent(
      "Removed content",
    );
    expect(screen.getByTestId("to-text")).toBeEmptyDOMElement();
    chooseFile("z-unchanged.md");
    expect(
      screen.getByText("This file is identical in both versions."),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("text-diff")).not.toBeInTheDocument();
  });

  it("compares a label's resolved immutable version with the draft and preserves other URL params", () => {
    renderCompare("from=label:production&to=draft&tab=versions");
    expect(mocks.compare).toHaveBeenLastCalledWith("skill-1", 1, "draft");
    expect(
      screen.queryByRole("button", { name: /Restore draft/ }),
    ).not.toBeInTheDocument();
    fireEvent.change(
      within(screen.getByRole("group", { name: "Compare from" })).getByRole(
        "combobox",
      ),
      { target: { value: "2" } },
    );
    expect(mocks.compare).toHaveBeenLastCalledWith("skill-1", 2, "draft");
    const params = new URLSearchParams(window.location.search);
    expect(params.get("from")).toBe("2");
    expect(params.get("to")).toBe("draft");
    expect(params.get("tab")).toBe("versions");
  });

  it.each(["label:missing", "0", "1.5", "9007199254740992"])(
    "does not silently fall back for invalid selector %s",
    (from) => {
      renderCompare(`from=${from}&to=2`);
      expect(
        screen.getByText("Choose two available versions or your draft"),
      ).toBeInTheDocument();
      expect(mocks.compare.mock.lastCall[0]).toBeUndefined();
    },
  );

  it("requires confirmation before restoring a compared version", async () => {
    renderCompare();
    fireEvent.click(
      screen.getByRole("button", { name: "Restore v1 as new version" }),
    );
    expect(mocks.restore).not.toHaveBeenCalled();
    expect(screen.getByText("Restore v1 as new version?")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Restore as new version" }),
    );
    await waitFor(() =>
      expect(mocks.restore).toHaveBeenCalledWith({ number: 1 }),
    );
  });

  it.each(["loading", "error", "empty"])(
    "renders the %s review state",
    (state) => {
      renderCompare(`from=1&to=2&state=${state}`);
      const titles = {
        loading: "Comparing versions",
        error: "Could not compare these versions",
        empty: "No files to compare",
      };
      expect(
        state === "loading"
          ? screen.getByRole("progressbar", { name: titles[state] })
          : screen.getByText(titles[state]),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("list", { name: "Compared files" }),
      ).not.toBeInTheDocument();
    },
  );

  it("shows retry on query failure and does not expose stale restore actions", () => {
    const refetch = vi.fn();
    mocks.compare.mockReturnValue({ isError: true, data: comparison, refetch });
    renderCompare();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalledOnce();
    expect(
      screen.queryByRole("button", { name: "Restore v1 as new version" }),
    ).not.toBeInTheDocument();
  });
});
