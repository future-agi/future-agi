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
