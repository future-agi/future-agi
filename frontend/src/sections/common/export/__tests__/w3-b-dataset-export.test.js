// W3-6 B2: dataset export rows follow the column roles, generated columns
// carry their reasons, the OpenAI formats find their columns, and the table
// reads are paged until every row is in.
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get },
  endpoints: {
    develop: {
      getDatasetDetail: (id) => `/model-hub/develops/${id}/get-dataset-table/`,
      versions: {
        table: (id, v) => `/model-hub/datasets/${id}/versions/${v}/table/`,
      },
    },
  },
}));

const {
  datasetFile,
  datasetRecords,
  defaultMessageRoles,
  fetchDatasetRows,
  formatBlocker,
  generatedColumnsOf,
  userColumnsOf,
} = await import("../datasetExport");

const COLUMNS = [
  {
    id: "c1",
    name: "customer_message",
    data_type: "text",
    origin_type: "OTHERS",
    role: "input",
  },
  {
    id: "c2",
    name: "conversation",
    data_type: "json",
    origin_type: "OTHERS",
    role: "input",
  },
  {
    id: "c3",
    name: "expected_reply",
    data_type: "text",
    origin_type: "OTHERS",
    role: "expected",
  },
  {
    id: "c4",
    name: "expected_tool_calls",
    data_type: "json",
    origin_type: "OTHERS",
    role: "expected",
  },
  {
    id: "c5",
    name: "customer_tier",
    data_type: "text",
    origin_type: "OTHERS",
    role: "metadata",
  },
  {
    id: "c6",
    name: "Draft reply (gpt-4o-mini)",
    data_type: "text",
    origin_type: "run_prompt",
    role: "output",
  },
  {
    id: "c7",
    name: "Correctness",
    data_type: "boolean",
    origin_type: "evaluation",
    role: "eval",
    source_id: "m1",
  },
  {
    id: "c7r",
    name: "Correctness-reason",
    data_type: "text",
    origin_type: "evaluation_reason",
    role: null,
    source_id: "m1",
  },
];
const cell = (v, extra = {}) => ({ cell_value: v, ...extra });
const ROWS = [
  {
    row_id: "r1",
    order: 0,
    c1: cell("Where is A-1?"),
    c2: cell(
      JSON.stringify([
        { role: "user", content: "hi" },
        { role: "assistant", content: "hello" },
      ]),
    ),
    c3: cell("It ships Thursday."),
    c4: cell(
      JSON.stringify([{ name: "lookup_order", args: { order_id: "A-1" } }]),
    ),
    c5: cell("pro"),
    c6: cell("Ships soon"),
    c7: cell("Failed", { value_infos: { reason: "No date given." } }),
    c7r: cell("No date given."),
    row_meta: { split_ids: ["S1"], metadata: { ticket_id: "T-1" } },
  },
  {
    row_id: "r2",
    order: 1,
    c1: cell("Refund?"),
    c2: cell("[]"),
    c3: cell(""),
    c4: cell("[]"),
    c5: cell("free"),
    c6: cell("No"),
    c7: cell("Passed"),
    c7r: cell("Fine."),
    row_meta: { split_ids: [], metadata: {} },
  },
];

beforeEach(() => {
  mocks.get.mockReset();
});

