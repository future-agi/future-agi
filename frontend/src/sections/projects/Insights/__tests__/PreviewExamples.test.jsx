import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import PreviewExamples from "../PreviewExamples";

vi.mock("src/components/iconify", () => ({ default: () => null }));

const unit = (id, facets, included) => ({
  unit_id: id,
  label: `Session ${id}`,
  facets: { summary: `Summary ${id}`, lens_answer: "none", ...facets },
  included,
});

describe("PreviewExamples", () => {
  it("says why each example would be left out, and shows the quotes", () => {
    render(
      <PreviewExamples
        lens="failures"
        units={[
          unit(
            "a",
            {
              lens_answer: "Agent gave a delivery time with no lookup shown",
              failure_present: false,
              unbacked_failure: "no_contradiction",
            },
            false,
          ),
          unit("b", { failure_present: false }, false),
          unit(
            "c",
            {
              lens_answer: "Agent said a declined refund went through",
              failure_present: true,
              evidence: [{ quote: "Done. I've refunded $61.20" }],
            },
            true,
          ),
        ]}
      />,
    );
    expect(
      screen.getByText("Named a failure the conversation doesn't show"),
    ).toBeInTheDocument();
    expect(screen.getByText("No agent failure found")).toBeInTheDocument();
    expect(
      screen.getByText(/Done\. I've refunded \$61\.20/),
    ).toBeInTheDocument();
  });
});
