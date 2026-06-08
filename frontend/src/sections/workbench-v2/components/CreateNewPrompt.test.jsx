import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import CreateNewPrompt from "./CreateNewPrompt";

const mockRecordActivationEvent = vi.hoisted(() => vi.fn());
const mockOnClose = vi.fn();

vi.mock("src/utils/axios", () => ({
  default: {
    post: vi.fn(),
  },
  endpoints: {
    develop: {
      runPrompt: {
        createPromptDraft: "/model-hub/prompt-template/draft/",
      },
    },
  },
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/utils/Mixpanel", () => ({
  Events: {
    promptCreateClicked: "promptCreateClicked",
    promptNewPromptModeSelected: "promptNewPromptModeSelected",
  },
  PropertyName: {
    click: "click",
    type: "type",
  },
  trackEvent: vi.fn(),
}));

vi.mock("src/sections/onboarding-home/hooks/useRecordActivationEvent", () => ({
  useRecordActivationEvent: () => ({
    mutate: mockRecordActivationEvent,
  }),
}));

import axios from "src/utils/axios";

const theme = createTheme();

const LocationProbe = () => {
  const location = useLocation();
  return (
    <div data-testid="location">
      {location.pathname}
      {location.search}
    </div>
  );
};

const renderPromptDialog = (route) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });

  return render(
    <ThemeProvider theme={theme}>
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[route]}>
          <LocationProbe />
          <Routes>
            <Route
              path="/dashboard/workbench/:folder"
              element={<CreateNewPrompt open onClose={mockOnClose} />}
            />
            <Route
              path="/dashboard/workbench/create/:id"
              element={<div data-testid="created-prompt-route" />}
            />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );
};

describe("CreateNewPrompt onboarding routes", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    axios.post.mockResolvedValue({
      data: {
        result: {
          rootTemplate: "prompt-1",
        },
      },
    });
  });

  it("continues onboarding-created prompts into run-test mode", async () => {
    renderPromptDialog(
      "/dashboard/workbench/all?source=onboarding&action=create-prompt&quick_start_goal=improve_prompts&quick_start_id=prompt&quick_start_primary_path=prompt",
    );

    await userEvent.click(screen.getByText("Start from scratch"));

    await waitFor(() => {
      const [path, query] = screen
        .getByTestId("location")
        .textContent.split("?");
      const params = new URLSearchParams(query);

      expect(path).toBe("/dashboard/workbench/create/prompt-1");
      expect(params.get("source")).toBe("onboarding");
      expect(params.get("onboarding")).toBe("run-test");
      expect(params.get("tour_anchor")).toBe("prompt_run_test_button");
      expect(params.get("journey_step")).toBe("run_prompt_test");
      expect(params.get("quick_start_goal")).toBe("improve_prompts");
      expect(params.get("quick_start_id")).toBe("prompt");
      expect(params.get("quick_start_primary_path")).toBe("prompt");
    });
    expect(mockRecordActivationEvent).toHaveBeenCalledWith({
      eventName: "prompt_created",
      primaryPath: "prompt",
      stage: "start_prompt",
      source: "prompt_template",
      metadata: {
        step: "create-prompt",
        template_id: "prompt-1",
      },
      quickStartGoal: "improve_prompts",
      quickStartId: "prompt",
      quickStartPrimaryPath: "prompt",
      idempotencyKey: "prompt_onboarding:prompt_created:prompt-1",
    });
  });

  it("marks the manual prompt option as the onboarding destination action", () => {
    renderPromptDialog(
      "/dashboard/workbench/all?source=onboarding&action=create-prompt&tour_anchor=prompt_create_button&journey_step=start_prompt",
    );

    expect(screen.getByText("Create a prompt to test")).toBeVisible();
    expect(screen.getByTestId("prompt-create-setup-guide")).toHaveTextContent(
      "1. Create prompt",
    );
    expect(screen.getByTestId("prompt-create-setup-guide")).toHaveTextContent(
      "4. Compare version",
    );
    expect(screen.getByText("Recommended")).toBeVisible();
    expect(
      screen
        .getByText("Start from scratch")
        .closest('[data-tour-anchor="prompt_create_button"]'),
    ).toBeTruthy();
    expect(
      screen.getByText("Generate with AI").closest("[data-tour-anchor]"),
    ).toBeNull();
  });

  it("keeps non-onboarding prompt creation routes clean", async () => {
    renderPromptDialog("/dashboard/workbench/all");

    await userEvent.click(screen.getByText("Start from scratch"));

    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/dashboard/workbench/create/prompt-1",
      ),
    );
    expect(mockRecordActivationEvent).not.toHaveBeenCalled();
  });
});
