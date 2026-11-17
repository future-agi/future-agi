// the capability contract: custom Python code is turned off. Every door that would test,
// add, edit or re-run code is hidden or disabled with the reason, and none of
// them sends a request; existing values stay readable and every other column
// keeps its own controls. The server refuses the same operations (400, fixed
// message) and its workers refuse before touching data
// (futureagi/model_hub/tests/test_dataset_code_columns_disabled.py).
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const REASON =
  "Custom Python code is turned off. Existing values stay readable, but code cannot be tested, added, edited or re-run.";
const DATASET = "0b7c5a52-6f0e-4a8e-9a43-2a1f0e1d3c11";
const COLUMN = "6d1b1f7e-3c55-4a2b-8e4b-1f6a9c2d7e01";
const BENIGN_CODE = "def main(**kwargs):\n    return 'diagnosis complete'\n";
const CODE_CONDITIONAL = [
  {
    branch_type: "else",
    condition: "",
    branch_node_config: { type: "extract_code", config: { code: BENIGN_CODE } },
  },
];
const HARMLESS_CONDITIONAL = [
  {
    branch_type: "else",
    condition: "mentions extract_code",
    branch_node_config: {
      type: "static_value",
      config: { value: "extract_code: def main(**kwargs)" },
    },
  },
];

const net = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  metadata: null,
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { ...actual.default, get: net.get, post: net.post },
  };
});
vi.mock("src/sections/develop-detail/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ refreshGrid: vi.fn() }),
}));
vi.mock("src/api/develop/develop-detail", async (importOriginal) => ({
  ...(await importOriginal()),
  useDatasetColumnConfig: () => [],
}));
// The Monaco editor does not run in jsdom; a stand-in shows where it renders.
vi.mock("src/components/form-code-editor", () => ({
  FormCodeEditor: () => <textarea data-testid="python-code-editor" />,
}));

import {
  useClassificationStore,
  useConditionalNodeStore,
  useExecuteCodeStore,
} from "../../states";
import { columnActionMenuItems } from "../../DataTab/common";
import ExecuteCode from "../ExecuteCode/ExecuteCode";
import ConditionalNodeV2 from "../ConditionalNode/ConditionalNodeV2";
import OutputCard from "../../DataTab/DatapointDrawerV2/OutputCard";

const configure = (items) =>
  items.find((i) => i?.name === "Configure Dynamic Column");

const theme = createTheme({ palette: palette("light") });

const renderInPage = (ui) =>
  render(
    <ThemeProvider theme={theme}>
      <QueryClientProvider
        client={
          new QueryClient({
            defaultOptions: { queries: { retry: false }, mutations: {} },
          })
        }
      >
        <MemoryRouter initialEntries={[`/dashboard/develop/${DATASET}`]}>
          <Routes>
            <Route path="/dashboard/develop/:dataset" element={ui} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );

beforeEach(() => {
  net.get.mockReset();
  net.post.mockReset();
  net.get.mockImplementation(() =>
    Promise.resolve({
      data: { result: { column_id: COLUMN, metadata: net.metadata } },
    }),
  );
  net.post.mockImplementation(() => Promise.resolve({ data: {} }));
});

afterEach(() => {
  act(() => {
    useExecuteCodeStore.setState({ openExecuteCode: false });
    useConditionalNodeStore.setState({ openConditionalNode: false });
    useClassificationStore.setState({ openClassification: false });
  });
});

describe("column menu: Configure Dynamic Column", () => {
  it("is shown disabled, with the reason, on a Python code column", () => {
    const item = configure(
      columnActionMenuItems({
        id: COLUMN,
        name: "py_out",
        originType: "python_code",
      }),
    );
    expect(item).toBeTruthy();
    expect(item.disabled).toBe(true);
    expect(item.tooltip).toBe(REASON);
    act(() => item.action?.());
    expect(useExecuteCodeStore.getState().openExecuteCode).toBe(false);
  });

  it("still opens the editor of every other dynamic column", () => {
    const classification = configure(
      columnActionMenuItems({
        id: COLUMN,
        name: "label",
        originType: "classification",
      }),
    );
    expect(classification.disabled).toBeFalsy();
    act(() => classification.action());
    expect(useClassificationStore.getState().openClassification).toEqual({
      editId: COLUMN,
    });

    const conditional = configure(
      columnActionMenuItems({
        id: COLUMN,
        name: "cond",
        originType: "conditional",
      }),
    );
    expect(conditional.disabled).toBeFalsy();
    act(() => conditional.action());
    expect(useConditionalNodeStore.getState().openConditionalNode).toEqual({
      editId: COLUMN,
    });
  });

  it("keeps the ordinary controls of a Python code column", () => {
    const names = columnActionMenuItems(
      { id: COLUMN, name: "py_out", originType: "python_code" },
      false,
    ).map((i) => i?.name);
    expect(names).toEqual(
      expect.arrayContaining(["Edit Column Name", "Delete Column"]),
    );
  });
});