describe("W3-6 B2 · dataset export model", () => {
  it("splits user and generated columns; an eval's reason column rides with it", () => {
    expect(userColumnsOf(COLUMNS).map((c) => c.id)).toEqual([
      "c1",
      "c2",
      "c3",
      "c4",
      "c5",
    ]);
    expect(generatedColumnsOf(COLUMNS).map((c) => c.id)).toEqual(["c6", "c7"]);
  });

  it("JSON records group fields by role; CSV columns are flat with eval reasons", () => {
    const { records, flat, flatRows } = datasetRecords({
      columns: COLUMNS,
      rows: ROWS,
      splitNames: { S1: "smoke" },
      generated: true,
      rowMetadata: true,
    });
    expect(records[0]).toEqual({
      row_id: "r1",
      row: 1,
      splits: ["smoke"],
      input: {
        customer_message: "Where is A-1?",
        conversation: [
          { role: "user", content: "hi" },
          { role: "assistant", content: "hello" },
        ],
      },
      expected: {
        expected_reply: "It ships Thursday.",
        expected_tool_calls: [
          { name: "lookup_order", args: { order_id: "A-1" } },
        ],
      },
      metadata: { customer_tier: "pro" },
      generated: {
        "Draft reply (gpt-4o-mini)": "Ships soon",
        // A boolean column (Passed / Failed cells) is typed in JSON (M3).
        Correctness: { value: false, reason: "No date given." },
      },
      row_metadata: { ticket_id: "T-1" },
    });
    expect(flat.map((c) => c.label)).toEqual([
      "row_id",
      "row",
      "splits",
      "customer_message",
      "conversation",
      "expected_reply",
      "expected_tool_calls",
      "customer_tier",
      "Draft reply (gpt-4o-mini)",
      "Correctness",
      "Correctness reason",
      "row_metadata.ticket_id",
    ]);
    expect(flatRows[1]["r:c7"]).toBe("Fine.");
    const off = datasetRecords({
      columns: COLUMNS,
      rows: ROWS,
      generated: false,
    });
    expect(off.records[0].generated).toBeUndefined();
    expect(off.flat.map((c) => c.label)).not.toContain("Correctness");
  });

  it("finds the message columns from the roles, and says what a format needs", () => {
    expect(defaultMessageRoles(COLUMNS, ROWS)).toEqual({
      user: "c1",
      turns: "c2",
      assistant: "c3",
      toolCalls: "c4",
    });
    expect(formatBlocker("openai_ft", { user: "c1", assistant: null })).toBe(
      "Needs an Expected column",
    );
    expect(formatBlocker("openai_evals", { user: null, assistant: "c3" })).toBe(
      "Needs an Input column",
    );
    expect(formatBlocker("csv", {})).toBeNull();
  });

  it("writes fine-tune lines from the cells (rows without an expected value left out)", () => {
    const f = datasetFile({
      format: "openai_ft",
      rows: ROWS,
      columns: COLUMNS,
      roles: { user: "c1", turns: "c2", assistant: "c3", toolCalls: null },
      system: "Be brief.",
      nameParts: ["Support agent - golden set", "v6", "smoke"],
    });
    const lines = f.data
      .trimEnd()
      .split("\n")
      .map((l) => JSON.parse(l));
    expect(lines).toHaveLength(1);
    expect(lines[0].messages.map((m) => m.role)).toEqual([
      "system",
      "user",
      "assistant",
      "user",
      "assistant",
    ]);
    expect(f.name).toBe("support-agent-golden-set-v6-smoke-finetune.jsonl");
  });

  it("pages the table read until every row is in, with the split filter", async () => {
    const all = Array.from({ length: 7 }, (_, i) => ({
      row_id: `r${i}`,
      order: i,
    }));
    mocks.get.mockImplementation(async (url, { params }) => {
      const page = params.current_page_index;
      const size = params.page_size;
      return {
        data: {
          result: {
            metadata: {
              total_rows: 7,
              dataset_name: "GS",
              has_more: (page + 1) * size < 7,
            },
            column_config: COLUMNS,
            table: all.slice(page * size, (page + 1) * size),
          },
        },
      };
    });
    const got = await fetchDatasetRows({ datasetId: "DS", splitIds: ["S1"] });
    expect(got.rows.map((r) => r.row_id)).toEqual(all.map((r) => r.row_id));
    expect(mocks.get).toHaveBeenCalledTimes(Math.ceil(7 / 500));
    const [url, { params }] = mocks.get.mock.calls[0];
    expect(url).toBe("/model-hub/develops/DS/get-dataset-table/");
    expect(JSON.parse(params.filters)).toEqual([
      {
        column_id: "system:splits",
        filter_config: {
          filter_type: "categorical",
          filter_op: "in",
          filter_value: ["S1"],
        },
      },
    ]);
  });

  it("an older version reads N3 and takes the column roles from the dataset", async () => {
    mocks.get.mockImplementation(async (url, { params }) => {
      if (params.column_config_only)
        return { data: { result: { column_config: COLUMNS } } };
      return {
        data: {
          result: {
            metadata: { total_rows: 1, has_more: false },
            column_config: COLUMNS.slice(0, 5).map(({ role: _r, ...c }) => c),
            table: [ROWS[0]],
          },
        },
      };
    });
    const got = await fetchDatasetRows({
      datasetId: "DS",
      version: { id: "V3", number: 3 },
      isLatest: false,
    });
    expect(mocks.get.mock.calls[0][0]).toBe(
      "/model-hub/datasets/DS/versions/V3/table/",
    );
    expect(got.columns.map((c) => c.role)).toEqual([
      "input",
      "input",
      "expected",
      "expected",
      "metadata",
    ]);
  });
});
