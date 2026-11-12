import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import FolderView from "../../FolderView";

vi.mock("react-router", () => ({
  useParams: () => ({ folder: "all" }),
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

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));

vi.mock("../ActionBar", () => ({ default: () => null }));
vi.mock("../FolderListView", () => ({ default: () => null }));

function renderView() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <FolderView />
    </QueryClientProvider>,
  );
}

// SPFIX-r1 PROMPTS 1: at 1024 px the toolbar was 621 px in a 596 px pane,
// so Create prompt was cut at the right edge.
describe("All Prompts toolbar at narrow widths", () => {
  it("keeps Create prompt inside the page: the search gives way, then the buttons wrap", () => {
    renderView();
    const search = screen.getByPlaceholderText("Search in prompts");
    const create = screen.getByRole("button", { name: "Create prompt" });

    let row = search.parentElement;
    while (row && !row.contains(create)) row = row.parentElement;
    expect(getComputedStyle(row).flexWrap).toBe("wrap");

    const field = search.closest(".MuiTextField-root");
    expect(getComputedStyle(field).flexBasis).toBe("160px");
    expect(getComputedStyle(field).minWidth).toBe("160px");
  });
});
