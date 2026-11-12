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
// The fake server's read, restored before each test.
const realGet = axios.get.getMockImplementation();
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
  axios.get.mockImplementation(realGet);
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

  // W10-AGENT-FIX-U05
  it("leads a fix the engine stopped with what to do next, and keeps the engine's text under Details", async () => {
    const user = userEvent.setup();
    const raw =
      "the improvement engine stopped 3 times before it finished; last error: KeyError: 'choices' in provider response";
    renderUi(
      <FixFinishedView
        fix={fixOf("fix-failed", {
          failure: { code: "fix_engine_died", message: raw },
        })}
        tab="trials"
        onTab={vi.fn()}
        renderTab={() => null}
      />,
    );
    expect(
      screen.getByText(
        "The improvement stopped unexpectedly. Retry it, or contact support with improvement ID fix-failed.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/last error/)).toBeNull();
    const details = screen.getByRole("button", { name: "Details" });
    expect(details).toHaveAttribute("aria-expanded", "false");
    await user.click(details);
    expect(details).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(raw)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Copy to clipboard" }),
    ).toBeInTheDocument();
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

  // W10-AGENT-FIX-U05: this test used to expect the server's raw sentence,
  // which ends in an activation outcome code.
  it("says what to do, not the raw outcome code, when the version cannot become the default", async () => {
    const raw = "the version could not become the default: environment_busy";
    hooks.ship = {
      isPending: false,
      mutateAsync: vi.fn().mockRejectedValue({
        statusCode: 409,
        error: "fix_candidate_not_activated",
        message: raw,
      }),
    };
    const user = userEvent.setup();
    renderPage("fix-ready-archive");
    await user.click((await confirmShip(user, "Make default")).confirm);
    expect(
      await screen.findByText(
        "This version could not become the default. If the environment is updating, ship again once it finishes.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(raw)).toBeNull();
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

  it("holds Download patch while it reads the patch, and says when the read fails", async () => {
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
    const download = await screen.findByRole("button", {
      name: "Download patch",
    });

    let failRead;
    const pendingRead = new Promise((_, reject) => {
      failRead = reject;
    });
    axios.get.mockImplementation((url, config) =>
      /\/patch\/$/.test(url) ? pendingRead : realGet(url, config),
    );
    await user.click(download);
    await waitFor(() => expect(download).toBeDisabled());

    failRead({ statusCode: 500, detail: "Server error" });
    expect(
      await screen.findByText("Could not download the patch. Try again."),
    ).toBeInTheDocument();
    await waitFor(() => expect(download).toBeEnabled());
  });

  // W10-AGENT-FIX-U05, G3-UX-1: errors the server does not mark retryable.
  it.each([
    [
      "fix_github_patch_failed",
      409,
      "error: patch failed: instructions.md:3 error: instructions.md: patch does not apply",
      "This change could not be applied to the repository. Download the patch to apply it by hand.",
    ],
    [
      "fix_github_branch_changed",
      409,
      "the branch futureagi/fix-1a2b3c4d-3 changed on GitHub since the pull request was made",
      "The branch for this pull request changed on GitHub. Download the patch to apply it by hand.",
    ],
    [
      "fix_github_pull_request_failed",
      502,
      'GitHub refused the pull request: {"message":"Validation Failed","errors":[{"resource":"PullRequest","code":"custom"}]}',
      "GitHub could not open the pull request. Download the patch to apply it by hand.",
    ],
  ])(
    "says what to do instead of git's or GitHub's text, and offers the patch in place of Ship, on %s",
    async (code, statusCode, raw, copy) => {
      hooks.ship = {
        isPending: false,
        mutateAsync: vi.fn().mockRejectedValue({
          statusCode,
          error: code,
          message: raw,
          retryable: false,
        }),
      };
      const user = userEvent.setup();
      renderPage("fix-ready-github");
      await user.click((await confirmShip(user, "Open pull request")).confirm);
      expect(await screen.findByText(copy)).toBeInTheDocument();
      expect(screen.queryByText(raw)).toBeNull();
      expect(
        await screen.findByRole("button", { name: "Download patch" }),
      ).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Ship" })).toBeNull();
    },
  );

  // G3-UX-1: a failed push, or a GitHub 5xx, is retryable on the server, and
  // sending it again pushes the same commit, so Ship stays.
  it.each([
    [
      "fix_github_push_failed",
      "fatal: unable to access 'https://github.com/acme/dispatch-agent.git/': The requested URL returned error: 500",
      "This change could not be pushed to GitHub. Try again, or download the patch to apply it by hand.",
    ],
    [
      "fix_github_pull_request_failed",
      'GitHub refused the pull request: {"message":"Server Error"}',
      "GitHub could not open the pull request. Try again, or download the patch to apply it by hand.",
    ],
  ])(
    "keeps Ship, with the patch beside it, when the server marks %s retryable",
    async (code, raw, copy) => {
      const mutateAsync = vi
        .fn()
        .mockRejectedValueOnce({
          statusCode: 502,
          error: code,
          message: raw,
          retryable: true,
        })
        .mockResolvedValueOnce({
          kind: "pull_request",
          url: "https://github.com/acme/dispatch-agent/pull/42",
        });
      hooks.ship = { isPending: false, mutateAsync };
      const user = userEvent.setup();
      renderPage("fix-ready-github");
      await user.click((await confirmShip(user, "Open pull request")).confirm);
      expect(await screen.findByText(copy)).toBeInTheDocument();
      expect(screen.queryByText(raw)).toBeNull();
      expect(
        await screen.findByRole("button", { name: "Download patch" }),
      ).toBeInTheDocument();
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Ship" })).toBeEnabled(),
      );

      await user.click((await confirmShip(user, "Open pull request")).confirm);
      await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(2));
      expect(mutateAsync.mock.calls[1][0]).toEqual(
        mutateAsync.mock.calls[0][0],
      );
    },
  );
});

// W10-AGENT-FIX-U01

// The read's override, as the serializer sends it for every candidate.
const noOverride = {
  allowed: false,
  regressions: [],
  reason: "",
  by: null,
  at: null,
};

// Trial 2 of the fix passed every check but must-pass, on a case nobody
// protected: the server ships it on a reason.
function withShipAnywayTrial(fixId) {
  updateFixtureFixRun(fixId, (f) => ({
    ...f,
    candidates: f.candidates.map((c) => {
      if (c.attempt !== 2) return { ...c, override: noOverride };
      const regressions = ["refund-window: policy_cited"];
      return {
        ...c,
        outcome: "rejected",
        verdict: {
          ...c.verdict,
          accepted: false,
          reason: "must-pass: 1 must-pass check regressed",
          target_pass_rate: [0.33, 1.0],
          held_back_pass_rate: [0.17, 0.83],
          must_pass_regressions: regressions,
          tiers: [
            "static",
            "target",
            "held-back",
            "must-pass",
            "repeats",
            "ceilings",
          ].map((name) => ({
            name,
            status: name === "must-pass" ? "failed" : "passed",
            detail: "",
          })),
        },
        override: { ...noOverride, allowed: true, regressions },
      };
    }),
  }));
}

describe("A fix that ended without a winner", () => {
  it("keeps each trial's change, results and check run, opening on the best one", async () => {
    withShipAnywayTrial("fix-no-improvement");
    const user = userEvent.setup();
    renderPage("fix-no-improvement");
    expect(
      within(await screen.findByTestId("trial-1")).getByRole("link", {
        name: "Open run for Trial 1",
      }),
    ).toHaveAttribute(
      "href",
      "/dashboard/simulate/environments/env-fixture/runs/rt-fix-no-improvement/te-fix-no-improvement-c1",
    );

    await user.click(screen.getByRole("tab", { name: "Changes" }));
    expect(
      await screen.findByText("<!-- fix-no-improvement-c2 -->"),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("combobox", { name: "Trial" }));
    await user.click(
      screen.getByRole("option", { name: "Trial 1 · Rejected" }),
    );
    expect(
      await screen.findByText("<!-- fix-no-improvement-c1 -->"),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Per scenario" }));
    const row = within(await screen.findByTestId("scenario-late-cancel"));
    expect(row.getByText("Still failing")).toBeInTheDocument();
    expect(row.getByRole("link", { name: "Open run" })).toHaveAttribute(
      "href",
      "/dashboard/simulate/environments/env-fixture/runs/rt-fix-no-improvement/te-fix-no-improvement-c1",
    );
  });

  it("ships a trial only its must-pass check refused, once the user says why", async () => {
    withShipAnywayTrial("fix-no-improvement");
    const user = userEvent.setup();
    renderPage("fix-no-improvement");
    expect(
      await screen.findByText(
        "Trial 2 passed every check except must-pass: it broke 1 must-pass check (refund-window: policy_cited). You can still ship it with a reason.",
      ),
    ).toBeInTheDocument();
    const offers = screen.getAllByRole("button", { name: "Ship anyway" });
    expect(offers).toHaveLength(1);

    await user.click(offers[0]);
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByText("Make Trial 2's version the default anyway?"),
    ).toBeInTheDocument();
    expect(within(dialog).getByText("Broken checks")).toBeInTheDocument();
    expect(
      within(dialog).getByText("refund-window: policy_cited"),
    ).toBeInTheDocument();
    const confirm = within(dialog).getByRole("button", {
      name: "Make default",
    });
    expect(confirm).toBeDisabled();
    await user.type(
      within(dialog).getByRole("textbox", { name: /Why ship it anyway/ }),
      "The refund wording is fixed in a separate change.",
    );
    expect(confirm).toBeEnabled();
    expect(shipPosts()).toHaveLength(0);

    await user.click(confirm);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Default" })).toBeDisabled(),
    );
    expect(shipPosts()).toHaveLength(1);
    expect(shipPosts()[0][0]).toMatch(
      /candidates\/fix-no-improvement-c2\/make-default\/$/,
    );
    expect(shipPosts()[0][1]).toEqual({
      override_reason: "The refund wording is fixed in a separate change.",
    });
    expect(screen.queryByText(/You can still ship it/)).toBeNull();
  });

  // G3-UX-1
  it("keeps Ship anyway, and what the trial's note says, when GitHub fails on its side", async () => {
    withShipAnywayTrial("fix-no-improvement");
    updateFixtureFixRun("fix-no-improvement", (f) => ({
      ...f,
      agent: { ...f.agent, kind: "github", ref: "main @ 0487e25" },
    }));
    const mutateAsync = vi.fn().mockRejectedValue({
      statusCode: 502,
      error: "fix_github_pull_request_failed",
      message: 'GitHub refused the pull request: {"message":"Server Error"}',
      retryable: true,
    });
    hooks.ship = { isPending: false, mutateAsync };
    const note =
      "Trial 2 passed every check except must-pass: it broke 1 must-pass check (refund-window: policy_cited). You can still ship it with a reason.";
    const user = userEvent.setup();
    renderPage("fix-no-improvement");
    expect(await screen.findByText(note)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Ship anyway" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(
      within(dialog).getByRole("textbox", { name: /Why ship it anyway/ }),
      "The refund wording is fixed in a separate change.",
    );
    await user.click(
      within(dialog).getByRole("button", { name: "Open pull request" }),
    );
    expect(
      await screen.findByText(
        "GitHub could not open the pull request. Try again, or download the patch to apply it by hand.",
      ),
    ).toBeInTheDocument();
    expect(mutateAsync).toHaveBeenCalledWith({
      candidateId: "fix-no-improvement-c2",
      overrideReason: "The refund wording is fixed in a separate change.",
    });
    const row = within(screen.getByTestId("ship-anyway-2"));
    expect(row.getByText(note)).toBeInTheDocument();
    await waitFor(() =>
      expect(row.getByRole("button", { name: "Ship anyway" })).toBeEnabled(),
    );
    expect(row.getByRole("button", { name: "Download patch" })).toBeEnabled();
  });

  it("still ships a winner without a reason, with no Ship anyway beside it", async () => {
    withShipAnywayTrial("fix-ready-archive");
    const user = userEvent.setup();
    renderPage("fix-ready-archive");
    const { dialog, confirm } = await confirmShip(user, "Make default");
    expect(within(dialog).queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("button", { name: "Ship anyway" })).toBeNull();
    await user.click(confirm);
    await waitFor(() => expect(shipPosts()).toHaveLength(1));
    expect(shipPosts()[0][1]).toEqual({});
  });

  it("keeps only Trials and Cost when no trial ran", async () => {
    renderPage("fix-not-reproduced");
    expect(
      await screen.findByRole("tab", { name: "Trials (0)" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Cost" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Changes" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "Per scenario" })).toBeNull();
  });
});
