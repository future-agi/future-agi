import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import Section from "./Section";

describe("Section intrinsic attributes", () => {
  it("preserves IDs, accessible roles and pointer callbacks on passive panels", () => {
    const enter = vi.fn();
    const leave = vi.fn();
    render(
      <Section
        variant="panel"
        id="citation-7"
        role="tooltip"
        aria-label="Evidence"
        onMouseEnter={enter}
        onMouseLeave={leave}
      >
        Quoted source
      </Section>,
    );
    const panel = screen.getByRole("tooltip", { name: "Evidence" });
    expect(panel).toHaveAttribute("id", "citation-7");
    fireEvent.mouseEnter(panel);
    fireEvent.mouseLeave(panel);
    expect(enter).toHaveBeenCalledOnce();
    expect(leave).toHaveBeenCalledOnce();
  });

  it("keeps clickable role and keyboard activation authoritative", () => {
    const click = vi.fn();
    render(
      <Section
        onClick={click}
        role="region"
        tabIndex={-1}
        aria-label="Open details"
      >
        Details
      </Section>,
    );
    const panel = screen.getByRole("button", { name: "Open details" });
    expect(panel).toHaveAttribute("tabindex", "0");
    fireEvent.keyDown(panel, { key: "Enter" });
    fireEvent.keyDown(panel, { key: " " });
    expect(click).toHaveBeenCalledTimes(2);
  });

  it("does not activate a disabled clickable panel", () => {
    const click = vi.fn();
    render(
      <Section onClick={click} disabled aria-label="Unavailable">
        Details
      </Section>,
    );
    const panel = screen.getByRole("button", { name: "Unavailable" });
    expect(panel).toHaveAttribute("tabindex", "-1");
    expect(panel).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(panel);
    fireEvent.keyDown(panel, { key: "Enter" });
    fireEvent.keyDown(panel, { key: " " });
    expect(click).not.toHaveBeenCalled();
  });
});
