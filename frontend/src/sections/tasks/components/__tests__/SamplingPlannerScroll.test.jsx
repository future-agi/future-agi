import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, userEvent } from "src/utils/test-utils";
import SamplingPrecisionPlanner from "../SamplingPrecisionPlanner";
import { defaultSamplingPolicy } from "../samplingPolicyForm";
import { productionTheme, rgb, contrast } from "./samplingContrastTestUtils";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
describe.each(["light", "dark"])("planner scroll region in %s", (mode) => {
  it("is named, focusable, has a contrasting focus-visible outline and an overflow-only hint", async () => {
    let notify;
    const disconnect = vi.fn();
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(callback) {
          notify = callback;
        }
        observe() {}
        disconnect() {
          disconnect();
        }
      },
    );
    let overflows = false;
    vi.spyOn(HTMLElement.prototype, "scrollWidth", "get").mockImplementation(
      () => (overflows ? 900 : 500),
    );
    vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(500);
    const theme = productionTheme(mode);
    const view = render(
      <SamplingPrecisionPlanner
        policy={defaultSamplingPolicy()}
        preview={{
          strata: [
            { stratum: '["default",[]]', eligible_units: 2, selected_units: 1 },
          ],
        }}
        onApply={vi.fn()}
      />,
      { theme },
    );
    await userEvent.click(screen.getByText("Plan sample size"));
    const region = screen.getByRole("region", {
      name: "Planner results, scrolls horizontally",
    });
    expect(region).toHaveAttribute("tabindex", "0");
    expect(region).toHaveStyle({ overflowX: "auto" });
    expect(region).toContainElement(
      screen.getByRole("table", { name: "Precision planning by group" }),
    );
    expect(
      screen.queryByText("Scroll horizontally to see all planner columns."),
    ).not.toBeInTheDocument();
    overflows = true;
    act(() => notify());
    const hint = screen.getByText(
      "Scroll horizontally to see all planner columns.",
    );
    expect(region).toHaveAccessibleDescription(hint.textContent);
    region.focus();
    expect(region).toHaveFocus();
    const rules = [...document.styleSheets]
      .flatMap((sheet) => [...sheet.cssRules])
      .filter(
        (rule) =>
          rule.selectorText?.includes(":focus-visible") &&
          [...region.classList].some((name) =>
            rule.selectorText.includes(`.${name}:focus-visible`),
          ),
      );
    expect(rules.length).toBeGreaterThan(0);
    const style = rules.at(-1).style;
    expect(style.outline).toContain("2px solid");
    expect(
      contrast(
        rgb(style.outline.replace("2px solid ", "")),
        rgb(theme.palette.background.paper),
      ),
    ).toBeGreaterThanOrEqual(3);
    overflows = false;
    act(() => notify());
    expect(screen.queryByText(hint.textContent)).not.toBeInTheDocument();
    view.unmount();
    expect(disconnect).toHaveBeenCalled();
  });
});
