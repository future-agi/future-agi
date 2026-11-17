import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, waitFor } from "src/utils/test-utils";
import axios, { endpoints } from "src/utils/axios";
import DatasetTestMode from "../DatasetTestMode";

vi.mock("src/utils/axios", async (original) => {
  const actual = await original();
  return { ...actual, default: { get: vi.fn(), post: vi.fn() } };
});
vi.mock("../useMapToVariable", () => ({
  useMapToVariable: () => ({
    renderRowMapAction: () => null,
    mapMenu: null,
    rowHoverSx: {},
  }),
}));
vi.mock("../../hooks/useCompositeEval", () => ({
  useExecuteCompositeEvalAdhoc: () => ({ mutateAsync: vi.fn() }),
}));
vi.mock("../../hooks/useErrorLocalizerPoll", () => ({
  default: () => ({ state: {}, start: vi.fn() }),
}));
vi.mock("../EvalResultDisplay", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));

beforeEach(() => vi.clearAllMocks());

const cell = (value) => ({ cell_id: "cell", cell_value: value });
const column = (id, name) => ({ id, name, data_type: "text" });

async function mountDataset({ columns, row, selection, schemas = {} }) {
  axios.get.mockImplementation(async (url) => {
    if (url === endpoints.develop.getDatasetDetail("dataset")) {
      return {
        data: {
          result: {
            column_config: columns,
            table: [row],
            metadata: { total_rows: 1 },
          },
        },
      };
    }
    if (url === endpoints.develop.getJsonColumnSchema("dataset")) {
      return { data: { result: schemas } };
    }
    return { data: { status: true, result: { datasets: [] } } };
  });
  axios.post.mockResolvedValue({
    data: { status: true, result: { output: 1 } },
  });
  const ref = React.createRef();
  const onColumnsLoaded = vi.fn();
  const onReadyChange = vi.fn();
  const onTestResult = vi.fn();
  render(
    <DatasetTestMode
      ref={ref}
      templateId="eval"
      initialDatasetId="dataset"
      variables={["answer"]}
      initialMapping={{ answer: selection }}
      onColumnsLoaded={onColumnsLoaded}
      onReadyChange={onReadyChange}
      onTestResult={onTestResult}
    />,
  );
  await waitFor(() => {
    expect(onColumnsLoaded).toHaveBeenCalledWith(columns, schemas);
    expect(ref.current.mapping).toEqual({ answer: selection });
  });
  return {
    onReadyChange,
    onTestResult,
    run: async () => {
      await act(async () => ref.current.runTest());
      await waitFor(() => expect(onTestResult).toHaveBeenCalled());
    },
  };
}

function expectSubmittedAnswer(value) {
  expect(axios.post).toHaveBeenCalledTimes(1);
  expect(axios.post).toHaveBeenCalledWith(
    endpoints.develop.eval.evalPlayground,
    expect.objectContaining({
      config: expect.objectContaining({ mapping: { answer: value } }),
      input_data_types: { answer: "text" },
    }),
  );
}

describe("W10-EVALS-L03 dataset column identity", () => {
  it.each([
    ["response.text", true],
    ["response.text", false],
    ["response[0]", true],
    ["response[0]", false],
  ])("reads literal %s with prefix column present=%s", async (name, withPrefix) => {
    const columns = [
      ...(withPrefix ? [column("base", "response")] : []),
      column("selected", name),
    ];
    const { run, onReadyChange } = await mountDataset({
      columns,
      row: {
        base: cell("WRONG BASE INPUT"),
        selected: cell("CORRECT SELECTED INPUT"),
      },
      selection: name,
    });
    await run();
    expectSubmittedAnswer("CORRECT SELECTED INPUT");
    expect(onReadyChange).toHaveBeenLastCalledWith(true, { answer: "selected" });
  });

  it.each([false, true])(
    "prefers the literal column over a schema path, literal first=%s",
    async (literalFirst) => {
      const columns = [
        column("base", "response"),
        column("selected", "response.text"),
      ];
      if (literalFirst) columns.reverse();
      const { run, onReadyChange } = await mountDataset({
        columns,
        row: {
          base: cell({ text: "WRONG NESTED INPUT" }),
          selected: cell("CORRECT SELECTED INPUT"),
        },
        selection: "response.text",
        schemas: { base: { keys: ["text"] } },
      });
      await run();
      expectSubmittedAnswer("CORRECT SELECTED INPUT");
      expect(onReadyChange).toHaveBeenLastCalledWith(true, { answer: "selected" });
    },
  );

  it.each([
    ["response", "text", { text: "NESTED INPUT" }, "NESTED INPUT"],
    ["response", "text", '{"text":"JSON INPUT"}', "JSON INPUT"],
    ["response", "[0].text", [{ text: "ARRAY INPUT" }], "ARRAY INPUT"],
    ["response.text", "value", { value: "DOTTED PARENT" }, "DOTTED PARENT"],
    ["response[0]", "value", { value: "BRACKET PARENT" }, "BRACKET PARENT"],
    ["response", "value", { value: 0 }, "0"],
    ["response", "value", { value: false }, "false"],
    ["response", "value", { value: "" }, ""],
    ["response", "value", { value: { nested: true } }, '{"nested":true}'],
  ])("resolves an explicit nested selection %s / %s", async (name, path, value, expected) => {
    const separator = path.startsWith("[") ? "" : ".";
    const { run, onReadyChange } = await mountDataset({
      columns: [column("selected", name)],
      row: { selected: cell(value) },
      selection: `${name}${separator}${path}`,
      schemas: { selected: { keys: [path] } },
    });
    await run();
    expectSubmittedAnswer(expected);
    expect(onReadyChange).toHaveBeenLastCalledWith(true, {
      answer: `selected${separator}${path}`,
    });
  });

  it.each([
    ["plain text", "DO NOT EVALUATE THE PARENT"],
    ["malformed JSON", '{"text":'],
    ["absent key", { other: "DO NOT EVALUATE THE PARENT" }],
    ["null leaf", { text: null }],
    ["null cell", null],
    ["JSON null", "null"],
    ["JSON scalar", "42"],
  ])("does not evaluate the parent when a nested value is %s", async (_label, value) => {
    const { run, onTestResult } = await mountDataset({
      columns: [column("base", "response")],
      row: { base: cell(value) },
      selection: "response.text",
      schemas: { base: { keys: ["text"] } },
    });
    await run();
    expect(axios.post).not.toHaveBeenCalled();
    expect(onTestResult).toHaveBeenLastCalledWith(false, expect.any(String));
  });
});
