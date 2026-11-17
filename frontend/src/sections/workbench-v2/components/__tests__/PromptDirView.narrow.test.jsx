import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import PromptDirView from "../../PromptDirView";

vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  Outlet: () => <div data-testid="prompt-list" />,
}));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(() => Promise.resolve({ data: { result: [] } })) },
  endpoints: {
    develop: { runPrompt: { promptFolder: "/model-hub/prompt-folders/" } },
  },
}));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("src/components/FileSystem/FileSystem", () => ({
  default: ({ onFolderClick }) => (
    <nav data-testid="folder-rail">
      <button
        type="button"
        onClick={() => onFolderClick({ folderName: "Marketing", id: "f1" })}
      >
        Marketing
      </button>
    </nav>
  ),
}));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../AddFolder", () => ({ default: () => null }));
vi.mock("../CreateNewPrompt", () => ({ default: () => null }));
vi.mock("src/sections/workbench/ChoosePromptTemplateDrawer.jsx", () => ({
  ChoosePromptTemplateDrawer: () => null,
}));

const setupMatchMedia = window.matchMedia;
afterEach(() => {
  window.matchMedia = setupMatchMedia;
});

// A phone-width window: every "max-width" (down) breakpoint query matches.
function atPhoneWidth() {
  window.matchMedia = vi.fn().mockImplementation((query) => ({
    matches: /max-width/.test(query),
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}

function renderView() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <PromptDirView />
    </QueryClientProvider>,
  );
}

// At 390 px the 224 px folder rail (never narrower than
// 200 px) left the prompt list about 160 px, so its search and buttons were
// cut off. Below the sm breakpoint the rail now folds behind a Folders button.
describe("Prompt folders on a phone", () => {
  it("folds the folder rail behind a Folders button and gives the list the full width", () => {
    atPhoneWidth();
    renderView();
    const toggle = screen.getByRole("button", { name: "Folders" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByTestId("folder-rail")).not.toBeVisible();
    expect(screen.getByTestId("prompt-list")).toBeVisible();

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const rail = document.getElementById(toggle.getAttribute("aria-controls"));
    expect(rail).toContainElement(screen.getByTestId("folder-rail"));
    expect(screen.getByTestId("folder-rail")).toBeVisible();
  });

  it("folds the rail again after a folder is picked", () => {
    atPhoneWidth();
    renderView();
    const toggle = screen.getByRole("button", { name: "Folders" });
    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole("button", { name: "Marketing" }));
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByTestId("folder-rail")).not.toBeVisible();
  });

  it("keeps the rail beside the list on wider screens, with no Folders button", () => {
    renderView();
    expect(screen.queryByRole("button", { name: "Folders" })).toBeNull();
    expect(screen.getByTestId("folder-rail")).toBeVisible();
    expect(screen.getByTestId("prompt-list")).toBeVisible();
  });
});
