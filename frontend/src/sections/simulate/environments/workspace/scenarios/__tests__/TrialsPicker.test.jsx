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

// W10-SIMULATE-OTHER-U04: a keyboard user has to be able to leave 1 repeat.
describe("TrialsPicker — keyboard", () => {
  it("opens on the chosen value and picks a preset with the arrow keys", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<TrialsPicker trials={1} onChange={onChange} scenarioCount={2} />);

    await user.tab();
    expect(screen.getByText("Repeats: 1")).toHaveFocus();
    await user.keyboard("{Enter}");

    expect(
      screen.getByRole("menu", { name: "Repeats per scenario" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("menuitemradio", { name: /^1×/, checked: true }),
    ).toHaveFocus();

    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitemradio", { name: /^3×/ })).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(onChange).toHaveBeenCalledWith(3);
  });

  it("selects the focused preset with Space", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<TrialsPicker trials={3} onChange={onChange} scenarioCount={2} />);
    await user.click(screen.getByText("Repeats: 3"));

    expect(
      screen.getByRole("menuitemradio", { name: /^3×/, checked: true }),
    ).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitemradio", { name: /^5×/ })).toHaveFocus();
    await user.keyboard(" ");
    expect(onChange).toHaveBeenCalledWith(5);
  });

  it("reaches Custom, types a value and sets it with Enter", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<TrialsPicker trials={3} onChange={onChange} scenarioCount={2} />);
    await user.click(screen.getByText("Repeats: 3"));

    await user.keyboard("{End}");
    expect(screen.getByRole("menuitemradio", { name: /Custom/ })).toHaveFocus();
    await user.keyboard("{Enter}");

    const field = screen.getByRole("spinbutton");
    expect(field).toHaveFocus();
    await user.clear(field);
    await user.keyboard("12{Enter}");
    expect(onChange).toHaveBeenCalledWith(12);
  });
});
