import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import CssBaseline from "@mui/material/CssBaseline";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { palette } from "src/theme/palette";
import {
  resetEnvironmentsStore,
  useEnvironmentsStore,
} from "../store/useEnvironmentsStore";

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

vi.mock("src/api/simulate-environments/harnessEnvironments", () => ({
  listHarnessEnvironments: vi.fn(),
  deleteHarnessEnvironment: vi.fn(),
}));

const { listHarnessEnvironments } = await import(
  "src/api/simulate-environments/harnessEnvironments"
);
const { default: EnvironmentsHome } = await import("../EnvironmentsHome");

// The My Environments tab reads from the harness-environments list; one row
// whose name is a table-only value proves the list rendered.
const HARNESS_ENVS = {
  count: 1,
  next: null,
  previous: null,
  total_pages: 1,
  current_page: 1,
  results: [
    {
      id: "job-billing",
      name: "Billing Chat Agent",
      description: null,
      source_kind: "github",
      agent_type: "chat",
      status: "running",
      stage: "running",
      scenario_count: 0,
      tools_count: null,
      last_updated: "2026-09-15T11:00:00Z",
      created_at: "2026-09-14T09:00:00Z",
    },
  ],
};

const theme = createTheme({
  palette: palette("light"),
  spacing: (factor) => `${0.25 * factor}rem`,
});

let lastSearch = "";
function LocationProbe() {
  lastSearch = useLocation().search;
  return null;
}

const renderHome = (route) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <MemoryRouter initialEntries={[route]}>
      <QueryClientProvider client={client}>
        <ThemeProvider theme={theme}>
          <CssBaseline />
          <EnvironmentsHome />
          <LocationProbe />
        </ThemeProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  );
};

describe("EnvironmentsHome", () => {
  beforeAll(() => {
    Element.prototype.scrollIntoView = vi.fn();
  });

  beforeEach(() => {
    resetEnvironmentsStore();
    lastSearch = "";
    listHarnessEnvironments.mockReset();
    listHarnessEnvironments.mockResolvedValue(HARNESS_ENVS);
  });

  it("defaults to the Build environment tab with the matrix", () => {
    renderHome("/dashboard/simulate/environments");

    expect(
      screen.getByRole("tab", { name: "Build environment" }),
    ).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Source repository")).toBeInTheDocument();
    // A table-only harness-job name proves the My Environments list is absent.
    expect(screen.queryByText("Billing Chat Agent")).toBeNull();
  });

  it("shows the build matrix when ?tab=build", () => {
    renderHome("/dashboard/simulate/environments?tab=build");

    expect(
      screen.getByRole("tab", { name: "Build environment" }),
    ).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Source repository")).toBeInTheDocument();
    // 2 hero + 5 option cards; tabs are role="tab", not button.
    expect(screen.getAllByRole("button")).toHaveLength(7);
    // "Customer Support Line" doubles as a Prebuilt hero chip, so assert a
    // table-only harness-job name to prove the My Environments list is absent.
    expect(screen.queryByText("Billing Chat Agent")).toBeNull();
  });

  it("switches tabs and writes the choice to the url", async () => {
    const user = userEvent.setup();
    renderHome("/dashboard/simulate/environments");

    await user.click(screen.getByRole("tab", { name: "My Environments" }));
    expect(lastSearch).toContain("tab=my-environments");
    expect(await screen.findByText("Billing Chat Agent")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Build environment" }));
    expect(lastSearch).toContain("tab=build");
    expect(screen.getByText("Source repository")).toBeInTheDocument();
  });

  it("clears the draft on mount so the matrix starts clean", () => {
    useEnvironmentsStore.getState().setDraft({ kind: "repo" });
    renderHome("/dashboard/simulate/environments");
    expect(useEnvironmentsStore.getState().draft).toBeNull();
  });

  it("keeps the draft on unmount (it must survive the hop to /build)", () => {
    const { unmount } = renderHome("/dashboard/simulate/environments");
    useEnvironmentsStore.getState().setDraft({ kind: "repo" });
    unmount();
    expect(useEnvironmentsStore.getState().draft).toEqual({ kind: "repo" });
  });

  it("keeps adopted workspace envs on mount (the reset only clears the entry slice)", () => {
    useEnvironmentsStore
      .getState()
      .adoptEnvironment({ id: "env-a", name: "A" }, "2026-09-17T00:00:00Z");
    renderHome("/dashboard/simulate/environments");
    expect(
      useEnvironmentsStore.getState().workspaceEnvs["env-a"],
    ).toBeDefined();
  });

  it("renders the header and no scratch button", () => {
    renderHome("/dashboard/simulate/environments?tab=build");

    expect(screen.getByText("Environments")).toBeInTheDocument();
    expect(
      screen.getByText(
        "An environment is the world your agent runs in — seeded state, tools, and rules. Pick how you want to bring your agent in and we take care of the rest.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("Start from scratch")).toBeNull();
    expect(screen.queryByLabelText("Back to environments")).toBeNull();
  });
});
