import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";

import {
  ATTEMPT_ID,
  BLOCKED_BRIEFS,
  ENGINE_STATIC_DETAIL,
  ENGINE_TARGET_DETAIL,
  HELD_BACK_KEYS,
  SOURCE_RUN_ID,
  fixtureBlockedBrief,
  fixtureBrief,
  fixtureVerdict,
  outsideAttemptFix,
} from "src/api/simulate-environments/__tests__/fixtures/codingAgent";
import { fixtureFixRun } from "src/api/simulate-environments/__tests__/fixtures/fixRuns";

// The deployment's setting, as the capabilities read carries it.
const caps = vi.hoisted(() => ({ codingAgent: true }));
vi.mock("src/hooks/useCapabilities", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useCapabilities: () => ({
      data: { coding_agent_attempts_enabled: caps.codingAgent },
      isLoading: false,
      isError: false,
    }),
    useFeatureAllowed: () => ({ allowed: true, isLoading: false }),
  };
});

// The two coding-agent routes, answered from the fixtures; anything else is a bug.
const api = vi.hoisted(() => ({
  brief: null,
  briefStatus: 200,
  verdict: null,
  get: null,
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  const routes = actual.endpoints.agentFixRuns;
  api.get = vi.fn((url, config = {}) => {
    if (url === routes.codingAgentBrief()) {
      expect(config.params).toEqual({ source_run_id: SOURCE_RUN_ID });
      // The app's axios rejects with the body spread plus statusCode.
      if (api.briefStatus === 404) {
        return Promise.reject({ detail: "Not found", statusCode: 404 });
      }
      return Promise.resolve({ data: api.brief });
    }
    if (url === routes.verdict(ATTEMPT_ID)) {
      return Promise.resolve({ data: api.verdict });
    }
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
  return { ...actual, default: { ...actual.default, get: api.get } };
});

const clipboard = vi.hoisted(() => ({ copy: vi.fn(async () => true) }));
vi.mock("src/utils/copyToClipboard", () => ({
  copyToClipboard: clipboard.copy,
}));

// The launch form is the engine's; an attempt never opens it.
vi.mock("../LaunchFixDialog", () => ({ default: () => null }));

const { default: EpochMoreMenu } = await import("../EpochMoreMenu");
const { default: CopyForCodingAgentButton } = await import(
  "../CopyForCodingAgentButton"
);
const { default: FixFinishedView } = await import("../FixFinishedView");
const { headline, stageLabel, stepRows } = await import("../fixRunView");
const { default: OutsideAttemptVerdict } = await import(
  "../OutsideAttemptVerdict"
);
const { codingAgentSetup } = await import(
  "src/api/simulate-environments/codingAgent"
);
const { isUncountedAttempt } = await import("../attemptVerdictView");

const SETUP_URL = `${window.location.origin}/dashboard/settings/mcp-server`;
const attemptFix = () =>
  outsideAttemptFix(structuredClone(fixtureFixRun("fix-ready-archive")));

// The candidates' chart reads theme.palette.data, so render with the app theme.
const appTheme = createTheme({ palette: palette("light") });

function renderUi(ui) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <ThemeProvider theme={appTheme}>
      <QueryClientProvider client={client}>
        <SnackbarProvider>
          <MemoryRouter>{ui}</MemoryRouter>
        </SnackbarProvider>
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

const briefCalls = () =>
  api.get.mock.calls.filter(([url]) => url.includes("coding-agent-brief"));

beforeEach(() => {
  caps.codingAgent = true;
  api.brief = fixtureBrief();
  api.briefStatus = 200;
  api.verdict = fixtureVerdict();
  api.get.mockClear();
  clipboard.copy.mockClear();
});

describe("Copy for coding agent on the Epoch page's ⋯ menu", () => {
  async function openMenu(fix = attemptFix()) {
    const user = userEvent.setup();
    renderUi(<EpochMoreMenu fix={fix} />);
    await user.click(screen.getByRole("button", { name: "More actions" }));
    return user;
  }

  it("is not on the menu, and reads no brief, while the setting is off", async () => {
    caps.codingAgent = false;
    await openMenu();
    expect(screen.getByText("Copy epoch ID")).toBeInTheDocument();
    expect(screen.queryByText("Copy for coding agent")).not.toBeInTheDocument();
    expect(briefCalls()).toHaveLength(0);
  });

  it("copies the setup, then the brief's instructions exactly, and says so", async () => {
    const user = await openMenu();
    await user.click(screen.getByText("Copy for coding agent"));
    await waitFor(() => expect(clipboard.copy).toHaveBeenCalledTimes(1));
    const [copied] = clipboard.copy.mock.calls[0];
    expect(copied).toBe(
      `${codingAgentSetup(SETUP_URL)}\n\n---\n\n${api.brief.instructions}`,
    );
    expect(copied).toContain("npx @future-agi/cli");
    expect(copied).toContain("fagi login");
    expect(copied).toContain(SETUP_URL);
    for (const key of HELD_BACK_KEYS) expect(copied).not.toContain(key);
    expect(
      await screen.findByText("Copied. Paste it into your coding agent."),
    ).toBeInTheDocument();
  });

  it.each(Object.keys(BLOCKED_BRIEFS))(
    "says why a brief blocked by %s can't be used, in the platform's words, and copies nothing",
    async (code) => {
      api.brief = fixtureBlockedBrief(code);
      const user = await openMenu();
      await user.click(screen.getByText("Copy for coding agent"));
      expect(await screen.findByText(BLOCKED_BRIEFS[code])).toBeInTheDocument();
      expect(document.body).not.toHaveTextContent(code);
      expect(clipboard.copy).not.toHaveBeenCalled();
    },
  );

  it("is disabled, with why, on an epoch that has no run to brief from", async () => {
    await openMenu({ ...attemptFix(), source_run_id: null });
    const item = screen.getByText("Copy for coding agent").closest("li");
    expect(item).toHaveAttribute("aria-disabled", "true");
    expect(
      within(item).getByText(
        "Only an epoch started from a run has a brief to copy",
      ),
    ).toBeInTheDocument();
  });
});

describe("An attempt from a person's coding agent on its Epoch page", () => {
  it("shows nothing, and reads no verdict, while the setting is off", () => {
    caps.codingAgent = false;
    const { container } = renderUi(
      <OutsideAttemptVerdict fix={attemptFix()} />,
    );
    expect(container).toBeEmptyDOMElement();
    expect(api.get).not.toHaveBeenCalled();
  });

  it("shows nothing on an Epoch the engine made", () => {
    const { container } = renderUi(
      <OutsideAttemptVerdict fix={fixtureFixRun("fix-ready-archive")} />,
    );
    expect(container).toBeEmptyDOMElement();
    expect(api.get).not.toHaveBeenCalled();
  });

  it("marks it as the coding agent's and shows each check, held-back as a status only", async () => {
    renderUi(<OutsideAttemptVerdict fix={attemptFix()} />);
    const panel = screen.getByTestId("outside-attempt");
    expect(
      within(panel).getByText("Attempt 2 from your coding agent"),
    ).toBeInTheDocument();
    expect(
      within(panel).getByText(
        "Ask a supervisor before refunding a late cancellation.",
      ),
    ).toBeInTheDocument();
    const checks = await within(panel).findByTestId("outside-attempt-checks");
    const rows = Array.from(checks.children);
    expect(rows.map((row) => row.getAttribute("data-check"))).toEqual([
      "passed",
      "passed",
      "failed",
      "passed",
      "not-run",
    ]);
    expect(rows[2]).toHaveTextContent(
      "Held-back scenariosDid not pass on cases your agent wasn't given.",
    );
    expect(rows[4]).toHaveTextContent(
      "not run — an earlier check failed: Held-back scenarios",
    );
    // Nothing of the engine's own words, and no held-back case, reaches the page.
    const text = panel.textContent;
    expect(text).not.toContain(ENGINE_STATIC_DETAIL);
    expect(text).not.toContain(ENGINE_TARGET_DETAIL);
    for (const key of HELD_BACK_KEYS) expect(text).not.toContain(key);
    expect(api.get).toHaveBeenCalledWith(
      expect.stringContaining(`/agent-fix-runs/${ATTEMPT_ID}/verdict/`),
    );
  });

  it("says Future AGI is still checking while the attempt runs with no check yet", async () => {
    api.verdict = fixtureVerdict({
      state: "running",
      step: "verify",
      ending: null,
      tiers: [],
      patch_available: false,
    });
    renderUi(
      <OutsideAttemptVerdict fix={{ ...attemptFix(), state: "running" }} />,
    );
    expect(
      await screen.findByText(
        "Future AGI is checking this attempt. Its checks show here when they have run.",
      ),
    ).toBeInTheDocument();
  });

  it("reads a skip with no failed check before it as unmeasured, not as not run", async () => {
    api.verdict = fixtureVerdict({
      tiers: [
        { name: "static", status: "passed", detail: ENGINE_STATIC_DETAIL },
        { name: "target", status: "passed", detail: ENGINE_TARGET_DETAIL },
        { name: "ceilings", status: "skipped", detail: "" },
      ],
    });
    renderUi(<OutsideAttemptVerdict fix={attemptFix()} />);
    const checks = await screen.findByTestId("outside-attempt-checks");
    const ceilings = checks.children[2];
    expect(ceilings).toHaveAttribute("data-check", "unmeasured");
    expect(ceilings).toHaveTextContent("Couldn't measure");
    expect(ceilings).not.toHaveTextContent("not run");
  });

  it("says a change refused before any check failed the first one, without the engine's words", async () => {
    const refusal = "patch touches locked path spec.json";
    api.verdict = fixtureVerdict({
      tiers: [],
      summary: refusal,
      patch_available: false,
    });
    renderUi(<OutsideAttemptVerdict fix={attemptFix()} />);
    const checks = await screen.findByTestId("outside-attempt-checks");
    expect(checks.children).toHaveLength(1);
    expect(checks.children[0]).toHaveAttribute("data-check", "failed");
    expect(checks.children[0]).toHaveTextContent(
      "Change is validThe change did not apply, was too large, or changed a locked file.",
    );
    expect(
      screen.getByText(
        "Future AGI checked nothing else. Fix the change, then submit it again with fagi epoch submit.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByTestId("outside-attempt")).not.toHaveTextContent(
      refusal,
    );
  });

  // The verdict's summary of a failed attempt is the platform's words for its failure
  // (selectors/agent_fix_outside.shown_summary), never a static refusal.
  it("says an attempt that failed before any check does not use one of the run's attempts, and never calls it refused", async () => {
    const failure = {
      code: "fix_engine_failed",
      message: "The Epoch stopped because of a problem on our side.",
    };
    api.verdict = fixtureVerdict({
      state: "failed",
      step: "done",
      ending: "failed",
      failure,
      tiers: [],
      summary: failure.message,
      patch_available: false,
    });
    renderUi(
      <OutsideAttemptVerdict fix={{ ...attemptFix(), state: "failed" }} />,
    );
    expect(
      await screen.findByText(
        "This attempt stopped before Future AGI checked the change. It does not use one of the run's attempts. Submit it again with fagi epoch submit.",
      ),
    ).toBeInTheDocument();
    const panel = screen.getByTestId("outside-attempt");
    expect(panel).not.toHaveTextContent("fix_engine_failed");
    expect(panel).not.toHaveTextContent("did not apply");
    expect(
      screen.queryByTestId("outside-attempt-checks"),
    ).not.toBeInTheDocument();
  });
});

describe("Copy for coding agent reads the brief fresh", () => {
  it("reads the brief again on each copy, so attempts submitted meanwhile show", async () => {
    const user = userEvent.setup();
    renderUi(<CopyForCodingAgentButton sourceRunId={SOURCE_RUN_ID} />);
    const button = screen.getByRole("button", {
      name: /Copy for coding agent/,
    });
    await user.click(button);
    await waitFor(() => expect(clipboard.copy).toHaveBeenCalledTimes(1));
    api.brief = fixtureBrief({
      attempts: { used: 2, max: 4 },
      instructions: `${fixtureBrief().instructions}\nThis run has 2 left.`,
    });
    await user.click(
      await screen.findByRole("button", {
        name: /Copied|Copy for coding agent/,
      }),
    );
    await waitFor(() => expect(clipboard.copy).toHaveBeenCalledTimes(2));
    expect(briefCalls()).toHaveLength(2);
    expect(clipboard.copy.mock.calls[1][0]).toContain("This run has 2 left.");
  });

  it("says plainly that a run has no brief when the route answers 404", async () => {
    api.briefStatus = 404;
    const user = userEvent.setup();
    renderUi(<CopyForCodingAgentButton sourceRunId={SOURCE_RUN_ID} />);
    await user.click(
      screen.getByRole("button", { name: /Copy for coding agent/ }),
    );
    expect(
      await screen.findByText(
        "Future AGI has no brief for this run. The run may have been deleted. Reload the page and try again.",
      ),
    ).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("Not found");
    expect(clipboard.copy).not.toHaveBeenCalled();
  });
});

describe("An attempt's running stages and ending", () => {
  it("runs through its own stages, never diagnosing or proposing", () => {
    const fix = {
      ...attemptFix(),
      state: "running",
      step: "search",
      diagnosis: null,
      candidates: [],
    };
    expect(stepRows(fix).map((row) => row.label)).toEqual([
      "Measuring",
      "Checking the change",
      "Checking held-back",
    ]);
    expect(stageLabel(fix)).toBe("Checking the change");
    const text = stepRows(fix)
      .flatMap((row) => row.lines)
      .join(" ");
    expect(text).not.toMatch(
      /diagnos|propos|Tries changes|Reads the failures/i,
    );
  });

  it.each([
    ["failed", { code: "fix_engine_failed", message: "x" }, true],
    ["failed", { code: "fix_deadline", message: "x" }, false],
    ["no_improvement", null, false],
    ["out_of_budget", null, false],
  ])(
    "ends %s in attempt words, with the coding agent's next step",
    (state, failure, retryable) => {
      const fix = {
        ...attemptFix(),
        state,
        failure,
        retryable,
        retried_from_id: null,
      };
      const text = headline(fix);
      expect(text).toMatch(/attempt|coding agent/i);
      expect(text).not.toMatch(
        /retried|Run a new epoch|Run Epoch|Debug failures/i,
      );
    },
  );

  it("offers Copy for coding agent as the failed attempt's one way on, never Retry or Run Epoch", () => {
    const fix = {
      ...attemptFix(),
      state: "failed",
      retryable: true,
      failure: { code: "fix_engine_failed", message: "x" },
      candidates: [],
    };
    renderUi(<FixFinishedView fix={fix} tab="trials" onTab={() => {}} />);
    expect(
      screen.getByTestId("attempt-copy-for-coding-agent"),
    ).toHaveTextContent("Copy for coding agent");
    expect(
      screen.queryByRole("button", { name: /Retry/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/Run Epoch again|Run a new epoch/),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("fix-finished-view")).toHaveTextContent(
      "Submit it again with fagi epoch submit.",
    );
  });

  it("gives a no-improvement attempt the same way on, not the engine's what-to-try-next", () => {
    const fix = { ...attemptFix(), state: "no_improvement" };
    renderUi(<FixFinishedView fix={fix} tab="trials" onTab={() => {}} />);
    expect(
      screen.getByTestId("attempt-copy-for-coding-agent"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("what-to-try-next")).not.toBeInTheDocument();
    expect(
      screen.queryByText(/Run Epoch again|Run a new epoch/),
    ).not.toBeInTheDocument();
  });
});

describe("Which attempts the Epochs list marks as not counted", () => {
  it("is a failed attempt with no verdict on any candidate, as the platform counts", () => {
    const fix = attemptFix();
    const withVerdict = fix.candidates;
    expect(
      isUncountedAttempt({ ...fix, state: "failed", candidates: [] }),
    ).toBe(true);
    expect(
      isUncountedAttempt({
        ...fix,
        state: "failed",
        candidates: withVerdict.map((c) => ({ ...c, verdict: null })),
      }),
    ).toBe(true);
    expect(
      isUncountedAttempt({ ...fix, state: "failed", candidates: withVerdict }),
    ).toBe(false);
    expect(
      isUncountedAttempt({ ...fix, state: "no_improvement", candidates: [] }),
    ).toBe(false);
  });
});
