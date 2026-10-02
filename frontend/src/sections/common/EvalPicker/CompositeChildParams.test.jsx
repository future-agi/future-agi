import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";
import CompositeChildParams from "./CompositeChildParams";

describe("CompositeChildParams", () => {
  it("renders nothing when params is undefined", () => {
    const { container } = render(<CompositeChildParams />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when params is an empty object", () => {
    const { container } = render(<CompositeChildParams params={{}} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders the Parameters label and one row per param", () => {
    render(
      <CompositeChildParams
        params={{
          gap_threshold_ms: "3000",
          silence_threshold: 0.01,
        }}
      />,
    );
    expect(screen.getByText("Parameters")).toBeInTheDocument();
    expect(screen.getByText("gap_threshold_ms")).toBeInTheDocument();
    expect(screen.getByText("3000")).toBeInTheDocument();
    expect(screen.getByText("silence_threshold")).toBeInTheDocument();
    expect(screen.getByText("0.01")).toBeInTheDocument();
  });

  it("stringifies non-string scalar values", () => {
    render(<CompositeChildParams params={{ enabled: true, count: 20 }} />);
    expect(screen.getByText("true")).toBeInTheDocument();
    expect(screen.getByText("20")).toBeInTheDocument();
  });

  it("renders object and array values as JSON", () => {
    render(
      <CompositeChildParams
        params={{
          weights: { a: 1 },
          labels: ["x", "y"],
        }}
      />,
    );
    expect(screen.getByText('{"a":1}')).toBeInTheDocument();
    expect(screen.getByText('["x","y"]')).toBeInTheDocument();
  });
});
