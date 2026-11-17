import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  isZero,
  rowOf,
} from "src/sections/workbench/__tests__/promptsContrast";
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

// The View Docs / Use template / Create prompt group could
// not shrink or wrap, so on a phone (about 358 px of room) Create prompt ran
// past the right edge. jsdom has no layout: this pins the group's styles.
describe("All Prompts toolbar at phone width", () => {
  it("lets the button group wrap onto another row while each label stays on one line", () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <FolderView />
      </QueryClientProvider>,
    );
    const useTemplate = screen.getByRole("button", { name: "Use template" });
    const create = screen.getByRole("button", { name: "Create prompt" });
    const group = rowOf(useTemplate, create);
    const style = getComputedStyle(group);
    expect(style.flexWrap).toBe("wrap");
    expect(isZero(style.minWidth)).toBe(true);
    expect(style.flexShrink).not.toBe("0");
    expect(style.justifyContent).toBe("flex-end");
    expect(getComputedStyle(create).whiteSpace).toBe("nowrap");
  });
});
