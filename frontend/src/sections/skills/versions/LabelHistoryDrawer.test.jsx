import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, renderWithRouter, screen } from "src/utils/test-utils";
import LabelHistoryDrawer from "./LabelHistoryDrawer";

const mocks = vi.hoisted(() => ({ history: vi.fn(), covered: vi.fn() }));
vi.mock("src/api/skills", () => ({ useSkillLabelHistory: mocks.history }));
vi.mock("src/hooks/use-drawer-stack", () => ({
  useDrawerCovered: mocks.covered,
  coveredDrawerSx: () => undefined,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
const skill = { id: "skill-1", name: "release-review" };
const events = [
  {
    label: "production",
    fromVersion: 2,
    toVersion: null,
    actor: null,
    note: "Retired",
    createdAt: "2026-11-03T12:00:00Z",
  },
  {
    label: "production",
    fromVersion: 1,
    toVersion: 2,
    actor: { name: "Release bot", kind: "api_key" },
    note: "Reviewed",
    createdAt: "2026-11-02T12:00:00Z",
  },
  {
    label: "production",
    fromVersion: null,
    toVersion: 1,
    actor: { name: "Test author", kind: "user" },
    createdAt: "2026-11-01T12:00:00Z",
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  mocks.history.mockReturnValue({ data: { results: events, count: 3 } });
});

describe("LabelHistoryDrawer", () => {
  it("renders assignment, promotion, removal and actors in the shared drawer stack", () => {
    const onClose = vi.fn();
    renderWithRouter(
      <LabelHistoryDrawer
        open
        onClose={onClose}
        skill={skill}
        label="production"
      />,
    );
    expect(mocks.covered).toHaveBeenCalledWith(true);
    expect(mocks.history).toHaveBeenCalledWith(skill.id, "production");
    expect(
      screen.getByRole("dialog", { name: "production history" }),
    ).toBeInTheDocument();
    expect(screen.getByText("production: v2 → Removed")).toBeInTheDocument();
    expect(screen.getByText("production: v1 → v2")).toBeInTheDocument();
    expect(screen.getByText("production: Not set → v1")).toBeInTheDocument();
    expect(screen.getByText(/Release bot \(API key\)/)).toBeInTheDocument();
    expect(screen.getByText(/Unknown actor/)).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Close label history" }),
    );
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("does not fetch history while closed", () => {
    renderWithRouter(
      <LabelHistoryDrawer open={false} onClose={vi.fn()} skill={skill} />,
    );
    expect(mocks.history).toHaveBeenCalledWith(undefined, undefined);
  });

  it("discloses when only the most recent page is available", () => {
    mocks.history.mockReturnValue({ data: { results: events, count: 100 } });
    renderWithRouter(
      <LabelHistoryDrawer open onClose={vi.fn()} skill={skill} />,
    );
    expect(
      screen.getByText("Showing the latest 3 of 100 label changes."),
    ).toBeInTheDocument();
  });

  it.each(["loading", "error", "empty"])("shows the %s preview", (state) => {
    renderWithRouter(
      <LabelHistoryDrawer open onClose={vi.fn()} skill={skill} />,
      { route: `/?state=${state}` },
    );
    const text = {
      loading: "Loading label history",
      error: "Could not load label history",
      empty: "No label changes yet",
    };
    expect(
      state === "loading"
        ? screen.getByRole("progressbar", { name: text[state] })
        : screen.getByText(text[state]),
    ).toBeInTheDocument();
  });
});
