import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("src/components/iconify", () => ({ default: () => null }));

import OutputMeta from "../OutputMeta";

// New Run Prompt metadata sends responseTime in milliseconds with
// responseTimeUnit "ms"; the chip keeps its "s" label, so a 0.59 s call reads
// "0.59s", never "587.98s". Runs saved before the unit marker stored seconds
// with no unit and still read as seconds.
describe("OutputMeta time chip", () => {
  it("shows a 587.98 ms call as 0.59s", () => {
    render(
      <OutputMeta
        metadata={{
          responseTime: 587.98,
          responseTimeUnit: "ms",
          cost: 0.00003,
          tokens: 12,
        }}
      />,
    );
    expect(screen.getByText("0.59s")).toBeTruthy();
    expect(screen.queryByText("587.98s")).toBeNull();
  });

  it("shows a 1234 ms call as 1.23s", () => {
    render(
      <OutputMeta
        metadata={{ responseTime: 1234, responseTimeUnit: "ms", tokens: 5 }}
      />,
    );
    expect(screen.getByText("1.23s")).toBeTruthy();
    expect(screen.queryByText("1234.00s")).toBeNull();
  });

  it("shows a saved run with no unit as seconds: 2.3 reads 2.30s", () => {
    render(<OutputMeta metadata={{ responseTime: 2.3, tokens: 5 }} />);
    expect(screen.getByText("2.30s")).toBeTruthy();
    expect(screen.queryByText("0.00s")).toBeNull();
  });

  it("reads each result in a mixed set by its own unit", () => {
    render(
      <>
        <OutputMeta metadata={{ responseTime: 2.3, tokens: 5 }} />
        <OutputMeta
          metadata={{ responseTime: 587.98, responseTimeUnit: "ms", tokens: 5 }}
        />
      </>,
    );
    expect(screen.getByText("2.30s")).toBeTruthy();
    expect(screen.getByText("0.59s")).toBeTruthy();
  });

  it("shows a 0 ms call as 0.00s", () => {
    render(
      <OutputMeta
        metadata={{ responseTime: 0, responseTimeUnit: "ms", tokens: 0 }}
      />,
    );
    expect(screen.getByText("0.00s")).toBeTruthy();
  });

  it("shows no time chip when the run has no response time", () => {
    render(<OutputMeta metadata={{ responseTime: undefined, tokens: 3 }} />);
    expect(screen.queryByText(/\d\.\d\ds$/)).toBeNull();
  });
});
