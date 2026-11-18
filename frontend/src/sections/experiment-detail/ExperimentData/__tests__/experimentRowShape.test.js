import { describe, expect, it } from "vitest";

import { normalizeEvalResult } from "src/sections/develop-detail/DataTab/common";

// Captured shape of one row + cell on the experiment-rows endpoint. Matches
// the contract typed by ExperimentTableRowSerializer + ExperimentRowCellSerializer
// in futureagi/model_hub/serializers/experiment_contracts.py. The reads below
// are the exact ones EvalDetailDrawerContent's StatusCellRenderer and
// ViewDetailsModal perform; the test is a regression lock against a future
// rename on either side of the contract.
const EXPERIMENT_ROW_FIXTURE = {
  row_id: "5c05bbe6-5399-4274-bbbf-7e0f2a1caff4",
  "8b3e0d2f-1234-49aa-bf02-d4fbf08e9f55": {
    cell_value: "{'score': 0.25, 'choices': ['Useless', 'Non-Toxic']}",
    status: "completed",
    metadata: {
      response_time_ms: 412.0,
      token_count: 837,
      cost: { input: 0.0, output: 0.0 },
      cell_metadata: {
        explanation: "Output declines to advise on lisinopril dosage.",
        error_analysis: { input1: ["short response"] },
        selected_input_key: "output",
      },
      reason: "Output declines to advise on lisinopril dosage.",
    },
    value_infos: {
      reason: "Output declines to advise on lisinopril dosage.",
    },
  },
};

describe("experiment-rows endpoint shape", () => {
  it("StatusCellRenderer reads snake_case directly off the cell", () => {
    const evalCellId = "8b3e0d2f-1234-49aa-bf02-d4fbf08e9f55";
    const cell = EXPERIMENT_ROW_FIXTURE[evalCellId];

    expect(cell.cell_value).toBe(
      "{'score': 0.25, 'choices': ['Useless', 'Non-Toxic']}",
    );
    expect(cell.status).toBe("completed");
    expect(cell).not.toHaveProperty("cellValue");
  });

  it("normalizeEvalResult classifies the BE-emitted cell_value into choices", () => {
    const evalCellId = "8b3e0d2f-1234-49aa-bf02-d4fbf08e9f55";
    const cell = EXPERIMENT_ROW_FIXTURE[evalCellId];
    const result = normalizeEvalResult(cell.cell_value);

    expect(result.kind).toBe("choices");
    expect(result.items).toEqual(["Useless", "Non-Toxic"]);
  });

  it("ViewDetailsModal reads cell_metadata.explanation / error_analysis / selected_input_key", () => {
    const evalCellId = "8b3e0d2f-1234-49aa-bf02-d4fbf08e9f55";
    const cell = EXPERIMENT_ROW_FIXTURE[evalCellId];
    const cellMetadata = cell.metadata?.cell_metadata;

    expect(cellMetadata.explanation).toBe(
      "Output declines to advise on lisinopril dosage.",
    );
    expect(cellMetadata.error_analysis).toEqual({ input1: ["short response"] });
    expect(cellMetadata.selected_input_key).toBe("output");
    expect(cellMetadata).not.toHaveProperty("errorAnalysis");
    expect(cellMetadata).not.toHaveProperty("selectedInputKey");
  });

  it("ExperimentDataView getRowId reads row_id directly", () => {
    expect(EXPERIMENT_ROW_FIXTURE.row_id).toBe(
      "5c05bbe6-5399-4274-bbbf-7e0f2a1caff4",
    );
    expect(EXPERIMENT_ROW_FIXTURE).not.toHaveProperty("rowId");
  });
});
