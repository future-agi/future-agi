import { describe, it, expect } from "vitest";
import { blockedReason } from "../runBlocked";
import { ENV_STATUS } from "../../myEnvironments.constants";

const READY = { status: ENV_STATUS.READY, platform: { runTestId: "rt-1" } };
const STATE = { agent: { typeId: "retell" }, scenarios: [{ id: "a" }] };
// The Scenarios tab has no working Add, so an empty suite points at the
// builder chat, the one place that adds scenarios.
const NO_SCENARIOS = "This environment has no scenarios. Ask the builder in the chat to add some.";

describe("blockedReason", () => {
  it("reads the server scenario count over the seeded list", () => {
    expect(blockedReason({ agent: {}, scenarios: [] }, READY, true, false, 3)).not.toBe(
      NO_SCENARIOS,
    );
  });

  it("sends a built environment with no scenarios to the builder chat, not the Scenarios tab", () => {
    const reason = blockedReason({ agent: {}, scenarios: [] }, READY, true, false, 0);
    expect(reason).toBe(NO_SCENARIOS);
    expect(reason).not.toMatch(/Scenarios tab/);
  });

  it("says a built environment that isn't Ready hasn't finished building", () => {
    expect(blockedReason(STATE, { ...READY, status: ENV_STATUS.FAILED }, true)).toBe(
      "This environment hasn't finished building",
    );
  });

  it("never blames a missing agent on a built environment", () => {
    // The detail mapper always fills the agent in, so for a built env this
    // would be a guess; the next real reason wins instead.
    expect(blockedReason({ ...STATE, agent: null }, READY, true)).not.toMatch(/agent/i);
    expect(blockedReason({ agent: null, scenarios: [] }, READY, true)).toBe(NO_SCENARIOS);
  });

  it("still names a missing agent on a client-only environment", () => {
    expect(blockedReason({ ...STATE, agent: null }, {}, false)).toBe("No agent is connected to this environment yet");
  });

  it("says a run is starting while the request is in flight", () => {
    expect(blockedReason(STATE, READY, true, true)).toBe("Starting a run");
  });

  it("names a built environment with no linked run", () => {
    expect(blockedReason(STATE, { status: ENV_STATUS.READY, platform: {} }, true)).toMatch(/isn't linked to a run yet/);
  });
});
