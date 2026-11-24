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
// It stays mounted and toggles `open` (so the drawer slides); closed, it shows nothing.
vi.mock("../LaunchFixDialog", () => ({
  // eslint-disable-next-line react/prop-types
  default: ({ open, envId, sourceRunId }) =>
    open && (
      <div
        data-testid="launch-fix-dialog"
        data-env={envId}
        data-source={sourceRunId}
      />
    ),
}));

const axios = (await import("src/utils/axios")).default;
const { headline, listResult } = await import("../fixRunView");
const { default: FixRunPage } = await import("../FixRunPage");
const { default: FixFinishedView, FinishedActions } = await import(
  "../FixFinishedView"
);
const { default: FixVerdict } = await import("../FixVerdict");
const { default: FixDecisionDrawer } = await import("../FixDecisionDrawer");
const { default: CandidatesTable } = await import("../CandidatesTable");

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

// The ship button and its confirm say the same: Open pull request or Make default.
async function confirmShip(user, button) {
  await user.click(await screen.findByRole("button", { name: button }));
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
    expect(listResult(fixOf("fix-ready-no-held")).note).toMatch(
      /^Proven on the targets only · /,
    );
  });

  it("does not say proven on the targets only when cases were held back but have no rate", () => {
    renderUi(
      <FixVerdict
        fix={fixOf("fix-ready-no-held", {
          request: {
            ...fixOf("fix-ready-no-held").request,
            held_back_scenario_keys: ["a", "b", "c", "d"],
          },
        })}
      />,
    );
    expect(screen.queryByText("Proven on the targets only")).toBeNull();
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
        latency: { baseline: 1, candidate: 1.1, allowed: 1.25, limit: null },
        cost: { baseline: null, candidate: null, allowed: null, limit: null },
      },
    ];
    return fix;
  };

  it("shows the winner's cost and reply time as cards, and none without a ceilings check", () => {
    const { unmount } = renderUi(<FixVerdict fix={withCeilingParts()} />);
    const latency = within(screen.getByTestId("verdict-card-latency"));
    expect(latency.getByText("Reply time")).toBeInTheDocument();
    expect(latency.getByText("+10%")).toBeInTheDocument();
    expect(
      latency.getByText("1.10 s against 1.00 s on the current version"),
    ).toBeInTheDocument();
    const cost = within(screen.getByTestId("verdict-card-cost"));
    expect(cost.getByText("Cost / conversation")).toBeInTheDocument();
    expect(cost.getByText("Not measured")).toBeInTheDocument();
    unmount();

    renderUi(<FixVerdict fix={fixOf("fix-ready-archive")} />);
    expect(screen.queryByTestId("verdict-card-latency")).toBeNull();
    expect(screen.queryByTestId("verdict-card-cost")).toBeNull();
    expect(screen.getByTestId("verdict-card-held")).toBeInTheDocument();
  });

  it("shows each ceiling's own status in the verdict's full list of checks", async () => {
    renderUi(<FixVerdict fix={withCeilingParts()} />);

    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Show all" }));

    const checks = within(screen.getByTestId("verdict-checks"));
    const latency = checks.getByText("Reply time").parentElement;
    const cost = checks.getByText("Agent's cost").parentElement;
    expect(within(latency).queryByText("Reply time unknown")).toBeNull();
    expect(
      within(latency).getByText(
        "Within the limit: 1.10 s against 1.00 s on the current version, allowed up to 1.25 s.",
      ),
    ).toBeInTheDocument();
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
        latency: { baseline: 1, candidate: 1.1, allowed: 1.25, limit: null },
        cost: { baseline: null, candidate: null, allowed: null, limit: null },
      },
    ];
    renderUi(<FixDecisionDrawer fix={fix} open onClose={vi.fn()} />);

    const latency = screen.getByText("Reply time").parentElement;
    const cost = screen.getByText("Agent's cost").parentElement;
    expect(within(latency).queryByText("Reply time unknown")).toBeNull();
    expect(
      within(latency).getByText(
        "Within the limit: 1.10 s against 1.00 s on the current version, allowed up to 1.25 s.",
      ),
    ).toBeInTheDocument();
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

  it("labels limited validation, with the held-back tally", async () => {
    renderUi(
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

    // The badge names the ending; the sentence says what stopped the candidates.
    const ending = within(screen.getByTestId("epoch-ending"));
    expect(
      ending.getByText("Epoch 6 · No safe improvement"),
    ).toBeInTheDocument();
    expect(
      ending.getByText("Neither candidate improved the target scenarios."),
    ).toBeInTheDocument();
    expect(ending.getByText("Spent 412 credits.")).toBeInTheDocument();
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
    expect(
      screen.queryByRole("button", {
        name: /^(Open pull request|Make default)$/,
      }),
    ).toBeNull();
  });

  const view = (fix) =>
    renderUi(
      <FixFinishedView
        fix={fix}
        tab="trials"
        onTab={vi.fn()}
        renderTab={() => null}
      />,
    );

  it("offers Retry Epoch N as the ending's one way on, and opens the new fix", async () => {
    const user = userEvent.setup();
    view(fixOf("fix-failed"));
    const ending = within(screen.getByTestId("epoch-ending"));
    expect(ending.getByText("Epoch 2 · Failed")).toBeInTheDocument();
    expect(
      ending.getByText("Spent 412 credits before it stopped."),
    ).toBeInTheDocument();
    expect(
      ending.queryByRole("button", { name: "Run Epoch again" }),
    ).toBeNull();
    await user.click(ending.getByRole("button", { name: "Retry Epoch 2" }));
    await waitFor(() => expect(navigate).toHaveBeenCalled());
    expect(navigate.mock.calls[0][0]).toMatch(
      /^\/dashboard\/simulate\/environments\/env-1\/improvements\/fix-new-\d+$/,
    );
  });

  it("keeps Retry out of the header, so the ending's is the one primary", () => {
    renderUi(<FinishedActions fix={fixOf("fix-failed")} onReview={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /^Retry/ })).toBeNull();
  });

  it("runs a new epoch from the same run once a failed fix was retried", async () => {
    view(fixOf("fix-failed", { retryable: false }));
    const ending = within(screen.getByTestId("epoch-ending"));
    expect(ending.queryByRole("button", { name: /^Retry/ })).toBeNull();
    await userEvent
      .setup()
      .click(ending.getByRole("button", { name: "Run Epoch again" }));
    expect(screen.getByTestId("launch-fix-dialog")).toHaveAttribute(
      "data-source",
      "te-source-run-1",
    );
  });

  it.each([
    [
      "fix-stopped",
      "Epoch 3 · Stopped",
      "Spent 412 credits before it stopped.",
    ],
    [
      "fix-out-of-budget",
      "Epoch 4 · Stopped at budget",
      "Spent 1,500 credits before it stopped.",
    ],
    [
      "fix-not-reproduced",
      "Epoch 5 · Could not reproduce",
      "Spent 412 credits on this check.",
    ],
  ])(
    "reads %s in the one ending layout: badge, sentence, spent, one way on",
    (id, label, spent) => {
      const fix = fixOf(id);
      view(fix);
      const ending = within(screen.getByTestId("epoch-ending"));
      expect(ending.getByText(label)).toBeInTheDocument();
      expect(ending.getByText(headline(fix))).toBeInTheDocument();
      expect(ending.getByText(spent)).toBeInTheDocument();
      expect(
        ending.getAllByRole("button", { name: "Run Epoch again" }),
      ).toHaveLength(1);
    },
  );

  it("says nothing about spend for an epoch that never started", () => {
    view(
      fixOf("fix-failed", {
        started_at: null,
        failure: { code: "fix_queue_timeout", message: "" },
      }),
    );
    const ending = within(screen.getByTestId("epoch-ending"));
    expect(ending.getByText(/could not start/)).toBeInTheDocument();
    expect(ending.queryByText(/^Spent/)).toBeNull();
  });

  it("runs an issue's epoch again from its issue", () => {
    view(
      fixOf("fix-stopped", {
        issue: { job_id: "j", cluster_id: "S-1A2B3C4D", project_id: "p" },
      }),
    );
    expect(
      within(screen.getByTestId("epoch-ending")).getByRole("link", {
        name: "Run Epoch again",
      }),
    ).toHaveAttribute("href", "/dashboard/error-feed/S-1A2B3C4D");
  });

  it("offers no way on when the run it started from is gone", () => {
    view(fixOf("fix-stopped", { source_run_id: null }));
    const ending = within(screen.getByTestId("epoch-ending"));
    expect(
      ending.queryByRole("button", { name: "Run Epoch again" }),
    ).toBeNull();
    expect(ending.getByText(/You stopped this epoch/)).toBeInTheDocument();
  });
});

