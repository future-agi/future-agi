// G2B-imports-list-01: regression tests authored before the adapter.
// Static-only packet: these tests have not been executed.
import React, { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "src/utils/test-utils";
import { setRealBackendForTests } from "src/sections/develop/realBackendCaps";
import {
  useChangeSetStore,
  useCreatedColumnsStore,
} from "src/sections/develop-detail/DataTab/Versions/changeSetStore";
import PasteRowsDialog from "../PasteRowsDialog";

const h = vi.hoisted(() => ({
  post: vi.fn(),
  get: vi.fn(),
  refresh: vi.fn(),
  close: vi.fn(),
  notify: vi.fn(),
  columns: [],
  detail: {},
}));

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: {
    post: (...args) => h.post(...args),
    get: (...args) => h.get(...args),
  },
}));
vi.mock("src/sections/common/ux/toast", async (importOriginal) => ({
  ...(await importOriginal()),
  notify: h.notify,
}));
vi.mock(
  "src/sections/develop-detail/Context/DevelopDetailContext",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useDevelopDetailContext: () => ({ refreshGrid: h.refresh }),
  }),
);
vi.mock(
  "src/sections/develop-detail/DataTab/DatapointDrawerV2/itemHooks",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useDatasetColumns: () => ({ data: h.columns }),
  }),
);
vi.mock("src/api/develop/develop-detail", async (importOriginal) => ({
  ...(await importOriginal()),
  useGetDatasetDetail: () => ({ data: h.detail }),
}));
/* eslint-disable react/prop-types */
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => <>{children}</>,
}));
/* eslint-enable react/prop-types */

const DATASET = "11111111-1111-4111-8111-111111111111";
const ROWS_PATH = `/model-hub/develops/${DATASET}/add_rows/`;
const COLUMN_PATH = `/model-hub/develops/${DATASET}/add_static_column/`;
let client;
let invalidate;
let addOp;
let trackColumn;

function Harness() {
  const [open, setOpen] = useState(true);
  return (
    <PasteRowsDialog
      open={open}
      datasetId={DATASET}
      onClose={(added) => {
        h.close(added);
        setOpen(false);
      }}
    />
  );
}

function mount() {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  invalidate = vi.spyOn(client, "invalidateQueries");
  render(
    <QueryClientProvider client={client}>
      <Harness />
    </QueryClientProvider>,
  );
}

function paste(text) {
  fireEvent.change(screen.getByTestId("paste-rows-text"), {
    target: { value: text },
  });
}

const submit = () => fireEvent.click(screen.getByTestId("paste-rows-add"));
const successCalls = () =>
  h.notify.mock.calls.filter(([, options]) => options?.variant === "success");
const rowResponse = () => ({
  data: { status: true, result: "Successfully added 1 row(s)" },
});

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  vi.resetAllMocks();
  setRealBackendForTests(true);
  useChangeSetStore.setState({ sets: {} });
  useCreatedColumnsStore.setState({ cols: {} });
  addOp = vi.spyOn(useChangeSetStore.getState(), "addOp");
  trackColumn = vi.spyOn(useCreatedColumnsStore.getState(), "add");
  h.columns = [{ id: "col-question", name: "question", data_type: "text" }];
  h.detail = {};
  h.post.mockImplementation((url) =>
    Promise.reject(new Error(`Unexpected POST ${url}`)),
  );
  h.get.mockImplementation((url) =>
    Promise.reject(new Error(`Unexpected GET ${url}`)),
  );
});

afterEach(() => {
  cleanup();
  client?.clear();
  vi.restoreAllMocks();
  useChangeSetStore.setState({ sets: {} });
  useCreatedColumnsStore.setState({ cols: {} });
  setRealBackendForTests(null);
});

