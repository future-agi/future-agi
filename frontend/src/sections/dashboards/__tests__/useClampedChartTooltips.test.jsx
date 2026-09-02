import React, { useRef, useState } from "react";
import { describe, it, expect } from "vitest";
import { act, render, waitFor } from "src/utils/test-utils";
import useClampedChartTooltips from "../hooks/useClampedChartTooltips";

// Apex writes the tooltip's position straight onto the style attribute, so the
// tests drive that attribute rather than a React prop.
function Harness({ mountChartImmediately = true }) {
  const containerRef = useRef(null);
  const [chartMounted, setChartMounted] = useState(mountChartImmediately);
  useClampedChartTooltips(containerRef);
  return (
    <div>
      <button onClick={() => setChartMounted(true)}>render chart</button>
      {chartMounted && (
        <div ref={containerRef} data-testid="container">
          <div className="apexcharts-tooltip" data-testid="tooltip" />
        </div>
      )}
    </div>
  );
}

const setTop = (el, top) => act(() => { el.style.top = top; });

describe("useClampedChartTooltips", () => {
  it("pulls a tooltip drawn above the canvas back to the top edge", async () => {
    const { getByTestId } = render(<Harness />);
    const tooltip = getByTestId("tooltip");

    setTop(tooltip, "-84.86px");

    await waitFor(() => expect(tooltip.style.top).toBe("0px"));
  });

  it("leaves a tooltip that already fits following the cursor", async () => {
    const { getByTestId } = render(<Harness />);
    const tooltip = getByTestId("tooltip");

    setTop(tooltip, "120px");

    // Give the observer a chance to fire before asserting it did nothing.
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(tooltip.style.top).toBe("120px");
  });

  it("clamps a chart that only mounts once its query resolves", async () => {
    // The widget editor renders its chart inside a branch that is empty until
    // data arrives, so the ref is null when the effect first runs. An observer
    // attached to the ref at mount would watch nothing for the widget's life.
    const { getByText, getByTestId } = render(
      <Harness mountChartImmediately={false} />,
    );

    act(() => getByText("render chart").click());
    const tooltip = getByTestId("tooltip");
    setTop(tooltip, "-40px");

    await waitFor(() => expect(tooltip.style.top).toBe("0px"));
  });

  it("ignores tooltips belonging to a different chart", async () => {
    const { getByTestId } = render(<Harness />);
    const stray = document.createElement("div");
    stray.className = "apexcharts-tooltip";
    stray.style.top = "-50px";
    document.body.appendChild(stray);

    setTop(getByTestId("tooltip"), "-10px");
    await waitFor(() => expect(getByTestId("tooltip").style.top).toBe("0px"));

    expect(stray.style.top).toBe("-50px");
    stray.remove();
  });
});
