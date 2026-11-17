import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { createTheme, ThemeProvider } from "@mui/material/styles";
import { describe, expect, it, vi } from "vitest";

import VerticalNavItem from "../vertical/nav-item";
import MiniNavItem from "../mini/nav-item";
import HorizontalNavItem from "../horizontal/nav-item";

// Only decorative assets are stubbed. MUI, Tooltip and RouterLink stay real:
// mocking them would conceal the nested ButtonBase tab stop under test.
vi.mock("src/components/iconify", () => ({
  default: () => <svg aria-hidden="true" />,
}));

const variants = [
  ["vertical", VerticalNavItem],
  ["mini", MiniNavItem],
  ["horizontal", HorizontalNavItem],
];
const theme = createTheme();
const destination = "/nav-focus-target";

function Location() {
  return <output aria-label="Current route">{useLocation().pathname}</output>;
}

function mount(Item, props = {}) {
  return render(
    <MemoryRouter initialEntries={["/nav-focus-start"]}>
      <ThemeProvider theme={theme}>
        <button type="button">Before navigation</button>
        <nav aria-label="Test navigation">
          <Item
            title="Destination"
            path={destination}
            depth={1}
            icon={<svg aria-hidden="true" />}
            {...props}
          />
        </nav>
        <button type="button">After navigation</button>
        <Location />
      </ThemeProvider>
    </MemoryRouter>,
  );
}

function tabStops() {
  return [
    ...screen.getByRole("navigation").querySelectorAll(
      'a[href], button, input, select, textarea, [tabindex]',
    ),
  ].filter((node) => node.tabIndex >= 0 && !node.disabled);
}

async function traverseOneStop(user, expected) {
  await user.tab();
  expect(screen.getByRole("button", { name: "Before navigation" })).toHaveFocus();
  await user.tab();
  expect(expected).toHaveFocus();
  await user.tab();
  expect(screen.getByRole("button", { name: "After navigation" })).toHaveFocus();
}

describe.each(variants)("%s NavItem focus contract", (variant, Item) => {
  it.each([1, 2, 3])("has one tabbable leaf link at depth %s", async (depth) => {
    mount(Item, { depth });
    const link = screen.getByRole("link", { name: "Destination" });
    expect(tabStops()).toEqual([link]);
    expect(link).toHaveAttribute("href", destination);
    expect(link.querySelector('[role="button"]')).toBeNull();
    await traverseOneStop(userEvent.setup(), link);
  });

  it("keeps native Enter navigation on the only tab stop", async () => {
    mount(Item);
    const user = userEvent.setup();
    await user.tab();
    await user.tab();
    expect(screen.getByRole("link", { name: "Destination" })).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(screen.getByLabelText("Current route")).toHaveTextContent(destination);
  });

  it.each([
    ["Control", { ctrlKey: true }],
    ["Meta", { metaKey: true }],
    ["Shift", { shiftKey: true }],
    ["middle", { button: 1 }],
  ])("does not intercept %s activation of link content", (_name, init) => {
    mount(Item);
    const link = screen.getByRole("link", { name: "Destination" });
    let intercepted;
    // React has already handled the bubble by the time it reaches document.
    // Cancel afterwards solely to avoid jsdom's unimplemented page navigation.
    const observe = (event) => {
      intercepted = event.defaultPrevented;
      event.preventDefault();
    };
    const type = init.button === 1 ? "auxclick" : "click";
    document.addEventListener(type, observe, { once: true });
    fireEvent(link.firstElementChild, new MouseEvent(type, {
      bubbles: true, cancelable: true, ...init,
    }));
    expect(intercepted).toBe(false);
    expect(link).toHaveAttribute("href", destination);
    expect(screen.getByLabelText("Current route")).toHaveTextContent("/nav-focus-start");
  });

  it("keeps ordinary content clicks routed in the current tab", async () => {
    mount(Item);
    const link = screen.getByRole("link", { name: "Destination" });
    await userEvent.setup().click(link.firstElementChild);
    expect(screen.getByLabelText("Current route")).toHaveTextContent(destination);
  });

  it("keeps external links as one native new-tab link", async () => {
    mount(Item, { externalLink: true, path: "https://nav-focus.invalid/" });
    const link = screen.getByRole("link", { name: "Destination" });
    expect(tabStops()).toEqual([link]);
    expect(link).toHaveAttribute("href", "https://nav-focus.invalid/");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
    await traverseOneStop(userEvent.setup(), link);
  });

  it("places current-page state on the link, not its visual row", () => {
    mount(Item, { active: true, "aria-current": "page" });
    const link = screen.getByRole("link", { name: "Destination" });
    expect([...screen.getByRole("navigation").querySelectorAll('[aria-current="page"]')])
      .toEqual([link]);
  });

  it("keeps child branches to one control with their existing semantics", async () => {
    mount(Item, { hasChild: true, open: true, onClick: vi.fn() });
    const control = screen.getByRole(variant === "vertical" ? "button" : "link", {
      name: "Destination",
    });
    expect(tabStops()).toEqual([control]);
    if (variant === "vertical") {
      expect(control).toHaveAttribute("aria-expanded", "true");
      expect(screen.queryByRole("link")).not.toBeInTheDocument();
    } else {
      expect(control).toHaveAttribute("href", destination);
    }
    await traverseOneStop(userEvent.setup(), control);
  });

  it("activates a child branch with Enter using its actual consumer contract", async () => {
    const onClick = vi.fn();
    mount(Item, { hasChild: true, onClick });
    const user = userEvent.setup();
    await user.tab();
    await user.tab();
    await user.keyboard("{Enter}");
    if (variant === "vertical") {
      // vertical/nav-list.jsx passes onClick to expand/collapse children.
      expect(onClick).toHaveBeenCalledTimes(1);
      expect(screen.getByLabelText("Current route")).toHaveTextContent("/nav-focus-start");
    } else {
      // Mini/horizontal parents remain native links to their section route.
      expect(screen.getByLabelText("Current route")).toHaveTextContent(destination);
    }
  });

  it.each([false, true])("skips disabled items (hasChild=%s)", async (hasChild) => {
    mount(Item, { disabled: true, hasChild });
    expect(tabStops()).toEqual([]);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByRole("navigation").querySelector('[aria-disabled="true"]'))
      .not.toBeNull();
    const user = userEvent.setup();
    await user.tab();
    await user.tab();
    expect(screen.getByRole("button", { name: "After navigation" })).toHaveFocus();
    expect(screen.getByLabelText("Current route")).toHaveTextContent("/nav-focus-start");
  });

  it("does not create a tab stop for a role-hidden item", async () => {
    mount(Item, { roles: ["owner"], currentRole: "viewer" });
    expect(tabStops()).toEqual([]);
    const user = userEvent.setup();
    await user.tab();
    await user.tab();
    expect(screen.getByRole("button", { name: "After navigation" })).toHaveFocus();
  });
});