describe("Paste rows against the real backend", () => {
  it("waits for one name-based persistent write before success, close, or refresh", async () => {
    const write = deferred();
    h.post.mockReturnValue(write.promise);
    mount();
    paste("question\nhello");
    submit();

    await waitFor(() => expect(h.post).toHaveBeenCalledTimes(1));
    expect(h.post).toHaveBeenCalledWith(ROWS_PATH, {
      rows: [{ cells: [{ column_name: "question", value: "hello" }] }],
    });
    expect(addOp).not.toHaveBeenCalled();
    expect(trackColumn).not.toHaveBeenCalled();
    expect(useChangeSetStore.getState().sets).toEqual({});
    expect(screen.getByTestId("paste-rows-text")).toHaveValue("question\nhello");
    expect(screen.getByTestId("paste-rows-add")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Close" })).toBeDisabled();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(h.close).not.toHaveBeenCalled();
    expect(successCalls()).toEqual([]);
    expect(h.refresh).not.toHaveBeenCalled();
    expect(invalidate).not.toHaveBeenCalled();

    await act(async () => write.resolve(rowResponse()));
    await waitFor(() => expect(h.close).toHaveBeenCalledWith(true));
    expect(h.close).toHaveBeenCalledTimes(1);
    expect(h.post).toHaveBeenCalledTimes(1);
    expect(h.refresh).toHaveBeenCalledTimes(1);
    expect(invalidate).toHaveBeenCalledTimes(1);
    expect(successCalls()).toEqual([
      ["1 pasted row added", { variant: "success", key: "rows-pasted" }],
    ]);
    expect(addOp).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("retains the dialog and input after rejection without announcing success", async () => {
    const write = deferred();
    h.post.mockReturnValue(write.promise);
    mount();
    paste("question\nhello");
    submit();
    await waitFor(() => expect(h.post).toHaveBeenCalledTimes(1));

    await act(async () =>
      write.reject({ response: { data: { result: "Row limit reached" } } }),
    );
    await waitFor(() => expect(screen.getByTestId("paste-rows-add")).toBeEnabled());
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByTestId("paste-rows-text")).toHaveValue("question\nhello");
    expect(h.notify).toHaveBeenCalledWith(
      expect.stringContaining("Row limit reached"),
      { variant: "error", key: "rows-pasted-error" },
    );
    expect(successCalls()).toEqual([]);
    expect(h.close).not.toHaveBeenCalled();
    expect(h.refresh).not.toHaveBeenCalled();
    expect(invalidate).not.toHaveBeenCalled();
    expect(addOp).not.toHaveBeenCalled();
  });

  it("keeps new-column values when add_static_column returns its actual success string", async () => {
    const columnWrite = deferred();
    const rowWrite = deferred();
    h.post.mockImplementation((url) => {
      if (url === COLUMN_PATH) return columnWrite.promise;
      if (url === ROWS_PATH) return rowWrite.promise;
      throw new Error(`Unexpected POST ${url}`);
    });
    mount();
    paste("question,sku\nhello,00123");
    submit();
    expect(h.post).toHaveBeenCalledTimes(1);
    expect(h.post).toHaveBeenCalledWith(COLUMN_PATH, {
      new_column_name: "sku",
      column_type: "text",
    });
    await act(async () =>
      columnWrite.resolve({ data: { status: true, result: "Column added successfully" } }),
    );
    await waitFor(() => expect(h.post).toHaveBeenCalledTimes(2));
    expect(h.post).toHaveBeenLastCalledWith(ROWS_PATH, {
      rows: [{ cells: [
        { column_name: "question", value: "hello" },
        { column_name: "sku", value: "00123" },
      ] }],
    });
    expect(h.get).not.toHaveBeenCalled();
    expect(h.close).not.toHaveBeenCalled();
    expect(h.refresh).not.toHaveBeenCalled();
    expect(invalidate).not.toHaveBeenCalled();
    expect(successCalls()).toEqual([]);
    expect(screen.getByTestId("paste-rows-text")).toHaveValue("question,sku\nhello,00123");

    await act(async () => rowWrite.resolve(rowResponse()));
    await waitFor(() => expect(h.close).toHaveBeenCalledWith(true));
    expect(successCalls()).toHaveLength(1);
    expect(successCalls()[0][0]).not.toContain("pending changes");
    expect(h.refresh).toHaveBeenCalledTimes(1);
    expect(addOp).not.toHaveBeenCalled();
    expect(trackColumn).not.toHaveBeenCalled();
  });

  it("retries a rejected row write without trying to recreate its new column", async () => {
    let writes = 0;
    h.post.mockImplementation((url) => {
      if (url === COLUMN_PATH)
        return Promise.resolve({ data: { result: "Column added successfully" } });
      if (url === ROWS_PATH) {
        writes += 1;
        return writes === 1
          ? Promise.reject(new Error("Try again"))
          : Promise.resolve(rowResponse());
      }
      throw new Error(`Unexpected POST ${url}`);
    });
    mount();
    paste("sku\nA-1");
    submit();
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith(
      expect.stringContaining("Try again"),
      { variant: "error", key: "rows-pasted-error" },
    ));
    expect(screen.getByTestId("paste-rows-text")).toHaveValue("sku\nA-1");
    expect(successCalls()).toEqual([]);
    expect(h.close).not.toHaveBeenCalled();
    submit();
    await waitFor(() => expect(h.close).toHaveBeenCalledWith(true));
    expect(h.post.mock.calls.filter(([url]) => url === COLUMN_PATH)).toHaveLength(1);
    expect(h.post.mock.calls.filter(([url]) => url === ROWS_PATH)).toEqual([
      [ROWS_PATH, { rows: [{ cells: [{ column_name: "sku", value: "A-1" }] }] }],
      [ROWS_PATH, { rows: [{ cells: [{ column_name: "sku", value: "A-1" }] }] }],
    ]);
    expect(addOp).not.toHaveBeenCalled();
  });

  it("does not write rows or clear input when column creation fails", async () => {
    h.post.mockRejectedValue(new Error("Column could not be created"));
    mount();
    paste("sku\nA-1");
    submit();
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith(
      expect.stringContaining("Column could not be created"),
      { variant: "error", key: "rows-pasted-error" },
    ));
    expect(h.post).toHaveBeenCalledTimes(1);
    expect(h.post.mock.calls[0][0]).toBe(COLUMN_PATH);
    expect(screen.getByTestId("paste-rows-text")).toHaveValue("sku\nA-1");
    expect(successCalls()).toEqual([]);
    expect(h.close).not.toHaveBeenCalled();
    expect(h.refresh).not.toHaveBeenCalled();
    expect(addOp).not.toHaveBeenCalled();
  });

  it("writes only schema-accepted rows and preserves the configured transformations", async () => {
    h.detail = { dataset_config: { schema: {
      enforce: "reject",
      declared: { properties: { question: { type: "string", enum: [" hello "] } } },
      transforms: [{ column: "question", kind: "trim_whitespace" }],
    } } };
    h.post.mockResolvedValue(rowResponse());
    mount();
    paste("question\n hello \nrejected");
    expect(screen.getByTestId("paste-rows-schema")).toHaveTextContent("1 row doesn't");
    submit();
    await waitFor(() => expect(h.close).toHaveBeenCalledWith(true));
    expect(h.post).toHaveBeenCalledExactlyOnceWith(ROWS_PATH, {
      rows: [{ cells: [{ column_name: "question", value: "hello" }] }],
    });
  });

  it("adds common metadata headers as new columns without remapping", async () => {
    const headers = ["category", "tag", "label", "source", "notes", "language"];
    const values = ["shipping", "urgent", "positive", "chat", "follow up", "en"];
    h.post.mockImplementation((url) => {
      if (url === COLUMN_PATH)
        return Promise.resolve({ data: { result: "Column added successfully" } });
      if (url === ROWS_PATH) return Promise.resolve(rowResponse());
      throw new Error(`Unexpected POST ${url}`);
    });
    mount();
    paste(`${headers.join(",")}\n${values.join(",")}`);
    expect(screen.getByTestId("paste-rows-add")).toBeEnabled();
    expect(screen.getByTestId("paste-rows-add")).toHaveTextContent(
      "Add 1 row · 6 new columns",
    );
    expect(screen.queryByTestId("paste-rows-reason")).not.toBeInTheDocument();
    submit();
    await waitFor(() => expect(h.close).toHaveBeenCalledWith(true));
    expect(h.post.mock.calls).toEqual([
      ...headers.map((header) => [COLUMN_PATH, {
        new_column_name: header,
        column_type: "text",
      }]),
      [ROWS_PATH, { rows: [{ cells: headers.map((header, index) => ({
        column_name: header,
        value: values[index],
      })) }] }],
    ]);
    expect(h.get).not.toHaveBeenCalled();
    expect(addOp).not.toHaveBeenCalled();
    expect(trackColumn).not.toHaveBeenCalled();
  });

  it("uses existing columns and creates only unmatched metadata headers", async () => {
    h.columns.push({ id: "col-category", name: "category", data_type: "text" });
    h.post.mockImplementation((url) => {
      if (url === COLUMN_PATH)
        return Promise.resolve({ data: { result: "Column added successfully" } });
      if (url === ROWS_PATH) return Promise.resolve(rowResponse());
      throw new Error(`Unexpected POST ${url}`);
    });
    mount();
    paste("question,Category,notes\nhello,shipping,follow up");
    expect(screen.getByTestId("paste-rows-add")).toBeEnabled();
    expect(screen.getByTestId("paste-rows-add")).toHaveTextContent(
      "Add 1 row · 1 new column",
    );
    submit();
    await waitFor(() => expect(h.close).toHaveBeenCalledWith(true));
    expect(h.post.mock.calls).toEqual([
      [COLUMN_PATH, { new_column_name: "notes", column_type: "text" }],
      [ROWS_PATH, { rows: [{ cells: [
        { column_name: "question", value: "hello" },
        { column_name: "category", value: "shipping" },
        { column_name: "notes", value: "follow up" },
      ] }] }],
    ]);
    expect(addOp).not.toHaveBeenCalled();
  });

  it("explains unsupported metadata instead of silently dropping pasted values", async () => {
    mount();
    paste("question,category\nhello,shipping");
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Where category goes" }));
    fireEvent.click(await screen.findByRole("option", { name: "metadata.category" }));
    expect(screen.getByTestId("paste-rows-add")).toBeDisabled();
    expect(screen.getByTestId("paste-rows-reason")).toHaveTextContent(/metadata/i);
    expect(screen.getByTestId("paste-rows-text")).toHaveValue("question,category\nhello,shipping");
    submit();
    expect(h.post).not.toHaveBeenCalled();
    expect(addOp).not.toHaveBeenCalled();
    expect(successCalls()).toEqual([]);
    expect(h.close).not.toHaveBeenCalled();
  });
});

