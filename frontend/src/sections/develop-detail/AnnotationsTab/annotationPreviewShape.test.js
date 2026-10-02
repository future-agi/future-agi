import { describe, expect, it } from "vitest";
import {
  normalizeAnnotationListRow,
  normalizeAnnotationPreviewData,
} from "./annotationPreviewShape";

describe("annotation preview API shape helpers", () => {
  it("maps canonical annotation list rows into the legacy grid shape", () => {
    const row = normalizeAnnotationListRow({
      id: "annotation-1",
      name: "Review answers",
      assigned_users: [{ id: "user-1", name: "Ada" }],
      labels: [{ id: "label-1", name: "Quality" }],
      lowest_unfinished_row: 3,
      summary: { completed: 2, total: 5 },
    });

    expect(row.assignedUsers).toEqual([{ id: "user-1", name: "Ada" }]);
    expect(row.lowestUnfinishedRow).toBe(3);
    expect(row.summary).toEqual({ completed: 2, total: 5 });
  });

  it("maps canonical annotate-row payloads into PreviewScreen field names", () => {
    const preview = normalizeAnnotationPreviewData({
      current_row_number: 1,
      total_rows: 2,
      first_row_order: 0,
      last_row_order: 1,
      next_row_order: 1,
      previous_row_order: null,
      static_fields: [
        {
          row_id: "row-1",
          column_id: "column-1",
          column_name: "Prompt",
          value: "Rate this answer",
          view: "default_open",
        },
      ],
      response_fields: [],
      label: [
        {
          row_id: "row-1",
          label_id: "label-1",
          label_name: "Quality",
          label_type: "categorical",
          label_settings: {
            options: [{ label: "Pass" }, { label: "Fail" }],
            auto_annotate: false,
            multi_choice: false,
          },
          column_id: "label-column-1",
          cell_value: ["Pass"],
          cell_description: "looks good",
          can_annotate: true,
        },
      ],
    });

    expect(preview.currentRowNumber).toBe(1);
    expect(preview.totalRows).toBe(2);
    expect(preview.nextRowOrder).toBe(1);
    expect(preview.staticFields[0]).toMatchObject({
      rowId: "row-1",
      columnId: "column-1",
      columnName: "Prompt",
    });
    expect(preview.label[0]).toMatchObject({
      labelId: "label-1",
      labelName: "Quality",
      labelType: "categorical",
      columnId: "label-column-1",
      cellValue: ["Pass"],
      cellDescription: "looks good",
      canAnnotate: true,
    });
    expect(preview.label[0].labelSettings).toMatchObject({
      autoAnnotate: false,
      multiChoice: false,
    });
  });
});
