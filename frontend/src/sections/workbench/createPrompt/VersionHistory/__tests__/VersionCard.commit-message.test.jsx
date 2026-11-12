import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PromptWorkbenchContext } from "../../WorkbenchContext";
import VersionCard from "../VersionCard";

vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));
vi.mock("src/components/custom-model-tools", () => ({ default: () => null }));
vi.mock("src/components/custom-model-dropdown/CustomModelDropdown", () => ({
  default: () => null,
}));
vi.mock("src/components/custom-model-options/CustomModelOptions", () => ({
  default: () => null,
}));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => children,
}));
vi.mock("../LabelDropdown/LabelDropdown", () => ({ default: () => null }));
vi.mock("../LabelDropdown/PromptLabel", () => ({ default: () => null }));

const workbench = {
  onRestoreVersion: vi.fn(),
  selectedVersions: [{ version: "V1" }],
};

// The prompt history API returns snake_case fields, the same as
// template_version and is_draft on this card.
const version = (extra) => ({
  id: "id-V2",
  template_name: "Support reply",
  template_version: "V2",
  is_draft: false,
  created_at: "2026-11-10T12:00:00Z",
  labels: [],
  variable_names: {},
  ...extra,
});

const renderCard = (v) =>
  render(
    <PromptWorkbenchContext.Provider value={workbench}>
      <VersionCard version={v} />
    </PromptWorkbenchContext.Provider>,
  );

// WLA-PROMPT-VERSIONCARD-COMMITMSG: the history card shows the message a
// version was committed with.
describe("VersionCard commit message", () => {
  it("shows the commit message the API returns", () => {
    renderCard(version({ commit_message: "Shorter greeting" }));
    expect(screen.getByText("Commit")).toBeInTheDocument();
    expect(screen.getByText("Shorter greeting")).toBeInTheDocument();
  });

  it("shows no commit box for a version without a message", () => {
    renderCard(version({ commit_message: "" }));
    expect(screen.queryByText("Commit")).not.toBeInTheDocument();
  });

  it("shows no commit box when the message is null or missing", () => {
    const { unmount } = renderCard(version({ commit_message: null }));
    expect(screen.queryByText("Commit")).not.toBeInTheDocument();
    unmount();
    renderCard(version());
    expect(screen.queryByText("Commit")).not.toBeInTheDocument();
  });

  // A commit saved with a blank message stores the version name, which the
  // card already shows in its version chip.
  it("shows no commit box when the message only repeats the version name", () => {
    renderCard(version({ commit_message: "V2" }));
    expect(screen.queryByText("Commit")).not.toBeInTheDocument();
  });

  it("shows no commit box for a message of spaces and trims a real one", () => {
    const { unmount } = renderCard(version({ commit_message: "   " }));
    expect(screen.queryByText("Commit")).not.toBeInTheDocument();
    unmount();
    renderCard(version({ commit_message: "  Shorter greeting  " }));
    expect(screen.getByText("Shorter greeting").textContent).toBe(
      "Shorter greeting",
    );
  });
});