describe("Execute Code drawer (store or deep-link entry)", () => {
  it.each([
    ["edit of an existing column", { editId: COLUMN }],
    ["create", true],
  ])("%s: shows the reason, no editor, no request", async (_label, entry) => {
    net.metadata = { code: BENIGN_CODE, concurrency: 2 };
    act(() => useExecuteCodeStore.setState({ openExecuteCode: entry }));
    renderInPage(<ExecuteCode />);

    expect(await screen.findByText(REASON)).toBeTruthy();
    expect(screen.queryByTestId("python-code-editor")).toBeNull();
    expect(screen.queryByRole("button", { name: "Test" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Update Column/ })).toBeNull();
    expect(
      screen.queryByRole("button", { name: /Create New Column/ }),
    ).toBeNull();
    expect(net.get).not.toHaveBeenCalled();
    expect(net.post).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(useExecuteCodeStore.getState().openExecuteCode).toBe(false);
  });
});

describe("Conditional Node editor", () => {
  it("a column with a code branch opens disabled, with the reason", async () => {
    net.metadata = { config: CODE_CONDITIONAL, concurrency: 2 };
    act(() =>
      useConditionalNodeStore.setState({
        openConditionalNode: { editId: COLUMN },
      }),
    );
    renderInPage(<ConditionalNodeV2 />);

    expect(await screen.findByText(REASON)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Update Column/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Test" })).toBeNull();
    expect(net.post).not.toHaveBeenCalled();
    // Only the read-only config read went out.
    expect(net.get).toHaveBeenCalledTimes(1);
  });

  it("a harmless conditional column keeps its editor", async () => {
    net.metadata = { config: HARMLESS_CONDITIONAL, concurrency: 2 };
    act(() =>
      useConditionalNodeStore.setState({
        openConditionalNode: { editId: COLUMN },
      }),
    );
    renderInPage(<ConditionalNodeV2 />);

    expect(
      await screen.findByRole("button", { name: /Update Column/ }),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Test" })).toBeTruthy();
    expect(screen.queryByText(REASON)).toBeNull();
  });
});

describe("Row view: a failed Python code cell", () => {
  const output = (originType) => ({
    column: {
      id: COLUMN,
      name: "py_out",
      data_type: "text",
      origin_type: originType,
    },
    cell: {
      status: "error",
      cell_value: null,
      value_infos: { reason: "stored reason" },
    },
    evals: [],
  });
  const card = (originType, onRerun) =>
    render(
      <ThemeProvider theme={theme}>
        <OutputCard
          output={output(originType)}
          row={{ row_id: "r1" }}
          mode="formatted"
          expectedOptions={[]}
          onToggleEval={() => {}}
          canEdit
          onRerun={onRerun}
        />
      </ThemeProvider>,
    );

  it("keeps its stored reason readable and offers no re-run", () => {
    const onRerun = vi.fn();
    card("python_code", onRerun);
    expect(screen.getByText("stored reason")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Re-run this cell/ })).toBe(
      null,
    );
    expect(screen.getByText(REASON)).toBeTruthy();
    expect(onRerun).not.toHaveBeenCalled();
  });

  it("other generated cells keep Re-run this cell", () => {
    const onRerun = vi.fn();
    card("run_prompt", onRerun);
    fireEvent.click(screen.getByRole("button", { name: /Re-run this cell/ }));
    expect(onRerun).toHaveBeenCalledTimes(1);
  });
});

describe("Conditional Node editor waits for its config", () => {
  it("never shows the editor of a code column before the config is read", async () => {
    let release;
    net.get.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = () =>
            resolve({
              data: {
                result: {
                  column_id: COLUMN,
                  metadata: { config: CODE_CONDITIONAL },
                },
              },
            });
        }),
    );
    act(() =>
      useConditionalNodeStore.setState({
        openConditionalNode: { editId: COLUMN },
      }),
    );
    renderInPage(<ConditionalNodeV2 />);
    expect(screen.queryByRole("button", { name: /Update Column/ })).toBeNull();
    await waitFor(() => expect(release).toBeTypeOf("function"));
    await act(async () => release());
    expect(await screen.findByText(REASON)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Update Column/ })).toBeNull();
  });
});
