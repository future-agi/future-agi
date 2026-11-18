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

const navigate = vi.fn();
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => navigate,
}));

// The real ship hook over the fake API unless a test makes it fail.
const hooks = vi.hoisted(() => ({ ship: null }));
vi.mock("src/api/simulate-environments/fixRuns", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useShipCandidate: (fix) => hooks.ship ?? actual.useShipCandidate(fix),
  };
});

const axios = (await import("src/utils/axios")).default;
const { listResult } = await import("../fixRunView");
const { default: FixRunPage } = await import("../FixRunPage");
const { default: FixFinishedView, FinishedActions } = await import(
  "../FixFinishedView"
);
const { default: FixVerdict } = await import("../FixVerdict");
const { default: FixDecisionDrawer } = await import("../FixDecisionDrawer");

const fixOf = (id, extra = {}) => ({
  ...structuredClone(fixtureFixRun(id)),
  environment_id: "env-1",
  ...extra,
});

const wrap = (ui) => (
  <QueryClientProvider
    client={
      new QueryClient({
        defaultOptions: {
          queries: { retry: false },
          mutations: { retry: false },
        },
      })
    }
  >
    <SnackbarProvider>
      <MemoryRouter>{ui}</MemoryRouter>
    </SnackbarProvider>
  </QueryClientProvider>
);
const renderUi = (ui) => render(wrap(ui));
const renderPage = (fixId) =>
  renderUi(<FixRunPage envId="env-1" fixId={fixId} />);

const shipPosts = () =>
  axios.post.mock.calls.filter(([url]) =>
    /make-default|pull-request/.test(url),
  );

async function confirmShip(user, button) {
  await user.click(await screen.findByRole("button", { name: "Ship" }));
  const dialog = await screen.findByRole("dialog");
  return {
    dialog,
    confirm: within(dialog).getByRole("button", { name: button }),
  };
}

beforeEach(() => {
  resetFixRunFixtures();
  hooks.ship = null;
  navigate.mockReset();
  axios.post.mockClear();
});

describe("FixVerdict", () => {
  it("says it was proven on the targets only when nothing was held back", () => {
    renderUi(<FixVerdict fix={fixOf("fix-ready-no-held")} />);
    expect(screen.getByText("Proven on the targets only")).toBeInTheDocument();
    expect(screen.queryByText("Held back")).toBeNull();
    expect(screen.queryByText("Overfit gap")).toBeNull();
    expect(screen.queryByText("Limited validation")).toBeNull();
  });

  it("lists must-pass breaks, noting what was within noise", () => {
    renderUi(<FixVerdict fix={fixOf("fix-ready-archive")} />);
    const breaks = within(screen.getByTestId("must-pass-breaks"));
    expect(breaks.getByText("on-time-cancel")).toBeInTheDocument();
    expect(breaks.getByText(/refund-window/)).toBeInTheDocument();
    expect(
      breaks.getByText(/within normal noise, not counted/),
    ).toBeInTheDocument();
  });

  it("renders a verdict without tiers, here and in the decision drawer", () => {
    const fix = fixOf("fix-ready-archive");
    fix.candidates.forEach((c) => delete c.verdict.tiers);
    const { unmount } = renderUi(<FixVerdict fix={fix} />);
    expect(screen.getByText(/Held-back pass rate/)).toBeInTheDocument();
    expect(screen.queryByText(/^Checks ·/)).toBeNull();
    unmount();
    renderUi(<FixDecisionDrawer fix={fix} open onClose={vi.fn()} />);
    expect(screen.getByText("How it was decided")).toBeInTheDocument();
  });

  it("labels limited validation, with the held-back tally only when cases were held back", async () => {
    const { unmount } = renderUi(
      <FixVerdict
        fix={fixOf("fix-ready-archive", { limited_validation: true })}
      />,
    );
    expect(screen.getByText("Limited validation")).toBeInTheDocument();
    expect(
      await screen.findByText(
        "Too few to tell · Fixed 1 of 2 failing scenarios it never saw — it takes 4 or more to tell.",
      ),
    ).toBeInTheDocument();
    unmount();
    renderUi(
      <FixVerdict
        fix={fixOf("fix-ready-no-held", { limited_validation: true })}
      />,
    );
    expect(screen.getByText("Limited validation")).toBeInTheDocument();
    expect(screen.queryByText(/Too few to tell/)).toBeNull();
    expect(screen.queryByText("Proven on the targets only")).toBeNull();
    expect(
      listResult(fixOf("fix-ready-archive", { limited_validation: true })).note,
    ).toMatch(/^Limited validation · /);
  });

  it("warns with each platform eval's rate pair, and says nothing when the field is absent", () => {
    const { unmount } = renderUi(
      <FixVerdict
        fix={fixOf("fix-ready-archive", {
          eval_warnings: [
            {
              name: "Politeness",
              pass_rate: 0.6,
              base_pass_rate: 0.85,
              trials: 6,
              base_trials: 6,
            },
            {
              name: "Tone",
              pass_rate: 0.5,
              base_pass_rate: 0.75,
              trials: 4,
              base_trials: 8,
            },
          ],
        })}
      />,
    );
    expect(screen.getByText("Politeness")).toBeInTheDocument();
    expect(screen.getByText("60% vs 85% · 6 trials vs 6")).toBeInTheDocument();
    expect(screen.getByText("50% vs 75% · 4 trials vs 8")).toBeInTheDocument();
    unmount();
    renderUi(
      <FixVerdict
        fix={fixOf("fix-ready-archive", { eval_warnings: undefined })}
      />,
    );
    expect(screen.queryByText(/platform eval/)).toBeNull();
  });
});

