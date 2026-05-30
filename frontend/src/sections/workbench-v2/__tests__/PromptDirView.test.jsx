/* eslint-disable react/prop-types */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import PromptDirView from "../PromptDirView";
import { usePromptStore } from "../store/usePromptStore";

vi.mock("react-router", () => ({
  Outlet: () => <div data-testid="prompt-dir-outlet" />,
}));

vi.mock("src/components/FileSystem/FileSystem", () => ({
  default: () => <div data-testid="prompt-file-system" />,
}));

vi.mock("../components/AddFolder", () => ({
  default: ({ open }) => (
    <div data-testid="add-folder" data-open={open ? "true" : "false"} />
  ),
}));

vi.mock("../components/CreateNewPrompt", () => ({
  default: ({ open }) => (
    <div data-testid="create-new-prompt" data-open={open ? "true" : "false"} />
  ),
}));

vi.mock("src/sections/workbench/ChoosePromptTemplateDrawer.jsx", () => ({
  ChoosePromptTemplateDrawer: ({ open }) => (
    <div
      data-testid="choose-template-drawer"
      data-open={open ? "true" : "false"}
    />
  ),
}));

vi.mock("src/utils/Mixpanel", () => ({
  Events: { promptCreateClicked: "promptCreateClicked" },
  PropertyName: { click: "click" },
  trackEvent: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(() => Promise.resolve({ data: { result: [] } })),
  },
  endpoints: {
    develop: {
      runPrompt: {
        promptFolder: "/model-hub/prompt-folders/",
      },
    },
  },
}));

function renderView(route) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[route]}>
        <PromptDirView />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("PromptDirView onboarding route mode", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    usePromptStore.setState({ newPromptModal: false });
  });

  it("opens the create prompt modal for onboarding create links", async () => {
    renderView(
      "/dashboard/workbench/all?source=onboarding&action=create-prompt",
    );

    await waitFor(() =>
      expect(screen.getByTestId("create-new-prompt")).toHaveAttribute(
        "data-open",
        "true",
      ),
    );
  });

  it("opens the create prompt modal for journey-step links", async () => {
    renderView(
      "/dashboard/workbench/all?tour_anchor=prompt_create_button&journey_step=start_prompt",
    );

    await waitFor(() =>
      expect(screen.getByTestId("create-new-prompt")).toHaveAttribute(
        "data-open",
        "true",
      ),
    );
  });
});
