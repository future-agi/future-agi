import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import SaveAndCommit from "./SaveAndCommit";
import { enqueueSnackbar } from "notistack";

const mockOnClose = vi.fn();
const mockOnCommitted = vi.fn();

vi.mock("src/utils/axios", () => ({
  default: {
    post: vi.fn(),
  },
  endpoints: {
    develop: {
      runPrompt: {
        commitSavePrompt: (id) => `/model-hub/prompt-template/${id}/commit/`,
      },
    },
  },
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/utils/Mixpanel", () => ({
  Events: {
    promptCommitClicked: "promptCommitClicked",
  },
  PropertyName: {
    promptId: "promptId",
  },
  trackEvent: vi.fn(),
}));

import axios from "src/utils/axios";

const theme = createTheme();

const defaultVersionData = {
  isDefault: false,
  isDraft: true,
  version: "v1",
};

const renderDialog = ({ data = defaultVersionData } = {}) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });

  return render(
    <ThemeProvider theme={theme}>
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/dashboard/workbench/create/prompt-1"]}>
          <Routes>
            <Route
              path="/dashboard/workbench/create/:id"
              element={
                <SaveAndCommit
                  open
                  onClose={mockOnClose}
                  onCommitted={mockOnCommitted}
                  data={data}
                  promptName="Support prompt"
                />
              }
            />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );
};

describe("SaveAndCommit", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    axios.post.mockResolvedValue({ data: { status: true } });
  });

  it("calls the commit callback after a successful save", async () => {
    renderDialog();

    await userEvent.type(
      screen.getByPlaceholderText("Enter a commit message for this version..."),
      "Baseline version",
    );
    await userEvent.click(screen.getByRole("button", { name: /^commit$/i }));

    await waitFor(() => expect(mockOnCommitted).toHaveBeenCalledTimes(1));
    expect(mockOnClose).toHaveBeenCalledTimes(1);
    expect(axios.post).toHaveBeenCalledWith(
      "/model-hub/prompt-template/prompt-1/commit/",
      {
        is_draft: true,
        message: "Baseline version",
        set_default: false,
        version_name: "v1",
      },
    );
  });

  it("does not commit when no draft version target is available", async () => {
    renderDialog({ data: null });

    expect(
      screen.getByText("Create or select a draft version before saving."),
    ).toBeVisible();

    await userEvent.type(
      screen.getByPlaceholderText("Enter a commit message for this version..."),
      "Baseline version",
    );

    expect(screen.getByRole("button", { name: /^commit$/i })).toBeDisabled();
    expect(
      screen.getByRole("button", {
        name: /commit and set as a default version/i,
      }),
    ).toBeDisabled();

    expect(axios.post).not.toHaveBeenCalled();
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });
});
