import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, within } from "src/utils/test-utils";
import RoutingConfigTab from "./RoutingConfigTab";

// Org config > Routing: each collapsible section (Routing Strategy, Complexity
// Routing, Provider Locking, ...) opens from the keyboard as well as the
// mouse, says whether it is open, and its icons keep their size at phone
// width.

vi.mock("../hooks/useAvailableModels", () => ({
  useAvailableModels: () => ["gpt-4o"],
}));
// The icon set is not loaded in tests: each icon stands in as a span that
// carries the flex-shrink it was given.
vi.mock("src/components/iconify", () => ({
  default: ({ sx }) => (
    <span
      data-testid="icon"
      data-flex-shrink={
        sx?.flexShrink === undefined ? "" : String(sx.flexShrink)
      }
    />
  ),
}));

const header = (name) => screen.getByRole("button", { name: new RegExp(name) });

describe("Routing tab sections", () => {
  it("are buttons that say whether they are open", () => {
    render(<RoutingConfigTab routing={{}} onChange={vi.fn()} />);
    expect(header("Routing Strategy")).toHaveAttribute("aria-expanded", "true");
    for (const name of ["Complexity Routing", "Provider Locking"]) {
      expect(header(name)).toHaveAttribute("aria-expanded", "false");
    }
  });

  it("open and close from the keyboard", async () => {
    const user = userEvent.setup();
    render(<RoutingConfigTab routing={{}} onChange={vi.fn()} />);
    const section = header("Provider Locking");
    section.focus();
    expect(section).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(section).toHaveAttribute("aria-expanded", "true");
    await user.keyboard(" ");
    expect(section).toHaveAttribute("aria-expanded", "false");
  });

  it("keep their icons from shrinking", () => {
    render(<RoutingConfigTab routing={{}} onChange={vi.fn()} />);
    const icons = within(header("Complexity Routing")).getAllByTestId("icon");
    expect(icons.map((icon) => icon.dataset.flexShrink)).toEqual(["0", "0"]);
    expect(
      within(header("Complexity Routing")).getByText("Complexity Routing"),
    ).toBeInTheDocument();
  });
});
