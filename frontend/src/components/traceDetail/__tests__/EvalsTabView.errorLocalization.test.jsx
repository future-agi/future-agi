import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";

vi.mock("../EvalErrorLocalization", () => ({
  default: () => <div data-testid="error-localization" />,
}));

import EvalsTabView from "../EvalsTabView";

const EVALS = [
  {
    id: "eval-1",
    eval_name: "tone_check",
    score: 20,
    explanation: "Too curt.",
    error_localizer: true,
    observation_span_id: "span-1",
    custom_eval_config_id: "config-1",
  },
];

describe("EvalsTabView error localization", () => {
  it("offers error localization on an expanded eval by default", async () => {
    const user = userEvent.setup();
    render(<EvalsTabView evals={EVALS} showAddEvals={false} />);

    await user.click(screen.getByText("tone_check"));

    expect(screen.getByTestId("error-localization")).toBeInTheDocument();
  });

  it("leaves it out when the host turns it off", async () => {
    const user = userEvent.setup();
    render(
      <EvalsTabView
        evals={EVALS}
        showAddEvals={false}
        showErrorLocalization={false}
      />,
    );

    await user.click(screen.getByText("tone_check"));

    expect(screen.getByText("Too curt.")).toBeInTheDocument();
    expect(screen.queryByTestId("error-localization")).toBeNull();
  });
});
