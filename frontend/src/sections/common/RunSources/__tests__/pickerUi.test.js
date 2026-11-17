import { describe, expect, it } from "vitest";
import { discardBody, usePickerUi } from "../pickerUi";

describe("IT-R2 G1 P1 · discard confirm and layers", () => {
  it("names the draft run and what it holds", () => {
    expect(discardBody({ names: ["LangGraph"], simulated: true })).toBe(
      "Your LangGraph run and its simulated results will be lost.",
    );
    expect(discardBody({ names: ["Claude Code"], tried: true })).toBe(
      "Your Claude Code run and its 1-row try will be lost.",
    );
    expect(discardBody({ names: ["Claude Code", "Codex CLI"] })).toBe(
      "Your 2 runs (Claude Code, Codex CLI) will be lost.",
    );
    expect(discardBody({ names: [] })).toBe("Closing discards your changes.");
  });

  it("layers count up and down, never below zero", () => {
    const st = usePickerUi.getState();
    st.pushLayer();
    expect(usePickerUi.getState().layers).toBe(1);
    st.popLayer();
    st.popLayer();
    expect(usePickerUi.getState().layers).toBe(0);
  });
});
