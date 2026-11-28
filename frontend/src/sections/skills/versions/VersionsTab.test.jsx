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
import MoveLabelDialog from "./MoveLabelDialog";
import RestoreVersionDialog from "./RestoreVersionDialog";
import LabelProtectionDialog from "../governance/LabelProtectionDialog";

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
vi.mock("./MoveLabelDialog", () => ({ default: vi.fn(() => null) }));
vi.mock("./LabelHistoryDrawer", () => ({ default: vi.fn(() => null) }));
vi.mock("./RestoreVersionDialog", () => ({ default: vi.fn(() => null) }));

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
      screen.getByText(`Release ${number}`).closest('tr'),
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
    expect(screen.getByRole("button", { name: "Actions for production" })).toBeInTheDocument();
    mocks.labels.mockReturnValue({ data: [] });
    view.rerender(<VersionsTab skill={skill} />);
    expect(screen.queryByRole("button", { name: "Actions for production" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Label settings" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "History", exact: true }));
    expect(LabelHistoryDrawer).toHaveBeenLastCalledWith(expect.objectContaining({ open: true, label: "", skill }), expect.anything());
  });
  it("names a protected label with its meaning, never a bare \"Protected\" (UXF-09)", () => {
    renderWithRouter(<VersionsTab skill={{ ...skill, protectedLabelNames: ["production"] }} />);
    expect(screen.getByText("Protected label (only admins can move it)")).toBeInTheDocument();
    expect(screen.queryByText("Protected")).not.toBeInTheDocument();
    expect(screen.queryByText(/reviewed release/)).not.toBeInTheDocument();
    expect(document.body.textContent.replace(/protected labels?/gi, "").match(/protected/i)).toBeNull();
  });
  it("states the workspace's label rule above the labels; only admins get Change rules", () => {
    const view = renderWithRouter(<VersionsTab skill={{ ...skill, protectedLabelNames: ["production", "staging"], permissions: { canManageLabelProtection: false } }} />);
    expect(screen.getByText("Protected labels (only workspace admins can move them, and automation can't): production, staging.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Change rules" })).not.toBeInTheDocument();
    view.rerender(<VersionsTab skill={{ ...skill, protectedLabelNames: [], permissions: { canManageLabelProtection: true } }} />);
    expect(screen.getByText("No labels are protected.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Change rules" }));
    expect(LabelProtectionDialog).toHaveBeenLastCalledWith(expect.objectContaining({ open: true }), expect.anything());
  });
  it("shows no rule when the skill's rules are not loaded", () => {
    renderWithRouter(<VersionsTab skill={skill} />);
    expect(screen.queryByTestId("release-rule-note")).not.toBeInTheDocument();
  });
  it("keeps every label action in ⋯, with a reason when Move is unavailable (UXF-12)", async () => {
    const user = userEvent.setup();
    mocks.labels.mockReturnValue({ data: [{ name: "production", version: 2, canMove: false, isProtected: true }, { name: "staging", version: 3, canMove: true }] });
    renderWithRouter(<VersionsTab skill={skill} />);
    expect(screen.queryByRole("button", { name: /^Move (production|staging)$/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^History for / })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Actions for production" }));
    expect(screen.getByRole("menuitem", { name: /^Move/ })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("menuitem", { name: /^Move/ })).toHaveTextContent("Only workspace admins can move it");
    await user.click(screen.getByRole("menuitem", { name: "History", exact: true }));
    expect(LabelHistoryDrawer).toHaveBeenLastCalledWith(expect.objectContaining({ open: true, label: "production" }), expect.anything());
    await user.click(screen.getByRole("button", { name: "Actions for staging" }));
    await user.click(screen.getByRole("menuitem", { name: "Move", exact: true }));
    expect(MoveLabelDialog).toHaveBeenLastCalledWith(expect.objectContaining({ open: true, label: "staging" }), expect.anything());
  });
  it("reads each version as one card: label, note, one facts line, badges, with no field prefixes or raw digest", async () => {
    mocks.versions.mockReturnValue(result([{ ...row(3), labels: ["production"] }, row(2)]));
    renderWithRouter(<VersionsTab skill={skill} />);
    const card = screen.getByText("Release 3").closest("tr");
    expect(card.querySelectorAll("[data-label]")).toHaveLength(0);
    expect(within(card).getByRole("button", { name: /^v3, show full/ })).toBeInTheDocument();
    expect(within(card).getByText(/^Test author · .+ · 2 files$/)).toBeInTheDocument();
    expect(within(card).getByText("Production")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Actions for v3" })).toBeInTheDocument();
    expect(screen.queryByText(/aaaaaaaaaaaa/)).not.toBeInTheDocument();
    // The label opens the full digest; it never navigates the row.
    fireEvent.click(within(card).getByRole("button", { name: /^v3, show full/ }));
    expect(mocks.push).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "Copy full digest" }));
    expect(mocks.push).not.toHaveBeenCalled();
  });

  it("only compares exactly two selected rows, ordered oldest first", () => {
    renderWithRouter(<VersionsTab skill={skill} />);
    expect(
      screen.queryByRole("button", { name: /^Compare v/ }),
    ).not.toBeInTheDocument();
    checkRow(3);
    expect(screen.getByRole("button", { name: "Select two versions" })).toBeDisabled();
    checkRow(1);
    expect(screen.getByText("2 versions selected")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Compare v/ }));
    expect(mocks.push).toHaveBeenCalledWith(
      "/dashboard/skills/skill-1/compare?from=1&to=3",
    );
    expect(screen.getByRole("checkbox", { name: "Select v2 for comparison" })).toBeDisabled();
    expect(mocks.push).toHaveBeenCalledTimes(1);
  });

  it("keeps selection attached to version identities when a publish refreshes the page", () => {
    const { rerender } = renderWithRouter(<VersionsTab skill={skill} />);
    checkRow(3);
    checkRow(2);
    mocks.versions.mockReturnValue(result([row(4), row(3), row(2)]));
    rerender(<VersionsTab skill={{ ...skill, latestVersion: 4 }} />);
    fireEvent.click(screen.getByRole("button", { name: /^Compare v/ }));
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
      screen.queryByRole("button", { name: /^Compare v/ }),
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

  it("links to files and disables unavailable row comparisons with a reason", () => {
    renderWithRouter(<VersionsTab skill={skill} />);
    fireEvent.click(screen.getByRole("button", { name: "Actions for v1" }));
    const previous = screen.getByRole("menuitem", { name: /^Compare with previous/ });
    expect(previous).toHaveAttribute("aria-disabled", "true");
    expect(previous).toHaveTextContent("v1 is the first version");
    fireEvent.click(screen.getByRole("menuitem", { name: "View files" }));
    expect(mocks.push).toHaveBeenCalledWith(
      "/dashboard/skills/skill-1?tab=files&version=1",
    );
    fireEvent.click(screen.getByRole("button", { name: "Actions for v2" }));
    const production = screen.getByRole("menuitem", { name: /^Compare with production/ });
    expect(production).toHaveAttribute("aria-disabled", "true");
    expect(production).toHaveTextContent("This version is in production");
  });

  it("opens a version's files from the whole row; ⋯ lists that first, then the other actions (UXF-12)", async () => {
    const user = userEvent.setup();
    renderWithRouter(<VersionsTab skill={skill} />);
    const card = screen.getByText("Release 3").closest("tr");
    expect(card).toHaveAttribute("data-row-link", "true");
    fireEvent.click(within(card).getByText("Release 3"));
    expect(mocks.push).toHaveBeenLastCalledWith("/dashboard/skills/skill-1?tab=files&version=3");
    // The compare checkbox keeps its own click.
    mocks.push.mockClear();
    checkRow(3);
    expect(mocks.push).not.toHaveBeenCalled();
    // Enter on the focused row opens it too.
    card.focus();
    fireEvent.keyDown(card, { key: "Enter" });
    expect(mocks.push).toHaveBeenLastCalledWith("/dashboard/skills/skill-1?tab=files&version=3");
    mocks.push.mockClear();
    await user.click(within(card).getByRole("button", { name: "Actions for v3" }));
    expect(mocks.push).not.toHaveBeenCalled();
    expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "View files", "Compare with previous", "Compare with production", "Restore as new version", "Set label…",
    ]);
    await user.click(screen.getByRole("menuitem", { name: "Restore as new version" }));
    expect(RestoreVersionDialog).toHaveBeenLastCalledWith(expect.objectContaining({ number: 3 }), expect.anything());
    // Only ghost (text) buttons and the ⋯ live in rows; no outlined row buttons.
    expect(card.querySelector(".MuiButton-outlined, .MuiButton-contained")).toBeNull();
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

vi.mock("src/sections/skills/governance/LabelProtectionDialog", () => ({ default: vi.fn(() => null) }));
