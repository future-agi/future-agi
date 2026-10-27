import { describe, expect, it } from "vitest";
import { withServedLabels } from "../scenarioEditor.constants";

describe("withServedLabels", () => {
  it("names sub-actor choices from the field's own labels, not the level labels", () => {
    const [subActors, attack] = withServedLabels(
      [
        {
          value: "sub_actors",
          choices: ["friend", "none"],
          choice_labels: { friend: "Rowan", none: "Simulated user only" },
        },
        { value: "coverage.overlay", choices: ["none"] },
      ],
      { none: "None" },
    );
    expect(subActors.choiceLabels).toEqual({
      friend: "Rowan",
      none: "Simulated user only",
    });
    // "none" means no attack here, so the two fields must not share one label.
    expect(attack.choiceLabels).toEqual({ none: "None" });
  });
});
