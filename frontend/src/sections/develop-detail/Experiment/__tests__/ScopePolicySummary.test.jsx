import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ScopePolicySummary from "../ScopePolicySummary";
import {
  TIME_LIMIT_HELP,
  UNAVAILABLE_ROW_LIMITS_TEXT,
} from "src/sections/common/RunSettings/rowLimitsModel";

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

  it("refuses the message limit and says when the time limit stops a row", () => {
    render(
      <ScopePolicySummary
        values={{
          promptConfig: [{ rowLimits: { messages: 1, timeout_s: 5 } }],
        }}
      />,
    );
    // No policy chosen: the default the server applies, not Count as 0.
    expect(
      screen.getByText(
        "Errors get no score and stay out of averages (default)",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Errors count as 0 in scores"),
    ).not.toBeInTheDocument();
    expect(screen.getByText(UNAVAILABLE_ROW_LIMITS_TEXT)).toBeInTheDocument();
    expect(screen.getByText(TIME_LIMIT_HELP)).toBeInTheDocument();
    expect(
      screen.getByText(/Counts shown before creation are requested counts/),
    ).toBeInTheDocument();
  });

  it("names Count as 0 only when the person chose it", () => {
    render(
      <ScopePolicySummary values={{ errorPolicy: "zero", promptConfig: [] }} />,
    );
    expect(screen.getByText("Errors count as 0 in scores")).toBeInTheDocument();
    expect(screen.queryByText(/\(default\)/)).not.toBeInTheDocument();
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
