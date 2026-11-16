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

  it("shows each problem of a refused save under the limit it names, never an API key", () => {
    renderLimits({
      error: {
        statusCode: 400,
        error: "limits_invalid",
        message: "Two limits are not valid",
        problems: [
          { field: "max_active", message: "A whole number above 0" },
          { field: "model", message: "Pick one of small, large" },
          { field: null, message: "Limits could not be read" },
        ],
      },
    });
    expect(field("How many at once on a call")).toHaveAccessibleDescription(
      "A whole number above 0",
    );
    expect(field("Model actors are played on")).toHaveAccessibleDescription(
      "Pick one of small, large",
    );
    // A problem about no one limit shows once, above Save.
    expect(screen.getByText("Limits could not be read")).toBeInTheDocument();
    expect(screen.queryByText("Two limits are not valid")).toBeNull();
    expect(screen.queryByText(/max_active/)).toBeNull();
  });

  it("shows any other refusal in the server's words", () => {
    renderLimits({
      error: {
        statusCode: 409,
        error: "draft_changed",
        message: "The actors were edited elsewhere",
      },
    });
    expect(
      screen.getByText("The actors were edited elsewhere"),
    ).toBeInTheDocument();
  });

  it("shows what a limit left empty takes, as the server says, and nothing for one with no default", () => {
    renderLimits();
    expect(field("How many at once on a call")).toHaveAttribute(
      "placeholder",
      "Default: 2",
    );
    expect(field("Caller takes back after agent turns")).not.toHaveAttribute(
      "placeholder",
    );
  });

  it("keeps what was typed when the stored limits change under it", () => {
    const { rerender } = renderLimits({ limits: { max_messages: 20 } });
    type("How many at once on a call", "3");
    rerender(
      <ActorLimits
        limits={{ max_messages: 40 }}
        fields={LIMIT_FIELDS}
        models={["small", "large"]}
        onSave={vi.fn()}
      />,
    );
    expect(field("How many at once on a call")).toHaveValue("3");
    expect(
      field("Most messages in a conversation before the run"),
    ).toHaveValue("40");
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
