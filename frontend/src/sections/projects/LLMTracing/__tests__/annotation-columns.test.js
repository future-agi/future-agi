import { describe, expect, it } from "vitest";

import { generateAnnotationColumnsForTracing } from "../common";

describe("generateAnnotationColumnsForTracing", () => {
  it("keeps a text annotation label visible before it has responses", () => {
    const columns = generateAnnotationColumnsForTracing([
      {
        id: "label-text",
        name: "Writing assistance",
        groupBy: "Annotation Metrics",
        annotationLabelType: "text",
        settings: {},
        annotators: null,
      },
    ]);

    expect(columns).toHaveLength(1);
    expect(columns[0].headerName).toBe("Writing assistance");
    expect(columns[0].field).toBe("label-text");
    expect(columns[0].headerComponentParams).toMatchObject({
      displayName: "Writing assistance",
      metricId: "label-text",
      isTextType: true,
      showActions: false,
    });
    expect(columns[0].valueGetter({ data: {} })).toBeNull();
  });

  it("keeps text annotation response columns once annotators exist", () => {
    const columns = generateAnnotationColumnsForTracing([
      {
        id: "label-text",
        name: "Writing assistance",
        groupBy: "Annotation Metrics",
        annotationLabelType: "text",
        settings: {},
        annotators: {
          "user-1": {
            user_id: "user-1",
            user_name: "Kartik",
          },
        },
      },
    ]);

    expect(columns).toHaveLength(1);
    expect(columns[0].headerName).toBe("Kartik");
    expect(columns[0].field).toBe("label-text.annotators.user-1");
    expect(columns[0].headerComponentParams).toMatchObject({
      displayName: "Writing assistance",
      metricId: "label-text",
      isTextType: true,
      subLabel: "Kartik",
      subLabelType: "person",
    });
  });
});

// The span list sends each annotation cell as the bare value of one score
// (OBS-SW-191: a saved span text score still showed "-" in the Spans grid).
const label = (id, annotationLabelType, annotators = null) => ({
  id,
  name: id,
  groupBy: "Annotation Metrics",
  annotationLabelType,
  settings: {},
  annotators,
});
const ANNOTATORS = { "user-1": { user_id: "user-1", user_name: "Kartik" } };
const cell = (column, value) =>
  column.valueGetter({
    data: value === undefined ? {} : { [column.field]: value },
  });

describe("generateAnnotationColumnsForTracing with span-list values", () => {
  it("shows a span text score on a label that had no annotators", () => {
    const [column] = generateAnnotationColumnsForTracing([
      label("label-text", "text"),
    ]);

    expect(cell(column, "final note")).toBe("final note");
  });

  it("keeps a text label as one column reading the span value", () => {
    const columns = generateAnnotationColumnsForTracing(
      [label("label-text", "text", ANNOTATORS)],
      [],
      { perAnnotator: false },
    );

    expect(columns).toHaveLength(1);
    expect(columns[0].field).toBe("label-text");
    expect(columns[0].headerComponentParams).toMatchObject({
      metricId: "label-text",
      isTextType: true,
    });
    expect(cell(columns[0], "final note")).toBe("final note");
  });

  it("reads flat numeric, thumbs and categorical scores, including 0 and false", () => {
    const [numeric, thumbs, categorical] = generateAnnotationColumnsForTracing([
      label("label-num", "numeric"),
      label("label-thumbs", "thumbs_up_down"),
      label("label-cat", "categorical"),
    ]);

    expect(cell(numeric, 4)).toBe(4);
    expect(cell(numeric, 0)).toBe(0);
    expect(cell(thumbs, true)).toBe("up");
    expect(cell(thumbs, false)).toBe("down");
    expect(cell(categorical, ["a", "b"])).toEqual({ a: 1, b: 1 });
  });

  it("expands a span label to its value column only", () => {
    const columns = generateAnnotationColumnsForTracing(
      [label("label-num", "numeric", ANNOTATORS)],
      ["label-num"],
      { perAnnotator: false },
    );

    expect(columns).toHaveLength(1);
    expect(columns[0].valueGetter({ data: { "label-num": 4 } })).toBe(4);
  });

  it("still reads trace aggregates and per-annotator values", () => {
    const [numeric, thumbs] = generateAnnotationColumnsForTracing([
      label("label-num", "numeric"),
      label("label-thumbs", "thumbs_up_down"),
    ]);
    const expanded = generateAnnotationColumnsForTracing(
      [label("label-num", "numeric", ANNOTATORS)],
      ["label-num"],
    );
    const traceRow = {
      "label-num": { score: 3, annotators: { "user-1": { score: 4 } } },
    };

    expect(cell(numeric, { score: 3, annotators: {} })).toBe(3);
    expect(
      cell(thumbs, { thumbs_up: 2, thumbs_down: 1, annotators: {} }),
    ).toEqual({ thumbs_up: 2, thumbs_down: 1 });
    expect(cell(numeric, undefined)).toBeNull();
    expect(expanded).toHaveLength(2);
    expect(expanded[0].valueGetter({ data: traceRow })).toBe(3);
    expect(expanded[1].valueGetter({ data: traceRow })).toBe(4);
  });
});
