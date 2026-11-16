import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import ActorLimits from "../ActorLimits";
import { LIMIT_FIELDS } from "./castFixtures";

const field = (label) => screen.getByLabelText(label);
const type = (label, value) =>
  fireEvent.change(field(label), { target: { value } });
const renderLimits = (props) =>
  render(
    <ActorLimits
      limits={{}}
      fields={LIMIT_FIELDS}
      models={["small", "large"]}
      onSave={vi.fn()}
      {...props}
    />,
  );

describe("ActorLimits", () => {
  it("shows one field per limit the cast lists, and the model when it lists one", () => {
    renderLimits({ fields: LIMIT_FIELDS.slice(0, 2) });
    expect(field("How many at once on a call")).toBeInTheDocument();
    expect(field("Caller takes back after agent turns")).toBeInTheDocument();
    expect(screen.queryByLabelText("Seconds of actors per call")).toBeNull();
    expect(screen.queryByLabelText("Model actors are played on")).toBeNull();
  });

  it("sends a limit that is not a number as typed, so the server's refusal names it", () => {
    const onSave = vi.fn();
    renderLimits({ onSave });
    type("How many at once on a call", "two");
    type("Most turns each actor takes during a run", "2.5");
    fireEvent.click(
      screen.getByRole("button", { name: "Save limits to draft" }),
    );
    expect(onSave.mock.calls[0][0]).toEqual({
      max_active: "two",
      live_turns_per_actor: 2.5,
    });
  });

  it("shows the server's refusal of a save", () => {
    renderLimits({
      error: {
        statusCode: 400,
        error: "limits_invalid",
        message: "max_active must be a whole number above 0",
      },
    });
    expect(
      screen.getByText("max_active must be a whole number above 0"),
    ).toBeInTheDocument();
  });

  it("saves the whole block: what was stored, what was typed, and none as no limit", () => {
    const onSave = vi.fn();
    renderLimits({ limits: { max_messages: 200 }, onSave });
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
