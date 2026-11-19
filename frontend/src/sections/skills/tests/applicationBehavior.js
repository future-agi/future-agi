// Reserved fixture read by the hosted application run. The picker writes it so a
// reviewer never types a skill identity or the JSON envelope by hand.
export const BEHAVIOR_FIXTURE = "fi.application.expectations";
export const BEHAVIOR_ACTIONS = [
  ["issue_refund", "Issue a refund"],
  ["open_chargeback", "Open a chargeback dispute"],
  ["track_shipment", "Track the shipment"],
  ["none", "No business action"],
];
export const readBehavior = (fixtures) => {
  const item = fixtures.find((fixture) => fixture.name === BEHAVIOR_FIXTURE);
  try {
    const parsed = JSON.parse(item?.text ?? "");
    return { skillId: parsed.skill_id || "", action: parsed.action || "" };
  } catch {
    return { skillId: "", action: "" };
  }
};
export const behaviorPartial = (behavior) =>
  Boolean(behavior.skillId) !== Boolean(behavior.action);
/** Fixture list with the reserved expectation fixture set (or removed when incomplete). */
export const withBehavior = (fixtures, behavior, newId) => {
  const others = fixtures.filter((fixture) => fixture.name !== BEHAVIOR_FIXTURE);
  const existing = fixtures.find((fixture) => fixture.name === BEHAVIOR_FIXTURE);
  if (!behavior.skillId || !behavior.action) return others;
  return [
    ...others,
    {
      id: existing?.id || newId(),
      name: BEHAVIOR_FIXTURE,
      text: JSON.stringify({ schema: 1, skill_id: behavior.skillId, action: behavior.action }),
    },
  ];
};
