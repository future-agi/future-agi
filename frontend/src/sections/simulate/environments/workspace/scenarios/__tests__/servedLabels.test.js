import { describe, expect, it } from "vitest";
import { withServedLabels } from "../scenarioEditor.constants";

describe("withServedLabels", () => {
  it("names actor choices from the field's own labels, not the level labels", () => {
    const [callActors, attack] = withServedLabels(
      [
        {
          value: "actors",
          choices: ["friend", "none"],
          choice_labels: { friend: "Rowan", none: "Simulated user only" },
        },
        { value: "coverage.overlay", choices: ["none"] },
      ],
      { none: "None" },
    );
    expect(callActors.choiceLabels).toEqual({
      friend: "Rowan",
      none: "Simulated user only",
    });
    // "none" means no attack here, so the two fields must not share one label.
    expect(attack.choiceLabels).toEqual({ none: "None" });
  });

  it("says how many scenarios include each actor choice", () => {
    const [callActors] = withServedLabels([
      {
        value: "actors",
        choices: ["none", "friend"],
        choice_labels: { friend: "Rowan", none: "Simulated user only" },
        counts: { none: 6, friend: 2 },
      },
    ]);
    expect(callActors.choiceLabels).toEqual({
      friend: "Rowan (2)",
      none: "Simulated user only (6)",
    });
  });
});
