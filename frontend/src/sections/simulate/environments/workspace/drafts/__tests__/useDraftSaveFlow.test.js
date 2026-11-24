import { describe, expect, it, vi } from "vitest";
import { renderHook } from "src/utils/test-utils";

// The drafts answer the hook reads, set per test; every mutation is idle.
const state = {};
const idle = () => ({
  mutate: vi.fn(),
  reset: vi.fn(),
  isPending: false,
  isError: false,
  error: null,
});
vi.mock("src/api/simulate-environments/versions", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useDrafts: () => ({ data: actual.toDrafts(state.drafts) }),
    useDraftReview: () => ({ data: { review_digest: "d1", changes: [] } }),
    useSaveDrafts: idle,
    useDiscardDraft: idle,
    useValidateDrafts: idle,
    useSaveCandidates: idle,
  };
});

vi.mock(
  "src/api/simulate-environments/scenariosHooks",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useRunningScenarioValidation: () => ({ data: state.running }),
  }),
);

const { default: useDraftSaveFlow } = await import("../useDraftSaveFlow");

const ENV = { id: "env-1", versioning: { enabled: true } };
const draft = { id: "dv1", parent_id: "v1", revision: 3 };
const CANDIDATES = { environment_version_id: "c1", suite_version_id: null };

const flowFor = (validation, extra = {}) => {
  state.drafts = {
    environment: draft,
    validation_required: true,
    validation,
    ...extra,
  };
  return renderHook(() => useDraftSaveFlow(ENV)).result.current;
};

describe("useDraftSaveFlow", () => {
  it("saves directly when the server doesn't ask for Validate", () => {
    state.drafts = { environment: draft, validation_required: false };
    const flow = renderHook(() => useDraftSaveFlow(ENV)).result.current;
    expect(flow.needsValidate).toBe(false);
    expect(flow.pending).toBe(false);
  });

  it("says when scenarios are being validated, which Save waits for", () => {
    expect(flowFor(null).scenariosValidating).toBe(false);
    state.running = { job_id: "val-1" };
    expect(flowFor(null).scenariosValidating).toBe(true);
    state.running = undefined;
  });

  it("holds every button while the check runs", () => {
    const flow = flowFor({ state: "validating" });
    expect(flow.validating).toBe(true);
    expect(flow.pending).toBe(true);
    expect(flow.validationNote).toMatch(/Validating the changes/);
  });

  it("says the server's reason when the check failed for no scenario's sake", () => {
    const flow = flowFor({
      state: "failed",
      affected_scenarios: [],
      error: { code: "x", message: "We couldn't run this agent version." },
    });
    expect(flow.validationNote).toBe(
      "Validation failed: We couldn't run this agent version.",
    );
    // Without a reason it still doesn't count scenarios it has none of.
    expect(flowFor({ state: "failed" }).validationNote).toBe(
      "Validation failed. Validate again",
    );
  });

  it.each(["ready", "check_unavailable"])(
    "offers Save once the check ends %s",
    (end) => {
      const flow = flowFor({ state: end }, { candidates: CANDIDATES });
      expect(flow.canSaveValidated).toBe(true);
      expect(flow.pending).toBe(false);
    },
  );

  it.each([
    ["retry", /did not start/],
    ["superseded", /changed while validating/],
    ["outdated", /moved since/],
    ["unavailable", /isn't available/],
    ["failed", /Validation failed\. 1 scenario needs a change/],
  ])("offers Validate again with a reason after %s", (end, note) => {
    const flow = flowFor({ state: end, affected_scenarios: ["refund"] });
    expect(flow.canSaveValidated).toBe(false);
    expect(flow.validating).toBe(false);
    expect(flow.pending).toBe(false);
    expect(flow.validationNote).toMatch(note);
  });
});
