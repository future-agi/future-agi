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

describe("TrialsPicker with a custom value out of range", () => {
  const openCustom = async (
    user,
    { scenarioCount = 3, onChange = vi.fn() } = {},
  ) => {
    render(
      <TrialsPicker
        trials={1}
        onChange={onChange}
        scenarioCount={scenarioCount}
      />,
    );
    await user.click(screen.getByText("Repeats: 1"));
    await user.click(screen.getByRole("menuitemradio", { name: /Custom/ }));
    const input = screen.getByRole("spinbutton", {
      name: "Repeats per scenario",
    });
    await user.clear(input);
    return input;
  };

  it("shows the allowed range while the value fits", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const input = await openCustom(user, { onChange });
    await user.type(input, "12");

    expect(screen.getByText("1 to 20 repeats")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Set" }));
    expect(onChange).toHaveBeenCalledWith(12);
  });

  it("says so and keeps Set off instead of changing 25 to 20", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const input = await openCustom(user, { onChange });
    await user.type(input, "25{Enter}");

    expect(screen.getByText("Up to 20 repeats")).toBeInTheDocument();
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("button", { name: "Set" })).toBeDisabled();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("refuses 0 rather than changing it to 1", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const input = await openCustom(user, { onChange });
    await user.type(input, "0{Enter}");

    expect(
      screen.getByText("Use a whole number from 1 to 20"),
    ).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("names the 200-run limit when the scenarios times the repeats pass it", async () => {
    const user = userEvent.setup();
    const input = await openCustom(user, { scenarioCount: 30 });
    await user.type(input, "8");

    expect(
      screen.getByText("Up to 200 runs at once: 30 × 8 is 240"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Set" })).toBeDisabled();
  });
});
