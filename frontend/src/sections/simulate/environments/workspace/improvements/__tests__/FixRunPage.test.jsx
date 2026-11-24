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
  fixturePatch,
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

// jsdom cannot save a file or reach the clipboard: record what would be.
const saved = vi.hoisted(() => ({
  download: vi.fn(),
  copy: vi.fn(async () => true),
}));
vi.mock("../downloadText", () => ({ downloadText: saved.download }));
vi.mock("src/utils/copyToClipboard", () => ({ copyToClipboard: saved.copy }));

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
    saved.download.mockClear();
    saved.copy.mockClear();
  });

  async function openMore(user, fixId) {
    renderPage(fixId);
    await user.click(
      await screen.findByRole("button", { name: "More actions" }),
    );
    return screen.findByRole("menu");
  }

  it("copies the epoch's ID from the ⋯ menu without printing it", async () => {
    const user = userEvent.setup();
    const menu = await openMore(user, "fix-ready-archive");
    expect(screen.queryByText("fix-ready-archive")).toBeNull();
    await user.click(
      within(menu).getByRole("menuitem", { name: /Copy epoch ID/ }),
    );
    expect(saved.copy).toHaveBeenCalledWith("fix-ready-archive");
    expect(await screen.findByText("Epoch ID copied")).toBeInTheDocument();
  });

  it("exports the epoch's own read as a JSON file", async () => {
    const user = userEvent.setup();
    const menu = await openMore(user, "fix-ready-archive");
    await user.click(
      within(menu).getByRole("menuitem", { name: /Export epoch/ }),
    );
    expect(saved.download).toHaveBeenCalledTimes(1);
    const [name, text, type] = saved.download.mock.calls[0];
    expect(name).toBe("epoch-10.json");
    expect(type).toBe("application/json");
    expect(JSON.parse(text)).toMatchObject({
      id: "fix-ready-archive",
      number: 10,
    });
  });

  it("downloads the winner's patch from the ⋯ menu", async () => {
    const user = userEvent.setup();
    const menu = await openMore(user, "fix-ready-archive");
    await user.click(
      within(menu).getByRole("menuitem", { name: /Download patch/ }),
    );
    await waitFor(() =>
      expect(saved.download).toHaveBeenCalledWith(
        "fix-fix-ready-archive-attempt-3.patch",
        fixturePatch("fix-ready-archive-c3"),
      ),
    );
  });

  it("disables Download patch on an epoch with no winner", async () => {
    const user = userEvent.setup();
    const menu = await openMore(user, "fix-no-improvement");
    expect(
      within(menu).getByRole("menuitem", { name: /Download patch/ }),
    ).toHaveAttribute("aria-disabled", "true");
  });

  it("heads a running fix with its name, state and stage", async () => {
    renderPage("fix-search");
    expect(await screen.findByText("Epoch 13")).toBeInTheDocument();
    expect(screen.getByText("Running")).toBeInTheDocument();
    // The stage in the design's words, never the engine's own stage name.
    expect(screen.getByTestId("epoch-stage")).toHaveTextContent("Proposing");
    expect(screen.queryByText("Searching")).toBeNull();
    expect(screen.queryByText("Screen · attempt 2")).toBeNull();
  });

  it("says a search with no diagnosis yet is diagnosing, and shows the causes once it has them", async () => {
    const fix = fixOf("fix-unknown-identity");
    hooks.fixRun = { fix, isLoading: false, error: null };
    const { unmount } = renderPage("fix-unknown-identity");
    expect(screen.getByTestId("epoch-stage")).toHaveTextContent("Diagnosing");
    expect(screen.getByTestId("step-diagnose")).toHaveAttribute(
      "data-state",
      "now",
    );
    unmount();

    hooks.fixRun = {
      fix: {
        ...fix,
        diagnosis: { causes: ["the instructions never ask it to escalate"] },
      },
      isLoading: false,
      error: null,
    };
    renderPage("fix-unknown-identity");
    expect(screen.getByTestId("epoch-stage")).toHaveTextContent("Proposing");
    expect(screen.getByTestId("step-diagnose")).toHaveTextContent(
      "Starts from its diagnosis: The instructions never ask it to escalate.",
    );
  });

  it("shows no stage beside Queued", async () => {
    renderPage("fix-queued");
    expect(await screen.findByText("Epoch 15")).toBeInTheDocument();
    expect(screen.queryByTestId("epoch-stage")).toBeNull();
    expect(
      within(screen.getByTestId("step-reproduce")).getByText("Queued"),
    ).toBeInTheDocument();
  });

  it("goes back to the Epochs tab", async () => {
    renderPage("fix-search");
    expect(
      await screen.findByRole("link", { name: "Back to epochs" }),
    ).toHaveAttribute(
      "href",
      "/dashboard/simulate/environments/env-1?tab=improvements",
    );
  });

  it("starts the breadcrumb at the Error Feed issue it was run from", async () => {
    const fix = fixOf("fix-search");
    fix.issue = {
      job_id: "job-1",
      cluster_id: "S-1A2B3C4D",
      project_id: "proj-1",
    };
    hooks.fixRun = { fix, isLoading: false, error: null };
    renderPage("fix-search");
    expect(screen.getByTestId("epoch-issue-crumb")).toHaveAttribute(
      "href",
      "/dashboard/error-feed/S-1A2B3C4D",
    );
    expect(screen.getByTestId("epoch-issue-crumb")).toHaveTextContent(
      "S-1A2B3C4D",
    );
    expect(
      screen.getByRole("link", { name: "Back to S-1A2B3C4D" }),
    ).toHaveAttribute("href", "/dashboard/error-feed/S-1A2B3C4D");
    expect(screen.queryByRole("link", { name: "Back to epochs" })).toBeNull();
  });

  it("offers no Stop and no stage on a finished fix", async () => {
    renderPage("fix-ready-archive");
    expect(await screen.findByText("Epoch 10")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
    expect(screen.queryByText(/attempt/)).toBeNull();
  });

  it("stops a running fix after confirming", async () => {
    const user = userEvent.setup();
    renderPage("fix-search");
    const dialog = await openStop(user);
    expect(
      within(dialog).getByText(
        "2 of up to 4 candidates have run · 412 credits spent so far.",
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
      within(dialog).getByText(/The current version stays/),
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
    expect(screen.getByText("Epoch 13")).toBeInTheDocument();
    expect(screen.queryByText("This epoch could not be found")).toBeNull();
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
