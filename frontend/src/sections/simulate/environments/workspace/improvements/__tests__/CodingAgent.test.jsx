import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";

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
const api = vi.hoisted(() => ({ brief: null, verdict: null, get: null }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  const routes = actual.endpoints.agentFixRuns;
  api.get = vi.fn((url, config = {}) => {
    if (url === routes.codingAgentBrief()) {
      expect(config.params).toEqual({ source_run_id: SOURCE_RUN_ID });
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

const { default: EpochMoreMenu } = await import("../EpochMoreMenu");
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

function renderUi(ui) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <SnackbarProvider>
        <MemoryRouter>{ui}</MemoryRouter>
      </SnackbarProvider>
    </QueryClientProvider>,
  );
}

const briefCalls = () =>
  api.get.mock.calls.filter(([url]) => url.includes("coding-agent-brief"));

beforeEach(() => {
  caps.codingAgent = true;
  api.brief = fixtureBrief();
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

  it("says an attempt that failed before any check does not use one of the run's attempts", async () => {
    api.verdict = fixtureVerdict({
      state: "failed",
      step: "done",
      ending: null,
      failure: { code: "fix_engine_failed", message: "The Epoch stopped." },
      tiers: [],
      summary: "",
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
    expect(screen.getByTestId("outside-attempt")).not.toHaveTextContent(
      "fix_engine_failed",
    );
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