describe("FixFinishedView, candidates", () => {
  const view = (id) =>
    renderUi(
      <FixFinishedView
        fix={fixOf(id)}
        tab="trials"
        onTab={vi.fn()}
        renderTab={() => null}
      />,
    );

  it("groups the candidates by where they stopped, the closest calls open", async () => {
    view("fix-ready-archive");
    expect(screen.getByTestId("candidate-group-winner")).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(
      within(screen.getByTestId("candidate-3")).getByText("Winner"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId("candidate-2")).getByText("Passed, not picked"),
    ).toBeInTheDocument();
    // A rejected group starts folded.
    const rejected = screen.getByTestId("candidate-group-target");
    expect(rejected).toHaveTextContent("Didn't improve the target scenarios");
    expect(screen.queryByTestId("candidate-1")).toBeNull();
    await userEvent.setup().click(rejected);
    const row = within(screen.getByTestId("candidate-1"));
    expect(row.getByText("0% → 0%, not a clear gain.")).toBeInTheDocument();
    expect(row.getByText("Not run")).toBeInTheDocument();
  });

  it("opens each candidate's own check run, and keeps the pass-rate chart below", () => {
    view("fix-ready-archive");
    expect(
      within(screen.getByTestId("candidate-3")).getByRole("link", {
        name: "Open run",
      }),
    ).toHaveAttribute(
      "href",
      "/dashboard/simulate/environments/env-1/runs/rt-fix-ready-archive/te-fix-ready-archive-c3",
    );
    expect(screen.getAllByTestId("trial-point")).toHaveLength(3);
  });

  it("folds a finished epoch's diagnosis into one line that opens to every cause", async () => {
    const fix = fixOf("fix-ready-archive", {
      diagnosis: {
        causes: [
          "the instructions never ask it to escalate",
          "it replies before it looks the order up",
        ],
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
    const diagnosis = within(screen.getByTestId("finished-diagnosis"));
    const toggle = diagnosis.getByRole("button");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveTextContent(
      "The instructions never ask it to escalate. · 1 more",
    );
    await userEvent.setup().click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(
      diagnosis.getByText("It replies before it looks the order up."),
    ).toBeInTheDocument();
    // Open, each cause shows once: the folded summary does not repeat the first.
    expect(
      diagnosis.getAllByText(/The instructions never ask it to escalate\./),
    ).toHaveLength(1);
    expect(diagnosis.queryByText(/· 1 more/)).toBeNull();
  });

  it("says nothing about a diagnosis a finished epoch never ran", () => {
    renderUi(
      <FixFinishedView
        fix={fixOf("fix-stopped", { diagnosis: null })}
        tab="trials"
        onTab={vi.fn()}
        renderTab={() => null}
      />,
    );
    expect(screen.queryByTestId("finished-diagnosis")).toBeNull();
  });

  it("opens a group that appears on a later read as its default says, and keeps a person's fold", async () => {
    const before = fixOf("fix-ready-archive");
    before.candidates = before.candidates.filter((c) => c.outcome !== "winner");
    const { rerender } = renderUi(<CandidatesTable fix={before} />);
    expect(screen.queryByTestId("candidate-group-winner")).toBeNull();
    // The person folds Passed, not picked.
    await userEvent
      .setup()
      .click(screen.getByTestId("candidate-group-finalist"));
    expect(screen.getByTestId("candidate-group-finalist")).toHaveAttribute(
      "aria-expanded",
      "false",
    );

    rerender(wrap(<CandidatesTable fix={fixOf("fix-ready-archive")} />));
    expect(screen.getByTestId("candidate-group-winner")).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(screen.getByTestId("candidate-3")).toBeInTheDocument();
    expect(screen.getByTestId("candidate-group-finalist")).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("opens a candidate's change beside the page", async () => {
    const user = userEvent.setup();
    view("fix-no-improvement");
    await user.click(screen.getByTestId("candidate-group-target"));
    await user.click(
      within(screen.getByTestId("candidate-2")).getByRole("button", {
        name: "See its change",
      }),
    );
    const drawer = within(await screen.findByTestId("candidate-change"));
    expect(drawer.getByText("Candidate 2's change")).toBeInTheDocument();
    expect(
      await drawer.findByText("<!-- fix-no-improvement-c2 -->"),
    ).toBeInTheDocument();
  });
});

describe("Ship, while the evals grade the winner", () => {
  const shipButton = () =>
    screen.findByRole("button", {
      name: /^(Open pull request|Make default)$/,
    });

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

  it.each([
    [
      "a pull request with nothing held back",
      "fix-ready-github",
      "Open pull request",
      { held_back_scenario_keys: [] },
      false,
      "Proven on the targets only: it opens as a draft pull request, marked Proven on the targets only in its title and body.",
    ],
    [
      "a version with nothing held back",
      "fix-ready-archive",
      "Make default",
      { held_back_scenario_keys: [] },
      false,
      "Proven on the targets only: nothing was held back to prove this change.",
    ],
    [
      "a pull request with too few held back",
      "fix-ready-github",
      "Open pull request",
      {},
      true,
      "Limited validation: it opens as a draft pull request, marked Limited validation in its title and body.",
    ],
  ])(
    "says how strong the result is before it ships %s",
    async (_, id, button, request, limited, note) => {
      const user = userEvent.setup();
      updateFixtureFixRun(id, (f) => ({
        ...f,
        request: { ...f.request, ...request },
        limited_validation: limited,
      }));
      renderPage(id);
      const { dialog } = await confirmShip(user, button);
      expect(within(dialog).getByText(note)).toBeInTheDocument();
    },
  );

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
    // The confirm says Make default too: wait for it to close first.
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(
      await screen.findByRole("button", { name: "Make default" }),
    ).toBeEnabled();
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
