import { describe, it, expect, beforeEach, vi } from "vitest";
import PropTypes from "prop-types";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { get: vi.fn(), post: vi.fn() } };
});

const { resetFixRunFixtures, fixtureFixRun } = await import(
  "../_fixtures/fixRuns"
);
const {
  getFixRun,
  useStopFixRun,
  useShipCandidate,
  fixErrorOf,
  fetchCandidatePatch,
  patchFileName,
} = await import("../fixRuns");

const makeWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const Wrapper = ({ children }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  Wrapper.propTypes = { children: PropTypes.node };
  return Wrapper;
};

const winner = (fix) => fix.candidates.find((c) => c.outcome === "winner");

describe("stop, ship and patch on fixtures", () => {
  beforeEach(() => resetFixRunFixtures());

  it("stops a running fix: stopping first, then stopped", async () => {
    const { result } = renderHook(() => useStopFixRun("fix-search"), {
      wrapper: makeWrapper(),
    });
    await act(() => result.current.mutateAsync());
    expect((await getFixRun("fix-search")).state).toBe("stopping");
    const next = await getFixRun("fix-search");
    expect(next.state).toBe("stopped");
    expect(next.finished_at).not.toBeNull();
  });

  it("ships an uploaded agent's winner by making it the default", async () => {
    const fix = fixtureFixRun("fix-ready-archive");
    const { result } = renderHook(() => useShipCandidate(fix), {
      wrapper: makeWrapper(),
    });
    let outcome;
    await act(async () => {
      outcome = await result.current.mutateAsync(winner(fix).id);
    });
    expect(outcome).toEqual({ kind: "activated" });
    expect(
      fixtureFixRun("fix-ready-archive").environment.default_agent_version_id,
    ).toBe(winner(fix).agent_version_id);
  });

  it("reports already_default when the winner is already the default", async () => {
    const fix = fixtureFixRun("fix-ready-shipped");
    const { result } = renderHook(() => useShipCandidate(fix), {
      wrapper: makeWrapper(),
    });
    let outcome;
    await act(async () => {
      outcome = await result.current.mutateAsync(winner(fix).id);
    });
    expect(outcome).toEqual({ kind: "already_default" });
  });

  it("ships a GitHub agent's winner as a pull request", async () => {
    const fix = fixtureFixRun("fix-ready-github");
    const { result } = renderHook(() => useShipCandidate(fix), {
      wrapper: makeWrapper(),
    });
    let outcome;
    await act(async () => {
      outcome = await result.current.mutateAsync(winner(fix).id);
    });
    const url = "https://github.com/acme/dispatch-agent/pull/42";
    expect(outcome).toEqual({ kind: "pull_request", url });
    await waitFor(() =>
      expect(winner(fixtureFixRun("fix-ready-github")).pull_request_url).toBe(
        url,
      ),
    );
  });

  it("returns a unified diff for a candidate", async () => {
    const diff = await fetchCandidatePatch(
      "fix-ready-archive",
      "fix-ready-archive-c3",
    );
    expect(diff).toMatch(/^diff --git /);
    expect(diff).toMatch(/^\+/m);
  });

  it("names the patch file", () => {
    expect(patchFileName("f1", 2)).toBe("fix-f1-attempt-2.patch");
  });
});

describe("fixErrorOf", () => {
  it("maps fix_candidate_not_activated to its message", () => {
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
  });

  it("reads an axios-style response too", () => {
    expect(
      fixErrorOf({
        response: {
          status: 422,
          data: {
            error: "fix_candidate_not_proven",
            message: "Not the winner",
          },
        },
      }).code,
    ).toBe("fix_candidate_not_proven");
  });

  it.each([
    "fix_github_not_connected",
    "fix_github_commit_unknown",
    "github_repository_invalid",
  ])(
    "offers the patch on %s",
    (code) => {
      expect(
        fixErrorOf({ statusCode: 422, error: code, message: "m" }).offerPatch,
      ).toBe(true);
    },
  );

  it("names an entitlement 402", () => {
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
  });

  it("names a usage 402", () => {
    expect(
      fixErrorOf({
        statusCode: 402,
        error_code: "usage_limit",
        dimension: "credits",
        message: "Credit limit reached",
      }),
    ).toEqual({
      code: "usage",
      message: "Credit limit reached",
      offerPatch: false,
    });
  });
});
