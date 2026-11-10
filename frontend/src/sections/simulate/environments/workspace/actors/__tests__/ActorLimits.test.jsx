import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import ActorLimits from "../ActorLimits";

const field = (label) => screen.getByLabelText(label);
const type = (label, value) =>
  fireEvent.change(field(label), { target: { value } });

describe("ActorLimits", () => {
  it("says what a limit takes once, and words a field only when it is wrong", () => {
    render(<ActorLimits limits={{}} onSave={vi.fn()} />);
    expect(screen.getByText(/none is no limit/)).toBeInTheDocument();
    expect(screen.queryByText("A number above 0, or none")).toBeNull();
    expect(screen.queryByText("A number, or none")).toBeNull();
    type("How many at once on a call", "two");
    expect(
      screen.getAllByText("A whole number above 0, or none"),
    ).toHaveLength(1);
    expect(
      screen.getByRole("button", { name: "Save limits to draft" }),
    ).toBeDisabled();
  });

  it("takes whole numbers for counts and a fraction only for seconds", () => {
    const onSave = vi.fn();
    render(<ActorLimits limits={{}} onSave={onSave} />);
    type("Most turns each actor takes during a run", "2.5");
    expect(
      screen.getAllByText("A whole number above 0, or none"),
    ).toHaveLength(1);
    expect(
      screen.getByRole("button", { name: "Save limits to draft" }),
    ).toBeDisabled();
    type("Most turns each actor takes during a run", "3");
    type("Seconds of actors per call", "12.5");
    fireEvent.click(
      screen.getByRole("button", { name: "Save limits to draft" }),
    );
    expect(onSave.mock.calls[0][0]).toEqual({
      live_turns_per_actor: 3,
      actor_seconds_per_call: 12.5,
    });
  });

  it("saves the whole block: what was stored, what was typed, and none as no limit", () => {
    const onSave = vi.fn();
    render(<ActorLimits limits={{ max_messages: 200 }} onSave={onSave} />);
    type("How many at once on a call", "none");
    type("Seconds of actors per call", "45");
    fireEvent.click(
      screen.getByRole("button", { name: "Save limits to draft" }),
    );
    expect(onSave.mock.calls[0][0]).toEqual({
      max_active: null,
      actor_seconds_per_call: 45,
      max_messages: 200,
    });
  });
});