describe("FixFinishedView", () => {
  it("leads a failed fix with why it failed and offers no Ship", () => {
    const fix = fixOf("fix-failed", {
      report: { ending: "failed", summary: "The fix did not finish." },
      failure: {
        code: "engine_exited",
        message: "The engine stopped: the model provider refused the key.",
      },
    });
    renderUi(
      <FixFinishedView
        fix={fix}
        tab="trials"
        onTab={vi.fn()}
        renderTab={() => null}
      />,
    );
    expect(
      screen.getByText(
        "The engine stopped: the model provider refused the key.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("The fix did not finish.")).toBeNull();
    expect(screen.queryByRole("button", { name: /^Ship$/ })).toBeNull();
  });

  it("offers Retry on a retryable failed fix and opens the new fix", async () => {
    const user = userEvent.setup();
    const { unmount } = renderUi(
      <FinishedActions
        fix={fixOf("fix-failed", { retryable: false })}
        onReview={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    unmount();
    renderUi(<FinishedActions fix={fixOf("fix-failed")} onReview={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(navigate).toHaveBeenCalled());
    expect(navigate.mock.calls[0][0]).toMatch(
      /^\/dashboard\/simulate\/environments\/env-1\/improvements\/fix-new-\d+$/,
    );
  });
});

describe("Ship", () => {
  it("asks before it acts: where a pull request goes and what it changes, or which version becomes the default", async () => {
    const user = userEvent.setup();
    const github = renderPage("fix-ready-github");
    let { dialog, confirm } = await confirmShip(user, "Open pull request");
    expect(within(dialog).getByText("main @ 0487e25")).toBeInTheDocument();
    expect(
      await within(dialog).findByText("instructions.md"),
    ).toBeInTheDocument();
    expect(within(dialog).getByText("+3 −1")).toBeInTheDocument();
    expect(within(dialog).queryByText("Repo")).toBeNull();
    expect(shipPosts()).toHaveLength(0);
    await user.click(confirm);
    expect(
      await screen.findByRole("link", { name: "View pull request" }),
    ).toHaveAttribute("href", "https://github.com/acme/dispatch-agent/pull/42");
    expect(shipPosts()).toHaveLength(1);
    github.unmount();

    resetFixRunFixtures();
    updateFixtureFixRun("fix-ready-github", (f) => ({
      ...f,
      agent: { ...f.agent, repository: "acme/dispatch-agent" },
    }));
    const repo = renderPage("fix-ready-github");
    ({ dialog } = await confirmShip(user, "Open pull request"));
    expect(within(dialog).getByText("acme/dispatch-agent")).toBeInTheDocument();
    expect(within(dialog).getByText("Repo")).toBeInTheDocument();
    repo.unmount();

    axios.post.mockClear();
    renderPage("fix-ready-archive");
    ({ dialog, confirm } = await confirmShip(user, "Make default"));
    expect(
      within(dialog).getByText("Make Trial 3's version the default?"),
    ).toBeInTheDocument();
    expect(shipPosts()).toHaveLength(0);
    await user.click(confirm);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Default" })).toBeDisabled(),
    );
    expect(shipPosts()[0][0]).toMatch(/make-default\/$/);
  });

  it("shows the server's message when the version cannot be activated", async () => {
    hooks.ship = {
      isPending: false,
      mutateAsync: vi.fn().mockRejectedValue({
        statusCode: 409,
        error: "fix_candidate_not_activated",
        message: "Could not activate v11",
      }),
    };
    const user = userEvent.setup();
    renderPage("fix-ready-archive");
    await user.click((await confirmShip(user, "Make default")).confirm);
    expect(
      await screen.findByText("Could not activate v11"),
    ).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Ship" })).toBeEnabled();
  });

  it("offers the patch when the repository cannot take a pull request", async () => {
    hooks.ship = {
      isPending: false,
      mutateAsync: vi.fn().mockRejectedValue({
        statusCode: 422,
        error: "fix_github_not_connected",
        message: "Not connected through the GitHub App",
      }),
    };
    const user = userEvent.setup();
    renderPage("fix-ready-github");
    await user.click((await confirmShip(user, "Open pull request")).confirm);
    expect(
      await screen.findByRole("button", { name: "Download patch" }),
    ).toBeInTheDocument();
  });
});
