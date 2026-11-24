import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import Field from "../Field";

describe("Field", () => {
  it("links its label to the input", () => {
    const onChange = vi.fn();
    render(<Field label="LiveKit URL" value="" onChange={onChange} />);

    const input = screen.getByLabelText("LiveKit URL");
    fireEvent.change(input, { target: { value: "wss://demo.livekit.cloud" } });

    expect(onChange).toHaveBeenCalledWith("wss://demo.livekit.cloud");
  });

  it("finds a required field by its label without the asterisk", () => {
    render(<Field label="Contact Number" required value="" />);

    expect(screen.getByRole("textbox", { name: "Contact Number" })).toBeRequired();
  });

  it("gives two fields with the same label their own inputs", () => {
    render(
      <>
        <Field label="API key" value="first" />
        <Field label="API key" value="second" />
      </>
    );

    const [first, second] = screen.getAllByLabelText("API key");
    expect(first).toHaveValue("first");
    expect(second).toHaveValue("second");
    expect(first.id).not.toBe(second.id);
  });

  it("uses the id it is given", () => {
    render(<Field id="system-prompt" label="System prompt" value="" />);

    expect(screen.getByLabelText("System prompt")).toHaveAttribute("id", "system-prompt");
  });

  it("describes the input with its helper or error line", () => {
    const { rerender } = render(<Field label="Agent name" value="" helper="As registered on LiveKit" />);
    expect(screen.getByLabelText("Agent name")).toHaveAccessibleDescription("As registered on LiveKit");

    rerender(<Field label="Agent name" value="" helper="As registered on LiveKit" error="Enter an agent name" />);
    expect(screen.getByLabelText("Agent name")).toHaveAccessibleDescription("Enter an agent name");
  });
});
