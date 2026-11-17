// The Summary's Fails on: a run with nothing to count (no evaluation with a
// Pass/fail result) keeps its card, every link to its errored rows and its
// cause line, with `—` where a figure was and its tasks named as errored.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "src/utils/test-utils";
import { useMatrix, useRunConfigs } from "src/api/develop/r2";
import { useRootCauses } from "src/sections/experiment-detail/matrix/useMatrixData";
import { SUCCESS_NEEDS_PASS_FAIL } from "src/sections/common/Runs/runNumbers";
import FailsOnCards from "../FailsOnCards";
import { failsOnModel } from "../failsOnModel";

vi.mock("src/api/develop/r2", async (importOriginal) => ({
  ...(await importOriginal()),
  useMatrix: vi.fn(),
  useRunConfigs: vi.fn(),
}));
vi.mock(
  "src/sections/experiment-detail/matrix/useMatrixData",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useRootCauses: vi.fn(),
  }),
);
// The section is drawn open, with its summary as text.
vi.mock("src/sections/common/ux/oneview/Section", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ summary, children }) =>
      createElement(
        "div",
        null,
        createElement("p", { "data-testid": "summary" }, summary),
        children,
      ),
  };
});
// The tooltip's text is drawn next to what it wraps, so a test can read it.
vi.mock("src/components/tooltip", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ title, children }) =>
      createElement(
        "span",
        null,
        children,
        createElement("span", { "data-testid": "tip" }, title),
      ),
  };
});

const row = (id, n, cells) => ({
  row_id: id,
  n,
  label: `T${n}`,
  group_values: { category: "refund" },
  cells,
});
// `a` has nothing to count and errored on every try of both rows.
const MATRIX = {
  rows: [
    row("r1", 1, { a: { trials: ["error", "error"] } }),
    row("r2", 2, { a: { trials: ["error", "error"] } }),
  ],
  columns: { a: { label: "#1", trials: 2, pass_fail_evals: 0 } },
};
const CAUSE = { run_id: "a", title: "Times out at the tool step" };
const pillHref = (pill) =>
  `/rows?label=${encodeURIComponent(pill.label)}&ids=${pill.ids.join(",")}`;

beforeEach(() => {
  useMatrix.mockReturnValue({ data: MATRIX });
  useRunConfigs.mockReturnValue({ data: { results: [] } });
  useRootCauses.mockReturnValue({ data: { causes: [CAUSE] } });
});
afterEach(cleanup);

const mount = () =>
  render(
    <FailsOnCards
      datasetId="d1"
      runIds={["a"]}
      baselineId={null}
      pillHref={pillHref}
    />,
  );

describe("Fails on for a run with nothing to count", () => {
  it("keeps its errored rows in the model, failing none", () => {
    const [a] = failsOnModel(MATRIX, { runIds: ["a"] });
    expect(a.failing).toBe(0);
    expect(a.errored).toEqual({
      ids: ["r1", "r2"],
      categories: [{ name: "refund", count: 2, rowIds: ["r1", "r2"] }],
      tasks: [
        { id: "r1", label: "T1", n: 1 },
        { id: "r2", label: "T2", n: 2 },
      ],
    });
  });

  it("keeps its card with every link and its cause line", () => {
    mount();
    const card = screen.getByTestId("fails-on-a");
    const total = within(card).getByTestId("fails-on-total");
    expect(total).toHaveTextContent("—");
    expect(total.getAttribute("href")).toBe(
      "/rows?label=%231%20errored&ids=r1,r2",
    );
    const links = within(card)
      .getAllByRole("link")
      .map((l) => [l.textContent, l.getAttribute("href")]);
    expect(links).toEqual([
      ["—", "/rows?label=%231%20errored&ids=r1,r2"],
      ["—", "/rows?label=%231%20errored%20%C2%B7%20refund&ids=r1,r2"],
      ["T1", "/rows?label=%231%20%C2%B7%20T1&ids=r1"],
      ["T2", "/rows?label=%231%20%C2%B7%20T2&ids=r2"],
    ]);
    expect(card).toHaveTextContent("Errored: T1 · T2");
    expect(card).toHaveTextContent(CAUSE.title);
  });

  it("says what Success needs on its count and in the section's line", () => {
    mount();
    const card = screen.getByTestId("fails-on-a");
    expect(within(card).getByTestId("tip")).toHaveTextContent(
      SUCCESS_NEEDS_PASS_FAIL,
    );
    expect(screen.getByTestId("summary")).toHaveTextContent(
      SUCCESS_NEEDS_PASS_FAIL,
    );
    expect(card).not.toHaveTextContent("Passes least");
  });
});

describe("Fails on for a run with a figure", () => {
  it("reads as before", () => {
    // Green at the base.
    useMatrix.mockReturnValue({
      data: {
        rows: [
          row("r1", 1, { b: { trials: [true, false] } }),
          row("r2", 2, { b: { trials: [true, false] } }),
        ],
        columns: { b: { label: "#2", trials: 2, pass_fail_evals: 1 } },
      },
    });
    render(
      <FailsOnCards
        datasetId="d1"
        runIds={["b"]}
        baselineId={null}
        pillHref={pillHref}
      />,
    );
    expect(screen.getByTestId("fails-on-total")).toHaveTextContent(
      "2 tasks of 2",
    );
    expect(screen.getByTestId("summary")).toHaveTextContent(
      "#2 fails the most: 2 tasks of 2, mostly refund",
    );
    expect(screen.getByTestId("fails-on-b")).toHaveTextContent(
      "Passes least: T1 1/2 · T2 1/2",
    );
  });
});
