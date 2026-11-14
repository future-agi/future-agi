import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { usePromptWorkbenchContext } from "../WorkbenchContext";
import WorkbenchProvider from "../WorkbenchProvider";

// The provider owns the tab every workbench view reads, so it is where an old
// ?tab=Simulation link becomes Playground for an org restricted to Environments.
const authState = { user: {} };

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => authState,
}));
vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(() => new Promise(() => {})),
    post: vi.fn(() => new Promise(() => {})),
  },
  endpoints: new Proxy(
    {},
    { get: () => new Proxy(() => "/x/", { get: () => () => "/x/" }) },
  ),
}));
vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));
vi.mock("src/sections/workbench/createPrompt/hooks/usePromptStreamUrl", () => ({
  usePromptStreamUrl: () => null,
}));

function CurrentTab() {
  return <p data-testid="tab">{usePromptWorkbenchContext().currentTab}</p>;
}

function renderAt(url) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route
            path="/dashboard/workbench/create/:id"
            element={
              <WorkbenchProvider>
                <CurrentTab />
              </WorkbenchProvider>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const SIMULATION_LINK = "/dashboard/workbench/create/prompt-1?tab=Simulation";

describe("WorkbenchProvider tab for an old Simulation link", () => {
  beforeEach(() => {
    authState.user = {};
  });

  it("opens Playground for an org restricted to Environments", () => {
    authState.user = { restrict_simulate_to_environments: true };
    renderAt(SIMULATION_LINK);
    expect(screen.getByTestId("tab")).toHaveTextContent("Playground");
  });

  it("keeps Simulation for other orgs", () => {
    authState.user = { restrict_simulate_to_environments: false };
    renderAt(SIMULATION_LINK);
    expect(screen.getByTestId("tab")).toHaveTextContent("Simulation");
  });
});
