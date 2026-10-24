import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
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

// The persona list has its own tests; a stub proves the tab mounts it and
// shows the toolbar actions the tab hands it.
vi.mock("src/sections/persona/PersonaListView", () => ({
  // eslint-disable-next-line react/prop-types
  default: ({ toolbarActions }) => (
    <div>
      persona-list-stub
      <div data-testid="persona-toolbar-actions">{toolbarActions}</div>
    </div>
  ),
}));

vi.mock("src/api/simulate-environments/harnessEnvironments", () => ({
  listHarnessEnvironments: vi.fn(),
  deleteHarnessEnvironment: vi.fn(),
}));

vi.mock("src/utils/Mixpanel", async (importOriginal) => ({
  ...(await importOriginal()),
  trackEvent: vi.fn(),
}));

const { listHarnessEnvironments } = await import(
  "src/api/simulate-environments/harnessEnvironments"
);
const { Events, PropertyName, trackEvent } = await import("src/utils/Mixpanel");
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
    trackEvent.mockClear();
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

    await user.click(screen.getByRole("tab", { name: "Personas" }));
    expect(lastSearch).toContain("tab=personas");
    expect(screen.getByText("persona-list-stub")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Build environment" }));
    expect(lastSearch).toContain("tab=build");
    expect(screen.getByText("Source repository")).toBeInTheDocument();
  });

  it("renders the three tabs in order", () => {
    renderHome("/dashboard/simulate/environments");

    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "Build environment",
      "My Environments",
      "Personas",
    ]);
  });

  it("tracks a Personas tab click with the personas navigation event", async () => {
    const user = userEvent.setup();
    renderHome("/dashboard/simulate/environments");

    await user.click(screen.getByRole("tab", { name: "My Environments" }));
    expect(trackEvent).not.toHaveBeenCalled();

    await user.click(screen.getByRole("tab", { name: "Personas" }));
    expect(trackEvent).toHaveBeenCalledTimes(1);
    expect(trackEvent).toHaveBeenCalledWith(Events.navigationPersonasClicked, {
      [PropertyName.click]: true,
    });
  });

  it("shows the persona list when ?tab=personas", () => {
    renderHome("/dashboard/simulate/environments?tab=personas");

    expect(screen.getByRole("tab", { name: "Personas" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByText("persona-list-stub")).toBeInTheDocument();
    expect(screen.queryByText("Source repository")).toBeNull();
    expect(screen.queryByText("Billing Chat Agent")).toBeNull();
  });

  it("puts the persona docs link in the persona list toolbar", () => {
    renderHome("/dashboard/simulate/environments?tab=personas");

    const link = within(
      screen.getByTestId("persona-toolbar-actions"),
    ).getByRole("link", { name: "View Docs" });
    expect(link).toHaveAttribute(
      "href",
      "https://docs.futureagi.com/docs/simulation/concepts/personas",
    );
    // Only one, so the tab rail no longer carries its own.
    expect(screen.getAllByRole("link", { name: "View Docs" })).toHaveLength(1);
  });

  it.each(["build", "my-environments"])(
    "has no docs link on the %s tab",
    (tab) => {
      renderHome(`/dashboard/simulate/environments?tab=${tab}`);
      expect(screen.queryByRole("link", { name: "View Docs" })).toBeNull();
    },
  );

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
        "An environment is the world your agent runs in: seeded state, tools, and rules. Pick how you want to bring your agent in and we take care of the rest.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("Start from scratch")).toBeNull();
    expect(screen.queryByLabelText("Back to environments")).toBeNull();
  });
});
