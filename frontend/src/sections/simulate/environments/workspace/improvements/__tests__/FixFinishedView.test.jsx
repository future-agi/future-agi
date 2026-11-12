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

// The launch form has its own tests; here it only shows which run and environment it opens on.
vi.mock("../LaunchFixDialog", () => ({
  // eslint-disable-next-line react/prop-types
  default: ({ envId, sourceRunId }) => (
    <div
      data-testid="launch-fix-dialog"
      data-env={envId}
      data-source={sourceRunId}
    />
  ),
}));

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

  it("marks a check the fix could not measure as Unknown, with its own icon", () => {
    const fix = fixOf("fix-ready-archive");
    const winner = fix.candidates.find((c) => c.outcome === "winner");
    winner.verdict.tiers = [
      ...winner.verdict.tiers,
      {
        name: "model-lock",
        status: "skipped",
        detail: "static check only: no added line names a model",
      },
    ];
    renderUi(<FixDecisionDrawer fix={fix} open onClose={vi.fn()} />);

    expect(
      screen.getByText("Checks · 5 of 5 passed · 1 unknown"),
    ).toBeInTheDocument();
    const row = screen.getByText("Model unchanged").parentElement;
    expect(within(row).getByText("Unknown")).toBeInTheDocument();
    expect(row.getAttribute("data-check")).toBe("unmeasured");
    expect(screen.getAllByText("Unknown")).toHaveLength(1);
    expect(screen.queryByText("model-lock")).toBeNull();
  });

  const withCeilingParts = () => {
    const fix = fixOf("fix-ready-archive");
    const winner = fix.candidates.find((c) => c.outcome === "winner");
    winner.verdict.tiers = [
      ...winner.verdict.tiers.filter((t) => t.name !== "ceilings"),
      {
        name: "ceilings",
        status: "skipped",
        detail: "reply latency within; agent cost not verified",
        parts: [
          {
            name: "reply latency",
            status: "passed",
            detail: "median 1.1 s, baseline 1 s: within",
          },
          {
            name: "agent cost",
            status: "skipped",
            detail: "the agent's model calls are not traced",
          },
        ],
      },
    ];
    return fix;
  };

  it("shows each ceiling's own status in the verdict's full list of checks", async () => {
    renderUi(<FixVerdict fix={withCeilingParts()} />);

    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Show all" }));

    const latency = screen.getByText("Reply time").parentElement;
    const cost = screen.getByText("Agent's cost").parentElement;
    expect(within(latency).queryByText("Reply time unknown")).toBeNull();
    expect(within(latency).getByText("Within the limit.")).toBeInTheDocument();
    // An untraced agent's cost is unknown, said plainly, never "not verified".
    expect(within(cost).getByText("Cost unknown")).toBeInTheDocument();
    expect(
      within(cost).getByText(
        "This agent's model calls weren't measured. Epoch measures calls made through the OpenAI or Anthropic Python SDK on its own. For other agents, add traceAI tracing.",
      ),
    ).toBeInTheDocument();
  });

  it("shows each ceiling's own status in the decision drawer", () => {
    const fix = fixOf("fix-ready-archive");
    const winner = fix.candidates.find((c) => c.outcome === "winner");
    winner.verdict.tiers = [
      ...winner.verdict.tiers.filter((t) => t.name !== "ceilings"),
      {
        name: "ceilings",
        status: "skipped",
        detail: "reply latency within; agent cost not verified",
        parts: [
          {
            name: "reply latency",
            status: "passed",
            detail: "median 1.1 s, baseline 1 s: within",
          },
          {
            name: "agent cost",
            status: "skipped",
            detail: "the agent's model calls are not traced",
          },
        ],
      },
    ];
    renderUi(<FixDecisionDrawer fix={fix} open onClose={vi.fn()} />);

    const latency = screen.getByText("Reply time").parentElement;
    const cost = screen.getByText("Agent's cost").parentElement;
    expect(within(latency).queryByText("Reply time unknown")).toBeNull();
    expect(within(latency).getByText("Within the limit.")).toBeInTheDocument();
    expect(within(cost).getByText("Cost unknown")).toBeInTheDocument();
    // Only the tier reads Unknown; its unmeasured cost says Cost unknown and why.
    expect(screen.getAllByText("Unknown")).toHaveLength(1);
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

describe("FixFinishedView, no safe improvement", () => {
  it("says what stopped the trials in one sentence, the numbers behind a toggle", async () => {
    renderUi(
      <FixFinishedView
        fix={fixOf("fix-no-improvement")}
        tab="trials"
        onTab={vi.fn()}
        renderTab={() => null}
      />,
    );

    expect(
      screen.getByText(
        "No safe improvement: neither candidate improved the target scenarios.",
      ),
    ).toBeInTheDocument();
    // The engine's summary is not the headline any more.
    expect(
      screen.queryByText(
        "No safe improvement: every candidate broke a must-pass scenario.",
      ),
    ).toBeNull();

    await userEvent
      .setup()
      .click(
        screen.getByRole("button", { name: "Show each candidate's result" }),
      );
    const numbers = within(screen.getByTestId("no-safe-numbers"));
    // Each trial's result in plain words from its own numbers; the engine's text is never shown.
    expect(
      numbers.getByText(
        "Candidate 1 · Target scenarios: 0% → 0%, not a clear gain.",
      ),
    ).toBeInTheDocument();
    expect(
      numbers.queryByText(/Engine's note|target scenarios passed/),
    ).toBeNull();
  });
});

describe("FixFinishedView, what to try next", () => {
  const view = (fix, onTab = vi.fn()) =>
    renderUi(
      <FixFinishedView
        fix={fix}
        tab="trials"
        onTab={onTab}
        renderTab={() => null}
      />,
    );

  it("offers the next steps on a fix with no safe improvement", async () => {
    const user = userEvent.setup();
    const onTab = vi.fn();
    view(fixOf("fix-no-improvement"), onTab);
    const next = within(screen.getByTestId("what-to-try-next"));
    expect(
      next.getByText(
        "Keep the current version. This epoch changed nothing: your agent changes only when you ship a candidate.",
      ),
    ).toBeInTheDocument();

    await user.click(next.getByRole("button", { name: "See the candidates" }));
    expect(onTab).toHaveBeenCalledWith("trials");
    await user.click(next.getByRole("button", { name: "How it was decided" }));
    expect(await screen.findByTestId("fix-decision")).toBeInTheDocument();
  });

  it("starts a new fix from the same run, through the launch form", async () => {
    view(fixOf("fix-no-improvement"));
    expect(screen.queryByTestId("launch-fix-dialog")).toBeNull();

    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Run a new epoch" }));
    const dialog = screen.getByTestId("launch-fix-dialog");
    expect(dialog).toHaveAttribute("data-env", "env-1");
    expect(dialog).toHaveAttribute("data-source", "te-source-run-1");
  });

  it("offers no new fix when the run it started from was deleted", () => {
    view(fixOf("fix-no-improvement", { source_run_id: null }));
    expect(screen.getByTestId("what-to-try-next")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Run a new epoch" }),
    ).toBeNull();
  });

  it.each([
    ["a verified fix", "fix-ready-archive", {}],
    [
      "a limited validation fix",
      "fix-ready-archive",
      { limited_validation: true },
    ],
    ["a failed fix", "fix-failed", {}],
  ])("says nothing about what to try next on %s", (_, id, extra) => {
    view(fixOf(id, extra));
    expect(screen.queryByTestId("what-to-try-next")).toBeNull();
  });
});

describe("FixFinishedView, protected cases it could not be held to", () => {
  it("names them in plain words, and says nothing for a report without them", () => {
    const fix = fixOf("fix-no-improvement");
    fix.report = {
      ...fix.report,
      flaky_protected: [
        {
          key: "s3",
          name: "Escalate a large order",
          sub_goal: "a",
          passed: 2,
          runs: 3,
        },
      ],
    };
    const { unmount } = renderUi(
      <FixFinishedView
        fix={fix}
        tab="trials"
        onTab={vi.fn()}
        renderTab={() => null}
      />,
    );
    expect(screen.getByTestId("flaky-protected")).toHaveTextContent(
      "1 protected case is flaky on the current version, so this epoch wasn't held to it: “Escalate a large order”.",
    );
    unmount();

    renderUi(
      <FixFinishedView
        fix={fixOf("fix-no-improvement")}
        tab="trials"
        onTab={vi.fn()}
        renderTab={() => null}
      />,
    );
    expect(screen.queryByTestId("flaky-protected")).toBeNull();
  });
});

describe("FixDecisionDrawer without a kept trial", () => {
  const checkNames = (trial) =>
    trial
      .getAllByText(
        /^(Change is valid|Target scenarios|Held-back scenarios|Must-pass scenarios)$/,
      )
      .map((el) => el.textContent);

  it("shows each rejected trial with the check that rejected it first", () => {
    renderUi(
      <FixDecisionDrawer
        fix={fixOf("fix-no-improvement")}
        open
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("Rejected candidates")).toBeInTheDocument();
    for (const attempt of [1, 2]) {
      const trial = within(screen.getByTestId(`rejected-${attempt}`));
      expect(trial.getByText(`Candidate ${attempt}`)).toBeInTheDocument();
      expect(checkNames(trial)).toEqual([
        "Target scenarios",
        "Change is valid",
        "Must-pass scenarios",
      ]);
      // The rejecting check in plain words, from the trial's own numbers.
      expect(trial.getByText("0% → 0%, not a clear gain.")).toBeInTheDocument();
    }
  });

  it("says why a check after the rejecting one was not run, never passed or unmeasured", () => {
    renderUi(
      <FixDecisionDrawer
        fix={fixOf("fix-no-improvement")}
        open
        onClose={vi.fn()}
      />,
    );

    const trial = within(screen.getByTestId("rejected-1"));
    const row = trial.getByText("Must-pass scenarios").parentElement;
    expect(
      within(row).getByText(
        "not run — an earlier check failed: Target scenarios",
      ),
    ).toBeInTheDocument();
    expect(row.getAttribute("data-check")).toBe("not-run");
    expect(trial.queryByText("Unknown")).toBeNull();
    expect(within(row).queryByText(/passed|held/)).toBeNull();
  });

  it("leaves a fix with a kept trial as it was", () => {
    renderUi(
      <FixDecisionDrawer
        fix={fixOf("fix-ready-archive")}
        open
        onClose={vi.fn()}
      />,
    );

    expect(screen.queryByText("Rejected candidates")).toBeNull();
    expect(screen.queryByTestId("rejected-1")).toBeNull();
    expect(screen.getByText("Checks · 5 of 5 passed")).toBeInTheDocument();
  });

  it("still shows a rejected trial whose verdict predates recorded checks", () => {
    const fix = fixOf("fix-no-improvement");
    delete fix.candidates[0].verdict.tiers;
    renderUi(<FixDecisionDrawer fix={fix} open onClose={vi.fn()} />);

    const trial = within(screen.getByTestId("rejected-1"));
    expect(
      trial.getByText("Rejected. Target scenarios 0% → 0%."),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId("rejected-2")).getByText("Target scenarios"),
    ).toBeInTheDocument();
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
        "This epoch stopped on something only you can change: The engine stopped: the model provider refused the key.",
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

describe("Ship, while the evals grade the winner", () => {
  const shipButton = () => screen.findByRole("button", { name: "Ship" });

  it("waits until grading finishes, and says why", async () => {
    updateFixtureFixRun("fix-ready-github", (f) => ({
      ...f,
      eval_grading: "pending",
    }));
    renderPage("fix-ready-github");
    expect(await shipButton()).toBeDisabled();
    expect(screen.getByTestId("ship-grading-note")).toHaveTextContent(
      "Platform evals are still scoring this candidate. You can ship it once they finish.",
    );
  });

  it("ships a trial the evals could not finish grading, and says so", async () => {
    updateFixtureFixRun("fix-ready-archive", (f) => ({
      ...f,
      eval_grading: "incomplete",
    }));
    renderPage("fix-ready-archive");
    expect(await shipButton()).toBeEnabled();
    expect(screen.getByTestId("ship-grading-note")).toHaveTextContent(
      "Platform evals did not finish on this candidate, so their results are missing. You can still ship it.",
    );
  });

  it("says nothing once grading is done", async () => {
    updateFixtureFixRun("fix-ready-github", (f) => ({
      ...f,
      eval_grading: "done",
    }));
    renderPage("fix-ready-github");
    expect(await shipButton()).toBeEnabled();
    expect(screen.queryByTestId("ship-grading-note")).toBeNull();
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
      within(dialog).getByText("Make Candidate 3's version the default?"),
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
