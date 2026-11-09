import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ScopePolicySummary from "../ScopePolicySummary";

describe("saved-policy review wording", () => {
  it("shows scoring even when trials and concurrency are not available", () => {
    render(
      <ScopePolicySummary
        values={{ errorPolicy: "exclude", promptConfig: [] }}
      />,
    );
    expect(screen.getByText("Errors excluded from scores")).toBeInTheDocument();
    expect(screen.queryByText(/concurrency/)).not.toBeInTheDocument();
  });

  it("explains the backend message counting and timeout boundary", () => {
    render(
      <ScopePolicySummary
        values={{
          promptConfig: [{ rowLimits: { messages: 1, timeout_s: 5 } }],
        }}
      />,
    );
    expect(screen.getByText("Errors count as 0 in scores")).toBeInTheDocument();
    expect(
      screen.getByText(/every input message plus the reserved response/),
    ).toBeInTheDocument();
    expect(screen.getByText(/not interrupted/)).toBeInTheDocument();
    expect(
      screen.getByText(/Counts shown before creation are requested counts/),
    ).toBeInTheDocument();
  });

  it("does not present a mixed policy as one saved limit", () => {
    render(
      <ScopePolicySummary
        values={{
          promptConfig: [
            { rowLimits: { tokens: 10 } },
            { rowLimits: { tokens: 20 } },
          ],
        }}
      />,
    );
    expect(screen.queryByText(/Limits per row ·/)).not.toBeInTheDocument();
  });
});
