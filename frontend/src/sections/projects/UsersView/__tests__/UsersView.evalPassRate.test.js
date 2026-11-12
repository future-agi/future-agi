import { createElement } from "react";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { getUsersColumnConfig, UsersFilterDefinition } from "../common";
import EvaluateCellRenderer from "../UserCellRenderers/EvaluateCellRenderer";

afterEach(cleanup);

const passRateColumn = (includeProject = false) =>
  getUsersColumnConfig({ includeProject }).find(
    (column) => column.field === "eval_score",
  );

// Match AG Grid's field fallback so the regression fails on a blank value,
// rather than only failing because the old column has no valueGetter.
const gridValue = (column, data) =>
  column.valueGetter
    ? column.valueGetter({ data, colDef: column })
    : data?.[column.field];

const renderPassRate = (column, data) =>
  render(
    createElement(column.cellRenderer, {
      value: gridValue(column, data),
      data,
      colDef: column,
    }),
  );

describe("Users evaluation pass-rate column", () => {
  it.each([
    [75, 0.75, "75%"],
    [100, 1, "100%"],
    [0, 0, "0%"],
    [1, 0.01, "1%"],
    [12.5, 0.125, "13%"],
  ])(
    "renders API pass rate %s at the existing float-renderer scale",
    (rate, score, label) => {
      for (const includeProject of [false, true]) {
        const column = passRateColumn(includeProject);
        const data = { bool_eval_pass_rate: rate };
        expect(gridValue(column, data)).toBe(score);
        const view = renderPassRate(column, data);
        expect(view.container.textContent).toBe(label);
        view.unmount();
      }
    },
  );

  it("keeps missing or nonnumeric rates undefined", () => {
    const column = passRateColumn();
    expect(column.valueGetter).toBeTypeOf("function");
    for (const data of [
      undefined,
      null,
      {},
      ...[
        undefined,
        null,
        "",
        "75",
        "invalid",
        false,
        true,
        NaN,
        Infinity,
        -Infinity,
        {},
        [],
      ].map((rate) => ({ bool_eval_pass_rate: rate })),
    ]) {
      expect(column.valueGetter({ data, colDef: column })).toBeUndefined();
    }
  });

  it("keeps unknown pass rates blank rather than showing zero", () => {
    const column = passRateColumn();
    expect(column.valueGetter).toBeTypeOf("function");
    for (const data of [{}, { bool_eval_pass_rate: null }]) {
      const view = renderPassRate(column, data);
      expect(view.container.textContent).toBe("");
      view.unmount();
    }
  });

  it("uses only bool_eval_pass_rate without mutating the API row", () => {
    const column = passRateColumn();
    const data = Object.freeze({
      bool_eval_pass_rate: 75,
      eval_score: 0.2,
      avg_output_float: 0.9,
    });
    expect(gridValue(column, data)).toBe(0.75);
    expect(data).toEqual({
      bool_eval_pass_rate: 75,
      eval_score: 0.2,
      avg_output_float: 0.9,
    });
    expect(
      gridValue(column, { eval_score: 0.2, avg_output_float: 0.9 }),
    ).toBeUndefined();
  });

  it("preserves eval_score identity and the existing float renderer", () => {
    for (const includeProject of [false, true]) {
      expect(passRateColumn(includeProject)).toMatchObject({
        field: "eval_score",
        headerName: "Evals Pass Rate (%)",
        hide: true,
        cellRenderer: EvaluateCellRenderer,
        cellRendererParams: { dataType: "float" },
      });
    }
    expect(
      UsersFilterDefinition().find(
        (filter) => filter.propertyId === "eval_score",
      ),
    ).toMatchObject({
      propertyName: "Evals Pass Rate (%)",
      propertyId: "eval_score",
      filterType: { type: "number" },
    });
  });
});
