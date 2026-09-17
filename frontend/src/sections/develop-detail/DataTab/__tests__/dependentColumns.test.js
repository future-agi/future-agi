import { describe, expect, it, vi } from "vitest";
import {
  findDependentColumns,
  getColumnDependencyIds,
  rerunDependentColumnsInOrder,
  toggleDependentColumnSelection,
  waitForColumnCompletion,
} from "../dependentColumns";

const source = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "source",
  origin_type: "OTHERS",
  metadata: {},
};

const anotherSource = {
  id: "22222222-2222-2222-2222-222222222222",
  name: "another",
  origin_type: "OTHERS",
  metadata: {},
};

const dynamicColumn = (originType, metadata, overrides = {}) => ({
  id: overrides.id ?? `${originType}-id`,
  name: overrides.name ?? originType,
  origin_type: originType,
  metadata,
  status: overrides.status ?? "Completed",
});

describe("getColumnDependencyIds", () => {
  const allColumns = [source, anotherSource];

  it.each([
    ["classification", { column_id: source.id }],
    ["extracted_entities", { column_id: source.id }],
    ["extracted_json", { column_id: source.id }],
    ["vector_db", { column_id: source.id, query_key: "vector" }],
  ])("detects the %s source column", (originType, metadata) => {
    expect(
      getColumnDependencyIds(dynamicColumn(originType, metadata), allColumns),
    ).toEqual([source.id]);
  });

  it("accepts an exact legacy vector query_key column reference", () => {
    expect(
      getColumnDependencyIds(
        dynamicColumn("vector_db", {
          column_id: anotherSource.id,
          query_key: source.id,
        }),
        allColumns,
      ),
    ).toEqual([anotherSource.id, source.id]);
  });

  it("detects API call UUID templates without matching unrelated text", () => {
    const column = dynamicColumn("api_call", {
      url: `https://example.com/{{${source.id}.slug}}`,
      params: {
        direct: { type: "Variable", value: source.id },
        ignored: { type: "PlainText", value: source.id },
      },
      headers: {
        nested: { type: "Variable", value: `{{${anotherSource.id}[0]}}` },
      },
      body: {
        selected: `{{${source.id}}}`,
        nestedObjectIsNotResolvedByRuntime: {
          value: `{{${anotherSource.id}}}`,
        },
      },
      note: `prefix-${anotherSource.id}-suffix`,
    });

    expect(getColumnDependencyIds(column, allColumns)).toEqual([
      source.id,
      anotherSource.id,
    ]);
  });

  it("detects explicit Python kwargs access by column name", () => {
    const column = dynamicColumn("python_code", {
      code: `def main(**kwargs):
    first = kwargs.get("source")
    second = kwargs['another']
    return first or second
`,
    });

    expect(getColumnDependencyIds(column, allColumns)).toEqual([
      source.id,
      anotherSource.id,
    ]);
  });

  it("detects conditional condition and nested operation references", () => {
    const column = dynamicColumn("conditional", {
      config: [
        {
          condition: `{{${source.id}}} == "yes"`,
          branch_node_config: {
            type: "api_call",
            config: {
              config: {
                url: `https://example.com/{{${anotherSource.id}}}`,
                params: {},
                headers: {},
                body: {},
              },
            },
          },
        },
      ],
    });

    expect(getColumnDependencyIds(column, allColumns)).toEqual([
      source.id,
      anotherSource.id,
    ]);
  });

  it("does not match a UUID embedded in arbitrary metadata text", () => {
    const column = dynamicColumn("api_call", {
      url: "https://example.com",
      params: {},
      headers: {},
      body: {},
      note: `unrelated-${source.id}-text`,
    });

    expect(getColumnDependencyIds(column, allColumns)).toEqual([]);
  });
});

describe("findDependentColumns", () => {
  it("returns direct and transitive dependents in execution order", () => {
    const direct = dynamicColumn(
      "classification",
      { column_id: source.id },
      { id: "direct", name: "Direct" },
    );
    const transitive = dynamicColumn(
      "extracted_json",
      { column_id: direct.id },
      { id: "transitive", name: "Transitive" },
    );

    expect(
      findDependentColumns(source.id, [source, transitive, direct]),
    ).toEqual([
      {
        id: direct.id,
        name: "Direct",
        operationType: "classify",
        dependencyIds: [source.id],
      },
      {
        id: transitive.id,
        name: "Transitive",
        operationType: "extract_json",
        dependencyIds: [direct.id],
      },
    ]);
  });

  it("topologically orders dependents regardless of the dataset column order", () => {
    const upstream = dynamicColumn(
      "classification",
      { column_id: source.id },
      { id: "upstream", name: "Upstream" },
    );
    const downstream = dynamicColumn(
      "api_call",
      {
        url: `https://example.com/{{${source.id}}}/{{${upstream.id}}}`,
        params: {},
        headers: {},
        body: {},
      },
      { id: "downstream", name: "Downstream" },
    );

    expect(
      findDependentColumns(source.id, [source, downstream, upstream]).map(
        (column) => column.id,
      ),
    ).toEqual(["upstream", "downstream"]);
  });
});

