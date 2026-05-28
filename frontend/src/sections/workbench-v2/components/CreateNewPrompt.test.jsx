import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import CreateNewPrompt from "./CreateNewPrompt";

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
      "/dashboard/workbench/all?source=onboarding&action=create-prompt",
    );

    await userEvent.click(screen.getByText("Start from scratch"));

    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/dashboard/workbench/create/prompt-1?source=onboarding&onboarding=run-test",
      ),
    );
  });

  it("keeps non-onboarding prompt creation routes clean", async () => {
    renderPromptDialog("/dashboard/workbench/all");

    await userEvent.click(screen.getByText("Start from scratch"));

    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/dashboard/workbench/create/prompt-1",
      ),
    );
  });
});
