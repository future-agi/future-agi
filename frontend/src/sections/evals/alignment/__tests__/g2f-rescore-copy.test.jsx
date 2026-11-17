import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, screen } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import SourceReport from "../SourceReport";

const storedReport = {
  numeric: false,
  n: 3,
  accuracy: 2 / 3,
  kappa: 0.4,
  labels: ["Pass", "Fail"],
  matrix: [
    [1, 0],
    [1, 1],
  ],
  per_label: [
    { label: "Pass", precision: 0.5, recall: 1, f1: 2 / 3, support: 1 },
    { label: "Fail", precision: 1, recall: 0.5, f1: 2 / 3, support: 2 },
  ],
};

const mount = (overrides = {}) =>
  render(<SourceReport report={{ ...storedReport, ...overrides }} />);

const storedNote = () => screen.getByTestId("w7-stored-note").textContent;

afterEach(cleanup);

describe("SourceReport stored-result provenance", () => {
  it.each([
    { state: "omitted", comparison: {} },
    { state: "null", comparison: { stored_vs_rescored: null } },
    { state: "undefined", comparison: { stored_vs_rescored: undefined } },
  ])(
    "does not infer rescore outcomes when the measurement is $state",
    ({ comparison }) => {
      mount(comparison);

      expect(storedNote()).toBe(
        "Stored verdicts, as the judge wrote them. No rescore comparison is available.",
      );
      expect(screen.getByTestId("w7-headline").textContent).toBe(
        "Accuracy 67% · κ 0.40 · n 3",
      );
      expect(screen.getByTestId("w7-agreement-matrix")).toBeTruthy();
      expect(screen.getByTestId("w7-per-label")).toBeTruthy();
    },
  );

  it("distinguishes an explicitly measured zero from a missing comparison", () => {
    mount({ stored_vs_rescored: 0 });

    expect(storedNote()).toBe(
      "Stored verdicts, as the judge wrote them. Reported rescore comparison: 0 of 3 verdicts differ.",
    );
  });

  it("describes the reported different count without predicting another rescore", () => {
    mount({ stored_vs_rescored: 2 });

    expect(storedNote()).toBe(
      "Stored verdicts, as the judge wrote them. Reported rescore comparison: 2 of 3 verdicts differ.",
    );
  });

  it.each([
    { mad: 0.4, formatted: "0.40" },
    { mad: 0, formatted: "0.00" },
  ])("labels MAD $mad as mean absolute difference", ({ mad, formatted }) => {
    mount({ numeric: true, spearman: 0.75, mad });

    expect(screen.getByTestId("w7-numeric-headline").textContent).toBe(
      `Spearman ρ 0.75 · mean absolute difference ${formatted} · n 3`,
    );
  });
});
