import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import {
  fireEvent,
  renderWithRouter,
  screen,
  within,
} from "src/utils/test-utils";
import VersionsTab from "./VersionsTab";
import LabelHistoryDrawer from "./LabelHistoryDrawer";

const mocks = vi.hoisted(() => ({
  versions: vi.fn(),
  labels: vi.fn(),
  push: vi.fn(),
}));
vi.mock("src/api/skills", () => ({
  useSkillVersions: mocks.versions,
  useSkillLabels: mocks.labels,
}));
vi.mock("src/routes/hooks", () => ({
  useRouter: () => ({ push: mocks.push }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("./MoveLabelDialog", () => ({ default: () => null }));
vi.mock("./LabelHistoryDrawer", () => ({ default: vi.fn(() => null) }));
vi.mock("./RestoreVersionDialog", () => ({ default: () => null }));

const skill = { id: "skill-1", name: "release-review", latestVersion: 3 };
const row = (number) => ({
  id: `version-${number}`,
  number,
  note: `Release ${number}`,
  createdAt: "2026-11-01T12:00:00Z",
  createdBy: { name: "Test author" },
  labels: [],
  digest: `sha256:${"a".repeat(64)}`,
  fileCount: 2,
});
const result = (rows = [row(3), row(2), row(1)]) => ({
  data: { results: rows, count: 30 },
  refetch: vi.fn(),
  isLoading: false,
});
const checkRow = (number) =>
  fireEvent.click(
    within(
      screen.getByText(`Release ${number}`).closest('[role="row"]'),
    ).getByRole("checkbox"),
  );

beforeEach(() => {
  vi.clearAllMocks();
  mocks.versions.mockReturnValue(result());
  mocks.labels.mockReturnValue({ data: [{ name: "production", version: 2 }] });
});

describe("VersionsTab", () => {
  it("keeps unfiltered label history accessible after the last label is removed", () => {
    const view = renderWithRouter(<VersionsTab skill={skill} />);
    expect(screen.getByRole("button", { name: "History for production" })).toBeInTheDocument();
    mocks.labels.mockReturnValue({ data: [] });
    view.rerender(<VersionsTab skill={skill} />);
    expect(screen.queryByRole("button", { name: "History for production" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Label history" }));
    expect(LabelHistoryDrawer).toHaveBeenLastCalledWith(expect.objectContaining({ open: true, label: "", skill }), expect.anything());
  });
  it("only compares exactly two selected rows, ordered oldest first", () => {
    renderWithRouter(<VersionsTab skill={skill} />);
    expect(
      screen.queryByRole("button", { name: "Compare" }),
    ).not.toBeInTheDocument();
    checkRow(3);
    expect(screen.getByRole("button", { name: "Compare" })).toBeDisabled();
    checkRow(1);
    fireEvent.click(screen.getByRole("button", { name: "Compare" }));
    expect(mocks.push).toHaveBeenCalledWith(
      "/dashboard/skills/skill-1/compare?from=1&to=3",
    );
    checkRow(2);
    expect(screen.getByRole("button", { name: "Compare" })).toBeDisabled();
  });

  it("keeps selection attached to version identities when a publish refreshes the page", () => {
    const { rerender } = renderWithRouter(<VersionsTab skill={skill} />);
    checkRow(3);
    checkRow(2);
    mocks.versions.mockReturnValue(result([row(4), row(3), row(2)]));
    rerender(<VersionsTab skill={{ ...skill, latestVersion: 4 }} />);
    fireEvent.click(screen.getByRole("button", { name: "Compare" }));
    expect(mocks.push).toHaveBeenCalledWith(
      "/dashboard/skills/skill-1/compare?from=2&to=3",
    );
  });

  it("clears the current-page selection when paging", () => {
    renderWithRouter(<VersionsTab skill={skill} />);
    checkRow(3);
    checkRow(2);
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(
      screen.queryByRole("button", { name: "Compare" }),
    ).not.toBeInTheDocument();
    expect(mocks.versions).toHaveBeenLastCalledWith(skill.id, {
      page: 2,
      pageSize: 25,
    });
  });

  it("opens the version menu on the first full pointer click", async () => {
    const user = userEvent.setup();
    renderWithRouter(<VersionsTab skill={skill} />);
    await user.click(screen.getByRole("button", { name: "Actions for v2" }));
    await user.click(screen.getByRole("menuitem", { name: "Compare with previous" }));
    expect(mocks.push).toHaveBeenCalledWith(
      "/dashboard/skills/skill-1/compare?from=1&to=2",
    );
  });

  it("links to files and disables unavailable row comparisons", () => {
    renderWithRouter(<VersionsTab skill={skill} />);
    fireEvent.click(screen.getByRole("button", { name: "Actions for v1" }));
    expect(
      screen.getByRole("menuitem", { name: "Compare with previous" }),
    ).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(screen.getByRole("menuitem", { name: "View files" }));
    expect(mocks.push).toHaveBeenCalledWith(
      "/dashboard/skills/skill-1?tab=files&version=1",
    );
    fireEvent.click(screen.getByRole("button", { name: "Actions for v2" }));
    expect(
      screen.getByRole("menuitem", { name: "Compare with production" }),
    ).toHaveAttribute("aria-disabled", "true");
  });

  it.each(["loading", "error", "empty"])(
    "shows the %s URL preview even when a query is loading",
    (state) => {
      mocks.versions.mockReturnValue({ isLoading: true });
      renderWithRouter(<VersionsTab skill={skill} />, {
        route: `/?state=${state}`,
      });
      const text = {
        loading: "Loading versions",
        error: "Could not load versions",
        empty: "No published versions yet",
      };
      expect(
        state === "loading"
          ? screen.getByRole("progressbar", { name: text[state] })
          : screen.getByText(text[state]),
      ).toBeInTheDocument();
    },
  );
});

vi.mock("src/sections/skills/governance/LabelProtectionDialog", () => ({ default: () => null }));