describe("toggleDependentColumnSelection", () => {
  const dependentColumns = [
    { id: "direct", dependencyIds: [source.id] },
    { id: "transitive", dependencyIds: ["direct"] },
  ];

  it("removes downstream columns when an upstream dependency is deselected", () => {
    expect(
      toggleDependentColumnSelection({
        columnId: "direct",
        selectedIds: ["direct", "transitive"],
        dependentColumns,
      }),
    ).toEqual([]);
  });

  it("selects required upstream columns with a downstream column", () => {
    expect(
      toggleDependentColumnSelection({
        columnId: "transitive",
        selectedIds: [],
        dependentColumns,
      }),
    ).toEqual(["direct", "transitive"]);
  });

  it("handles cyclic metadata without recursing forever", () => {
    const cyclicColumns = [
      { id: "first", dependencyIds: ["second"] },
      { id: "second", dependencyIds: ["first"] },
    ];

    expect(
      toggleDependentColumnSelection({
        columnId: "first",
        selectedIds: [],
        dependentColumns: cyclicColumns,
      }),
    ).toEqual(["first", "second"]);
  });
});

describe("waitForColumnCompletion", () => {
  it("polls until the column is completed", async () => {
    const fetchColumns = vi
      .fn()
      .mockResolvedValueOnce([{ ...source, status: "Running" }])
      .mockResolvedValueOnce([{ ...source, status: "Completed" }]);
    const wait = vi.fn().mockResolvedValue(undefined);

    await expect(
      waitForColumnCompletion({
        columnId: source.id,
        fetchColumns,
        wait,
        pollInterval: 1,
      }),
    ).resolves.toMatchObject({ status: "Completed" });
    expect(fetchColumns).toHaveBeenCalledTimes(2);
    expect(wait).toHaveBeenCalledWith(1);
  });

  it("stops when the column fails", async () => {
    await expect(
      waitForColumnCompletion({
        columnId: source.id,
        fetchColumns: vi.fn().mockResolvedValue([
          {
            ...source,
            status: "Failed",
          },
        ]),
      }),
    ).rejects.toThrow("finished with status Failed");
  });
});

describe("rerunDependentColumnsInOrder", () => {
  it("waits for the source and each dependent before starting the next", async () => {
    const events = [];
    const statuses = new Map([
      [source.id, ["Running", "Completed"]],
      ["direct", ["Completed"]],
      ["transitive", ["Completed"]],
    ]);
    const fetchColumns = vi.fn(async () =>
      [...statuses.entries()].map(([id, values]) => {
        const status = values.length > 1 ? values.shift() : values[0];
        events.push(`status:${id}:${status}`);
        return { id, name: id, status };
      }),
    );
    const rerunColumn = vi.fn(async (column) => {
      events.push(`rerun:${column.id}`);
      statuses.set(column.id, ["Running", "Completed"]);
    });

    await rerunDependentColumnsInOrder({
      sourceColumnId: source.id,
      dependentColumns: [{ id: "direct" }, { id: "transitive" }],
      fetchColumns,
      rerunColumn,
      waitOptions: { wait: vi.fn().mockResolvedValue(undefined) },
    });

    expect(rerunColumn.mock.calls.map(([column]) => column.id)).toEqual([
      "direct",
      "transitive",
    ]);
    expect(
      events.indexOf("status:11111111-1111-1111-1111-111111111111:Completed"),
    ).toBeLessThan(events.indexOf("rerun:direct"));
    const directRerunIndex = events.indexOf("rerun:direct");
    const directCompletionIndex = events.indexOf(
      "status:direct:Completed",
      directRerunIndex,
    );
    expect(directRerunIndex).toBeLessThan(directCompletionIndex);
    expect(directCompletionIndex).toBeLessThan(
      events.indexOf("rerun:transitive"),
    );
  });
});
