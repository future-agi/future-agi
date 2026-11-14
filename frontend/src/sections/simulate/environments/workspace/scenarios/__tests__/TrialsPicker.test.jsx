import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import TrialsPicker from "../TrialsPicker";

const openPicker = async (user, trials) => {
  render(<TrialsPicker trials={trials} onChange={vi.fn()} scenarioCount={2} />);
  await user.click(screen.getByText(`Repeats: ${trials}`));
};

describe("TrialsPicker — which value is chosen", () => {
  it("marks the preset in use", async () => {
    const user = userEvent.setup();
    await openPicker(user, 3);

    expect(
      screen.getByRole("menuitemradio", { name: /^3×/, checked: true }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("menuitemradio", { name: /Custom/, checked: false }),
    ).toBeInTheDocument();
  });

  // A value outside the presets was set through Custom, so Custom shows it
  // instead of leaving every row unmarked.
  it("marks Custom, with its value, when no preset matches", async () => {
    const user = userEvent.setup();
    await openPicker(user, 20);

    expect(
      screen.getByRole("menuitemradio", { name: /Custom.*20×/, checked: true }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("menuitemradio", { checked: true, name: /^1×/ }),
    ).not.toBeInTheDocument();
  });
});