describe("Paste rows in prototype mode", () => {
  it("still stages id-based rows with metadata and paste provenance", async () => {
    setRealBackendForTests(false);
    mount();
    paste("question,category\nhello,shipping");
    submit();
    await waitFor(() => expect(h.close).toHaveBeenCalledWith(true));
    expect(h.post).not.toHaveBeenCalled();
    expect(addOp).toHaveBeenCalledWith(DATASET, {
      op: "add_row",
      temp_id: expect.stringMatching(/^new-/),
      cells: { "col-question": "hello" },
      metadata: { category: "shipping" },
      split_ids: [],
      source: { type: "paste", label: "Pasted" },
    });
    expect(useChangeSetStore.getState().sets[DATASET].ops).toHaveLength(1);
    expect(successCalls()[0][0]).toContain("added to pending changes");
  });

  it.each([
    ["id response", { column_id: "col-sku" }],
    ["string response", "Column added successfully"],
  ])("keeps new-column values pending with a %s", async (_, result) => {
    setRealBackendForTests(false);
    h.post.mockResolvedValue({ data: { result } });
    h.get.mockResolvedValue({ data: { result: {
      column_config: [...h.columns, { id: "col-sku", name: "sku", data_type: "text" }],
    } } });
    mount();
    paste("sku\nA-1");
    submit();
    await waitFor(() => expect(h.close).toHaveBeenCalledWith(true));
    expect(h.post).toHaveBeenCalledExactlyOnceWith(COLUMN_PATH, {
      new_column_name: "sku",
      column_type: "text",
    });
    expect(addOp).toHaveBeenCalledWith(DATASET, expect.objectContaining({
      op: "add_row",
      cells: { "col-sku": "A-1" },
    }));
    expect(useCreatedColumnsStore.getState().cols[DATASET]).toEqual([
      { id: "col-sku", name: "sku", from: "paste" },
    ]);
    expect(successCalls()[0][0]).toContain("added to pending changes");
  });
});
