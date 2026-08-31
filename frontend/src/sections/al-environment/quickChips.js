/**
 * The suggestions above the composer, worked out from what the session already has.
 *
 * These are not decoration: "next stage →" sends an *empty* message, which is how the
 * harness is told to advance without being given new instructions. Without it the only way
 * forward is the roadmap, which calls a different endpoint and means something else.
 */
export const quickChips = (status) => {
  const have = status?.have || {};
  const scenarios = have.scenarios || 0;
  const chips = [];

  if (!status?.agent) {
    chips.push({
      label: "test the drive_thru example",
      say: "i want to test my drive thru voice agent, the code is in src/fi/alk",
    });
  }

  if (have.world && !scenarios) {
    chips.push({ label: "write 5 hard scenarios", say: "write 5 hard scenarios for this agent" });
  }

  if (scenarios > 0 && status?.stage === "run") {
    chips.push({
      label: "what can be run?",
      say: "which scenarios are there, and have any been run?",
    });
    chips.push({
      label: "run one live call",
      say: "run the first scenario that has not been run yet",
    });
  } else if (scenarios > 0) {
    chips.push({ label: `run all ${scenarios} against the world`, run: "" });
  }

  if (status?.stage && status?.agent) {
    // An empty message is the harness's own way of saying "carry on".
    chips.push({ label: "next stage →", say: "" });
  }

  return chips;
};
