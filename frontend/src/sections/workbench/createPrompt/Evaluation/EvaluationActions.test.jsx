import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import EvaluationActions from "./EvaluationActions";
import { WorkbenchEvaluationContext } from "./context/WorkbenchEvaluationContext";

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));

vi.mock("src/components/snackbar", () => ({
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/components/svg-color", () => ({
  default: () => <span data-testid="svg-color" />,
}));

vi.mock("src/components/Switch/SwitchComponent", () => ({
  default: ({ checked, label, onChange }) => (
    <label>
      {label}
      <input checked={checked} onChange={onChange} type="checkbox" />
    </label>
  ),
}));

vi.mock("./AddEvalsComparison", () => ({
  default: () => <div data-testid="add-evals-comparison" />,
}));

vi.mock("src/sections/common/EvaluationDrawer/EvaluationDrawer", () => ({
  default: ({ onSuccess, open }) => (
    <div data-open={open ? "true" : "false"} data-testid="evaluation-drawer">
      <button onClick={() => onSuccess?.()} type="button">
        simulate evaluation added
      </button>
    </div>
  ),
}));

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

const contextValue = {
  compareOpen: false,
  isEvalsCompareOpen: false,
  isEvaluationDrawerOpen: false,
  setCompareOpen: vi.fn(),
  setIsEvalsCompareOpen: vi.fn(),
  setIsEvaluationDrawerOpen: vi.fn(),
  setShowPrompts: vi.fn(),
  setShowVariables: vi.fn(),
  setVariables: vi.fn(),
  setVersions: vi.fn(),
  showPrompts: false,
  showVariables: true,
  variables: {},
  versions: ["v1", "v2"],
};

const renderActions = (route) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  const wrapperContext = {
    ...contextValue,
    setIsEvaluationDrawerOpen: vi.fn(),
  };

  return {
    ...render(
      <ThemeProvider theme={theme}>
        <QueryClientProvider client={queryClient}>
          <WorkbenchEvaluationContext.Provider value={wrapperContext}>
            <MemoryRouter initialEntries={[route]}>
              <LocationProbe />
              <Routes>
                <Route
                  element={<EvaluationActions />}
                  path="/dashboard/workbench/create/:id"
                />
              </Routes>
            </MemoryRouter>
          </WorkbenchEvaluationContext.Provider>
        </QueryClientProvider>
      </ThemeProvider>,
    ),
    wrapperContext,
  };
};

describe("EvaluationActions prompt onboarding", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows the prompt failure capture panel on guided add-failure routes", async () => {
    const { wrapperContext } = renderActions(
      "/dashboard/workbench/create/prompt-1?source=onboarding&onboarding=add-failure",
    );

    expect(screen.getByTestId("prompt-failure-capture-focus")).toBeVisible();
    expect(screen.getByText("Prompt setup")).toBeVisible();
    expect(screen.getByText("Step 6 of 6")).toBeVisible();

    await userEvent.click(
      screen.getByRole("button", { name: /^add evaluation$/i }),
    );

    expect(wrapperContext.setIsEvaluationDrawerOpen).toHaveBeenCalledWith(true);
  });

  it("shows the prompt failure capture panel from journey-step routes", () => {
    renderActions(
      "/dashboard/workbench/create/prompt-1?tour_anchor=prompt_add_example_button&journey_step=prompt_next_loop",
    );

    expect(screen.getByTestId("prompt-failure-capture-focus")).toBeVisible();
  });

  it("moves guided add-failure routes to metrics after an evaluation is added", async () => {
    renderActions(
      "/dashboard/workbench/create/prompt-1?source=onboarding&onboarding=add-failure&quick_start_goal=improve_prompts&quick_start_id=prompt&quick_start_primary_path=prompt",
    );

    await userEvent.click(screen.getByText("simulate evaluation added"));

    await waitFor(() => {
      const [path, query] = screen
        .getByTestId("location")
        .textContent.split("?");
      const params = new URLSearchParams(query);

      expect(path).toBe("/dashboard/workbench/create/prompt-1");
      expect(params.get("source")).toBe("onboarding");
      expect(params.get("onboarding")).toBe("metrics");
      expect(params.get("tab")).toBe("Metrics");
      expect(params.get("quick_start_goal")).toBe("improve_prompts");
      expect(params.get("quick_start_id")).toBe("prompt");
      expect(params.get("quick_start_primary_path")).toBe("prompt");
    });
  });

  it("keeps compared prompt versions when moving add-failure routes to metrics", async () => {
    const selectedVersions = [
      { version: "v1", templateVersion: "v1", isDraft: false },
      { version: "v2", templateVersion: "v2", isDraft: false },
    ];
    const params = new URLSearchParams({
      source: "onboarding",
      onboarding: "add-failure",
      "selected-versions": JSON.stringify(selectedVersions),
    });

    renderActions(`/dashboard/workbench/create/prompt-1?${params.toString()}`);

    await userEvent.click(screen.getByText("simulate evaluation added"));

    await waitFor(() => {
      const [path, query] = screen
        .getByTestId("location")
        .textContent.split("?");
      const nextParams = new URLSearchParams(query);

      expect(path).toBe("/dashboard/workbench/create/prompt-1");
      expect(nextParams.get("onboarding")).toBe("metrics");
      expect(nextParams.get("tab")).toBe("Metrics");
      expect(JSON.parse(nextParams.get("selected-versions"))).toEqual(
        selectedVersions,
      );
    });
  });

  it("does not show the failure capture panel outside guided routes", () => {
    renderActions("/dashboard/workbench/create/prompt-1");

    expect(screen.queryByTestId("prompt-failure-capture-focus")).toBeNull();
  });
});
