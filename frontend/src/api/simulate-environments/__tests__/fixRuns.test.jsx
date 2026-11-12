import { describe, it, expect, beforeEach, vi } from "vitest";
import PropTypes from "prop-types";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("src/utils/axios", async (importOriginal) => {
  const { withFakeFixRunsApi } = await import(
    "src/api/simulate-environments/__tests__/fixtures/fixRunsApi"
  );
  return withFakeFixRunsApi(await importOriginal());
});

const capability = vi.hoisted(() => ({ allowed: true }));
vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useFeatureAllowed: () => ({ allowed: capability.allowed, isLoading: false }),
}));

const axios = (await import("src/utils/axios")).default;
const { FIX_RUN_FIXTURES, fixtureFixRun, resetFixRunFixtures } = await import(
  "./fixtures/fixRuns"
);
const {
  useEnvironmentFixRuns,
  fixPollInterval,
  FIX_POLL_MS,
  fixErrorOf,
  collectAllCalls,
  checkRunsForScenarios,
  epochTargets,
  epochTargetsNote,
} = await import("../fixRuns");

describe("Run Epoch's starting cases", () => {
  const scenario = (key, evidence, failing = true) => ({
    key,
    evidence_import_id: evidence,
    failing_sub_goals: failing ? ["booked"] : [],
  });
  const options = (min, scenarios) => ({
    limits: { scenarios_min: min },
    scenarios,
  });

  it("takes only the imported failing cases when they reach the minimum", () => {
    const got = epochTargets(
      options(2, [
        scenario("a", "i1"),
        scenario("x", null),
        scenario("b", "i1"),
      ]),
    );
    expect(got).toEqual({ imported: ["a", "b"], added: [], keys: ["a", "b"] });
    expect(epochTargetsNote(got)).toBe(
      "2 failing scenarios from imported evidence",
    );
  });

  it("tops up with the run's other failing cases when short, imported first", () => {
    const got = epochTargets(
      options(3, [
        scenario("x", null),
        scenario("y", null, false),
        scenario("a", "i1"),
        scenario("z", null),
      ]),
    );
    expect(got.keys).toEqual(["a", "x", "z"]);
    expect(got.added).toEqual(["x", "z"]);
    expect(epochTargetsNote(got)).toBe(
      "1 from imported evidence, 2 more failing scenarios from this run to reach the minimum",
    );
  });

  it("never adds past the minimum", () => {
    const got = epochTargets(
      options(2, [
        scenario("a", "i1"),
        ...["p", "q", "r"].map((k) => scenario(k, null)),
      ]),
    );
    expect(got.keys).toEqual(["a", "p"]);
  });

  it("adds what there is when the run has too few failing cases; the form says why", () => {
    const got = epochTargets(
      options(5, [scenario("a", "i1"), scenario("x", null)]),
    );
    expect(got.keys).toEqual(["a", "x"]);
  });

  it("adds nothing without a minimum (before the launch limits exist)", () => {
    const got = epochTargets({
      scenarios: [scenario("a", "i1"), scenario("x", null)],
    });
    expect(got.keys).toEqual(["a"]);
  });
});

const makeWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const Wrapper = ({ children }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  Wrapper.propTypes = { children: PropTypes.node };
  return Wrapper;
};

describe("fix-run list", () => {
  beforeEach(() => {
    axios.get.mockClear();
    resetFixRunFixtures();
    capability.allowed = true;
  });

  it("asks for one 1-based page and reports the server's total", async () => {
    const { result } = renderHook(
      () => useEnvironmentFixRuns("env-1", { page: 1, pageSize: 10 }),
      { wrapper: makeWrapper() },
    );
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(axios.get).toHaveBeenCalledWith("/simulate/api/agent-fix-runs/", {
      params: { environment_id: "env-1", page: 2, limit: 10 },
    });
    expect(result.current.fixes).toHaveLength(FIX_RUN_FIXTURES.length - 10);
    expect(result.current.count).toBe(FIX_RUN_FIXTURES.length);
  });

  it("asks nothing when the org lacks Fix my agent", async () => {
    capability.allowed = false;
    const { result } = renderHook(() => useEnvironmentFixRuns("env-1"), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(axios.get).not.toHaveBeenCalled();
    expect(result.current.count).toBe(0);
  });

  it("polls only while the fix is live", () => {
    ["queued", "running", "stopping"].forEach((state) =>
      expect(fixPollInterval({ state })).toBe(FIX_POLL_MS),
    );
    expect(FIX_POLL_MS).toBe(5000);
    [
      "ready",
      "no_improvement",
      "not_reproduced",
      "out_of_budget",
      "stopped",
      "failed",
    ].forEach((state) => expect(fixPollInterval({ state })).toBe(false));
  });
});

describe("fixErrorOf", () => {
  it("reads the code and message from the app's or axios's error shape", () => {
    expect(
      fixErrorOf({
        statusCode: 409,
        error: "fix_candidate_not_activated",
        message: "Could not activate v9",
      }),
    ).toEqual({
      code: "fix_candidate_not_activated",
      message: "Could not activate v9",
      offerPatch: false,
    });
    expect(
      fixErrorOf({
        response: {
          status: 422,
          data: { error: "fix_candidate_not_proven", message: "m" },
        },
      }).code,
    ).toBe("fix_candidate_not_proven");
  });

  it.each([
    "fix_github_not_connected",
    "fix_github_commit_unknown",
    "github_repository_invalid",
  ])("offers the patch on %s", (code) => {
    expect(
      fixErrorOf({ statusCode: 422, error: code, message: "m" }).offerPatch,
    ).toBe(true);
  });

  it("tells an entitlement 402 from a usage 402", () => {
    expect(
      fixErrorOf({
        statusCode: 402,
        upgrade_required: true,
        error: { code: "x", message: "Fix my agent is not on your plan" },
      }),
    ).toEqual({
      code: "entitlement",
      message: "Fix my agent is not on your plan",
      offerPatch: false,
    });
    expect(
      fixErrorOf({
        statusCode: 402,
        error_code: "usage_limit",
        message: "Credit limit reached",
      }),
    ).toEqual({
      code: "usage",
      message: "Credit limit reached",
      offerPatch: false,
    });
  });
});

describe("Per scenario data", () => {
  it("counts every fix-level check run as baseline and reads every page of calls", async () => {
    const fix = structuredClone(fixtureFixRun("fix-ready-archive"));
    fix.check_runs = [
      { run_id: "rt", test_execution_id: "te-b1", label: "baseline-r1" },
      { run_id: "rt", test_execution_id: "te-b2", label: "base-2" },
    ];
    expect(
      checkRunsForScenarios(fix, "fix-ready-archive-c3").before.map(
        (r) => r.test_execution_id,
      ),
    ).toEqual(["te-b1", "te-b2"]);

    const pages = { 1: 200, 2: 200, 3: 37 };
    const fetchPage = vi.fn(async (page) => ({
      count: 437,
      results: Array.from({ length: pages[page] || 0 }, (_, i) => ({
        id: `${page}-${i}`,
      })),
    }));
    expect(await collectAllCalls(fetchPage, 200)).toHaveLength(437);
    expect(fetchPage).toHaveBeenCalledTimes(3);
  });
});
