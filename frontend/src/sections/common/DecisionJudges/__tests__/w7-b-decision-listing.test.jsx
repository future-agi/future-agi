// W7 B4 (decision-judge REVIEW should-fix 6 and 11): a failed listing says
// so and offers Try again (never "Add your key"); a Jev judge shows the PR's
// info line; the eval-path code of a key refusal is named.
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { render } from "src/utils/test-utils";

const state = { value: null };
vi.mock("../useDecisionModels", () => ({
  useDecisionModels: () => state.value,
}));

import DecisionModelsSection from "../DecisionModelsSection";
import DecisionCallPanel from "../DecisionCallPanel";
import { JEV_INFO_LINE, pickerRowsOf } from "../decisionModels";

describe("DecisionModelsSection when the listing fails", () => {
  beforeEach(() => {
    state.value = null;
  });

  it("shows Couldn't list decision models · Try again, and retries on click", () => {
    const refetch = vi.fn();
    state.value = { rows: [], isLoading: false, isError: true, refetch };
    render(<DecisionModelsSection onPick={() => {}} onAddKey={() => {}} />);
    const line = screen.getByTestId("decision-models-error");
    expect(line.textContent).toBe("Couldn't list decision models · Try again");
    expect(screen.queryByText(/Add .*key/i)).toBeNull();
    fireEvent.click(line);
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("lists the rows (union of the listing and the presets) when it works", () => {
    const rows = pickerRowsOf({
      data: [
        {
          id: "typesafe/jev-1.14.0",
          provider: "typesafe",
          format: "systemone",
        },
      ],
    });
    state.value = { rows, isLoading: false, isError: false, refetch: vi.fn() };
    render(<DecisionModelsSection onPick={() => {}} onAddKey={() => {}} />);
    expect(screen.queryByTestId("decision-models-error")).toBeNull();
    expect(
      screen.getByTestId("decision-model-typesafe/jev-1.14.0"),
    ).toBeTruthy();
    expect(
      screen.getByTestId("decision-model-typesafe/jev-1.13.0"),
    ).toBeTruthy();
  });
});

describe("DecisionCallPanel names the eval-path code of a key refusal", () => {
  it("JEV_KEY_MISSING for a missing key", () => {
    render(
      <DecisionCallPanel
        decision={{
          model: "typesafe/jev-1.13.0",
          error: {
            status: 403,
            code: "decision_provider_not_configured",
            message: "No TypeSafe key is saved for this workspace.",
          },
        }}
      />,
    );
    expect(screen.getByTestId("decision-eval-code").textContent).toMatch(
      /JEV_KEY_MISSING/,
    );
  });

  it("the Jev info line is the PR's words", () => {
    expect(JEV_INFO_LINE).toBe(
      "Jev returns typed decisions with probabilities; no written reasoning.",
    );
  });
});
