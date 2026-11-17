import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen, within } from "src/utils/test-utils";

import ManagedRefusalAlert from "../../components/ManagedRefusalAlert";
import { classify } from "../managedRefusals";

// Gateway > Models has no control that turns Future AGI
// credits on, so the Try it refusal must not send anyone there.
describe("403 future_agi_credits_off remedy", () => {
  it("names the real way to turn credits on, not a Gateway > Models control", () => {
    render(
      <ManagedRefusalAlert
        refusal={classify(403, "future_agi_credits_off")}
        status={403}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(
      within(alert).getByText("Future AGI credits are off"),
    ).toBeInTheDocument();
    expect(alert).toHaveTextContent(
      /ask an Owner or Admin to contact your Future AGI account team/,
    );
    expect(alert).toHaveTextContent(/use your own provider key/i);
    expect(alert).not.toHaveTextContent(/Gateway > Models/);
    expect(alert).not.toHaveTextContent(/"Future AGI credits" control/);
    expect(within(alert).queryByRole("link")).toBeNull();
    expect(within(alert).queryByRole("button")).toBeNull();
  });
});
