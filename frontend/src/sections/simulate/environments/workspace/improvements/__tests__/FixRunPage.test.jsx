import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  render as rtlRender,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";

import {
  fixtureFixRun,
  resetFixRunFixtures,
  updateFixtureFixRun,
} from "src/api/simulate-environments/__tests__/fixtures/fixRuns";

// The trials table reads theme.palette.data, so render with the app theme.
const appTheme = createTheme({ palette: palette("light") });
// eslint-disable-next-line react/prop-types
const AppTheme = ({ children }) => (
  <ThemeProvider theme={appTheme}>{children}</ThemeProvider>
);
const render = (ui, options) =>
  rtlRender(ui, { wrapper: AppTheme, ...options });

vi.mock("src/utils/axios", async (importOriginal) => {
  const { withFakeFixRunsApi } = await import(
    "src/api/simulate-environments/__tests__/fixtures/fixRunsApi"
  );
  return withFakeFixRunsApi(await importOriginal());
});

// The real hooks over the fake API unless a test pins what they return.
const hooks = vi.hoisted(() => ({ fixRun: null, stop: null }));
vi.mock("src/api/simulate-environments/fixRuns", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useFixRun: (id) => hooks.fixRun ?? actual.useFixRun(id),
    useStopFixRun: (id) => hooks.stop ?? actual.useStopFixRun(id),
  };
});

const { default: FixRunPage } = await import("../FixRunPage");

const fixOf = (id) => ({
  ...structuredClone(fixtureFixRun(id)),
  environment_id: "env-1",
});

function renderPage(fixId) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const utils = render(
    <QueryClientProvider client={client}>
      <SnackbarProvider>
        <MemoryRouter>
          <FixRunPage envId="env-1" fixId={fixId} />
        </MemoryRouter>
      </SnackbarProvider>
    </QueryClientProvider>,
  );
  return { ...utils, client };
}

async function openStop(user) {
  await user.click(await screen.findByRole("button", { name: "Stop" }));
  return screen.findByRole("dialog");
}

describe("FixRunPage", () => {
  beforeEach(() => {
    resetFixRunFixtures();
    hooks.fixRun = null;
    hooks.stop = null;
  });

  it("heads a running fix with its name, state and stage", async () => {
    renderPage("fix-search");
    expect(await screen.findByText("Improvement 13")).toBeInTheDocument();
    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(screen.getByText("Screen · attempt 2")).toBeInTheDocument();
  });

  it("goes back to the Improvements tab", async () => {
    renderPage("fix-search");
    expect(
      await screen.findByRole("link", { name: "Back to improvements" }),
    ).toHaveAttribute(
      "href",
      "/dashboard/simulate/environments/env-1?tab=improvements",
    );
  });

  it("offers no Stop and no stage on a finished fix", async () => {
    renderPage("fix-ready-archive");
    expect(await screen.findByText("Improvement 10")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
    expect(screen.queryByText(/attempt/)).toBeNull();
  });

  it("stops a running fix after confirming", async () => {
    const user = userEvent.setup();
    renderPage("fix-search");
    const dialog = await openStop(user);
    expect(
      within(dialog).getByText(
        "2 of up to 4 trials have run · 412 credits spent so far.",
      ),
    ).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Stop" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Stopping" })).toBeDisabled(),
    );
  });

  it("does not say no trial was checked on held-back once one was", async () => {
    const fix = fixOf("fix-search");
    fix.candidates[1].outcome = "finalist";
    fix.candidates[1].verdict.held_back_pass_rate = [0.17, 0.83];
    hooks.fixRun = { fix, isLoading: false, error: null };
    renderPage("fix-search");
    const dialog = await openStop(userEvent.setup());
    expect(
      within(dialog).queryByText(/None of them has been checked/),
    ).toBeNull();
    expect(
      within(dialog).getByText(/Today's version stays/),
    ).toBeInTheDocument();
  });

  it("shows the server's message when it refuses the Stop", async () => {
    hooks.stop = {
      isPending: false,
      mutate: (_v, { onError } = {}) =>
        onError?.({
          statusCode: 409,
          error: "fix_run_active",
          message: "The fix is already stopping",
        }),
    };
    hooks.fixRun = { fix: fixOf("fix-search"), isLoading: false, error: null };
    const user = userEvent.setup();
    renderPage("fix-search");
    const dialog = await openStop(user);
    await user.click(within(dialog).getByRole("button", { name: "Stop" }));
    expect(
      await screen.findByText("The fix is already stopping"),
    ).toBeInTheDocument();
  });

  it("keeps showing the fix it has when a poll fails", () => {
    hooks.fixRun = {
      fix: fixOf("fix-search"),
      isLoading: false,
      error: new Error("network"),
    };
    renderPage("fix-search");
    expect(screen.getByText("Improvement 13")).toBeInTheDocument();
    expect(
      screen.queryByText("This improvement could not be found"),
    ).toBeNull();
  });

  it("switches to the finished view when a running fix ends", async () => {
    const { client } = renderPage("fix-verify");
    expect(await screen.findByTestId("fix-running-view")).toBeInTheDocument();
    updateFixtureFixRun("fix-verify", (f) => ({
      ...f,
      state: "ready",
      step: "done",
      candidates: f.candidates.map((c) =>
        c.outcome === "pending" ? { ...c, outcome: "winner" } : c,
      ),
    }));
    await client.refetchQueries();
    expect(await screen.findByTestId("fix-finished-view")).toBeInTheDocument();
    expect(screen.queryByTestId("fix-running-view")).toBeNull();
  });
});
