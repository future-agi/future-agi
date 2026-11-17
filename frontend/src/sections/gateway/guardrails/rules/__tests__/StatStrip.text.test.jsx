// A figure and its rate read as two values ("43 7.3%"), never run together
// ("437.3%", or "00.0%" on a fresh organization).
import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";

import StatStrip from "../StatStrip";

describe("StatStrip: a value and its detail", () => {
  it("separates the value from its detail in the text", () => {
    render(
      <StatStrip
        label="Last 7 days"
        items={[{ label: "Matched", value: "43", detail: "7.3%" }]}
      />,
    );
    const figure = screen.getByText("Matched").nextElementSibling;
    expect(figure.textContent).toBe("43 7.3%");
  });
});
