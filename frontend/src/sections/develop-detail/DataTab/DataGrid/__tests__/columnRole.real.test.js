// L4 (grid:column-menu, grid:add-column-static): a data column's role is a
// real backend field. edit_dataset_behavior and add_static_column store it,
// get-dataset-table returns it, so the column menu offers "Set role" on every
// dataset grid (not only the versioned one) and the API contract knows the
// field and its values.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { columnActionMenuItems } from "../../common";
import { columnRole } from "../dataGridView";
import {
  validateContractedRequestConfig,
  validateContractedResponse,
} from "src/api/contracts/openapi-contract";

const { put } = vi.hoisted(() => ({
  put: vi.fn(() => Promise.resolve({ data: { status: true } })),
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, put } };
});
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const DATASET = "0b7c5a52-6f0e-4a8e-9a43-2a1f0e1d3c11";
const COLUMN = "6d1b1f7e-3c55-4a2b-8e4b-1f6a9c2d7e01";
const queryClient = {
  setQueriesData: vi.fn(),
  setQueryData: vi.fn(),
  invalidateQueries: vi.fn(),
};
const dataColumn = {
  id: COLUMN,
  name: "expected_answer",
  originType: "OTHERS",
  role: null,
};
const setRole = (items) => items.find((i) => i?.name === "Set role");

describe("L4 column role: Set role is a real write", () => {
  beforeEach(() => {
    put.mockClear();
    queryClient.setQueriesData.mockClear();
  });

  it("offers Set role on a classic (not versioned) dataset grid", () => {
    const items = columnActionMenuItems(dataColumn, false, {
      datasetId: DATASET,
      queryClient,
    });
    const menu = setRole(items);
    expect(menu).toBeTruthy();
    expect(menu.subMenu.map((i) => i.name)).toEqual([
      "Input",
      "Expected output",
      "Metadata",
      "None",
    ]);
    expect(menu.subMenu.find((i) => i.name === "None").checked).toBe(true);
  });

  it("is not offered without a dataset, on generated columns or to viewers", () => {
    expect(
      setRole(columnActionMenuItems(dataColumn, false, { queryClient })),
    ).toBeUndefined();
    expect(
      setRole(
        columnActionMenuItems(
          { ...dataColumn, originType: "run_prompt" },
          false,
          { datasetId: DATASET, queryClient },
        ),
      ),
    ).toBeUndefined();
    expect(
      setRole(
        columnActionMenuItems(dataColumn, true, {
          datasetId: DATASET,
          queryClient,
        }),
      ),
    ).toBeUndefined();
  });

  it("sends the role edit_dataset_behavior stores, and 'none' to clear it", async () => {
    const menu = setRole(
      columnActionMenuItems(dataColumn, false, {
        datasetId: DATASET,
        queryClient,
      }),
    );
    await menu.subMenu.find((i) => i.name === "Expected output").action();
    expect(put).toHaveBeenLastCalledWith(
      expect.stringContaining(
        `/model-hub/develops/${DATASET}/edit_dataset_behavior/`,
      ),
      { column_config: { [COLUMN]: { role: "expected" } } },
    );
    await menu.subMenu.find((i) => i.name === "None").action();
    expect(put).toHaveBeenLastCalledWith(
      expect.stringContaining("/edit_dataset_behavior/"),
      { column_config: { [COLUMN]: { role: "none" } } },
    );
  });

  it("marks the stored role as checked", () => {
    const menu = setRole(
      columnActionMenuItems({ ...dataColumn, role: "metadata" }, false, {
        datasetId: DATASET,
        queryClient,
      }),
    );
    expect(menu.subMenu.filter((i) => i.checked).map((i) => i.name)).toEqual([
      "Metadata",
    ]);
  });
});

describe("L4 column role: the API contract carries it", () => {
  it("add_static_column accepts the roles the backend stores, and no other", () => {
    const body = (role) => ({
      url: `/model-hub/develops/${DATASET}/add_static_column/`,
      method: "post",
      data: { new_column_name: "expected_answer", column_type: "text", role },
    });
    for (const role of ["input", "expected", "metadata", "none", null]) {
      expect(validateContractedRequestConfig(body(role)).ok).toBe(true);
    }
    expect(validateContractedRequestConfig(body("judge")).ok).toBe(false);
  });

  it("get-dataset-table's column carries a role (null for none)", () => {
    const column = (role) => ({
      id: COLUMN,
      name: "expected_answer",
      data_type: "text",
      is_visible: true,
      is_frozen: null,
      source_type: "text",
      origin_type: "OTHERS",
      source_id: null,
      order_index: 0,
      status: null,
      average_score: null,
      reason_column: false,
      is_numeric_eval: false,
      is_numeric_eval_percentage: false,
      eval_tag: [],
      metadata: {},
      choices_map: {},
      role,
    });
    const response = (role) => ({
      config: {
        url: `/model-hub/develops/${DATASET}/get-dataset-table/`,
        method: "get",
      },
      status: 200,
      data: { status: true, result: { column_config: [column(role)] } },
    });
    expect(validateContractedResponse(response("expected")).ok).toBe(true);
    expect(validateContractedResponse(response(null)).ok).toBe(true);
    expect(validateContractedResponse(response("judge")).ok).toBe(false);
  });

  it("the header reads the stored role, null for none", () => {
    expect(columnRole({ ...dataColumn, role: "expected" })).toBe("expected");
    expect(columnRole({ ...dataColumn, role: null })).toBeNull();
    expect(columnRole({ ...dataColumn, role: "none" })).toBeNull();
  });
});
