// NOHIDE-ADDROWS-FIX: real chooser + real forms, mocked transport only.
import React, { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { palette } from "src/theme/palette";
import { madeRealSurfaceIds } from "src/launch/madeReal";
import {
  isSurfaceShown,
  setLaunchOverrideForTest,
} from "src/launch/launchSwitch";
import AddRowDrawer from "../AddRowDrawer";

const h = vi.hoisted(() => ({
  post: vi.fn(),
  get: vi.fn(),
  refresh: vi.fn(),
  close: vi.fn(),
  addOp: vi.fn(),
  blankAddOp: vi.fn(),
  openRow: vi.fn(),
  snackbar: vi.fn(),
  versions: vi.fn(),
  reveal: vi.fn(),
}));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: {
    post: (...args) => h.post(...args),
    get: (...args) => h.get(...args),
  },
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: {},
  PropertyName: {},
}));
vi.mock("src/components/snackbar", () => ({
  enqueueSnackbar: (...args) => h.snackbar(...args),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/sections/common/ux/toast", () => ({ notify: vi.fn() }));
vi.mock("src/sections/develop-detail/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ refreshGrid: h.refresh }),
}));
vi.mock("src/sections/develop-detail/DataTab/Versions/changeSetStore", () => ({
  useChangeSet: () => ({ addOp: h.blankAddOp, revert: vi.fn() }),
  useChangeSetStore: Object.assign(() => 0, {
    getState: () => ({ addOp: h.addOp }),
  }),
}));
vi.mock(
  "src/sections/develop-detail/DataTab/DatapointDrawerV2/useRowViewUrl",
  () => ({
    default: () => ({ openRow: h.openRow }),
  }),
);
vi.mock("src/api/develop/dataset-versions", () => ({
  useDatasetVersions: (...args) => h.versions(...args),
  latestOf: () => null,
}));
vi.mock("src/sections/develop-detail/DataTab/DataGrid/newRowsReveal", () => ({
  revealNewRows: (...args) => h.reveal(...args),
}));
vi.mock("src/sections/common/AddData/GenerateRowsDialog", () => ({
  useGenerateRows: {},
}));
vi.mock("../ExistingDatasetModal", () => ({ default: () => null }));
vi.mock("../AddSDKModal", () => ({ default: () => null }));
vi.mock("../PasteRowsDialog", () => ({ default: () => null }));
vi.mock("../CreateSyntheticData", () => ({ default: () => null }));

const DATASET = "11111111-1111-4111-8111-111111111111";
const FILE_PATH = "/model-hub/develops/add_rows_from_file/";
const EMPTY_PATH = `/model-hub/develops/${DATASET}/add_empty_rows/`;
const IMPORT_PATH = "/model-hub/datasets/imports/";
const theme = createTheme({ palette: palette("light") });
const clients = [];

function Sheet() {
  const [open, setOpen] = useState(true);
  const location = useLocation();
  return (
    <>
      <output data-testid="location">
        {location.pathname}
        {location.search}
      </output>
      <AddRowDrawer
        open={open}
        onClose={() => {
          h.close();
          setOpen(false);
        }}
      />
    </>
  );
}
function mount() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  clients.push(client);
  const invalidate = vi.spyOn(client, "invalidateQueries");
  render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>
        <MemoryRouter initialEntries={[`/dashboard/develop/${DATASET}`]}>
          <Routes>
            <Route path="/dashboard/develop/:dataset" element={<Sheet />} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
  return { invalidate };
}
function openDoor(name, key) {
  const door = screen.getByRole("button", { name });
  expect(door).toBeVisible();
  expect(door).not.toHaveAttribute("aria-disabled", "true");
  if (key) fireEvent.keyDown(door, { key });
  else fireEvent.click(door);
}
async function chooseFile(
  text = "question\nhello\n",
  name = "rows.csv",
  type = "text/csv",
) {
  const file = new File([text], name, { type });
  file.text = vi.fn().mockResolvedValue(text);
  const input = document.querySelector('input[type="file"]');
  expect(input).not.toBeNull();
  fireEvent.change(input, { target: { files: [file] } });
  await waitFor(() => expect(screen.getByText(name)).toBeVisible());
  return file;
}
function expectNoStaging() {
  expect(h.addOp).not.toHaveBeenCalled();
  expect(h.blankAddOp).not.toHaveBeenCalled();
  expect(h.openRow).not.toHaveBeenCalled();
  expect(h.reveal).not.toHaveBeenCalled();
  expect(h.versions).not.toHaveBeenCalled();
  expect(h.get).not.toHaveBeenCalled();
}

beforeEach(() => {
  vi.clearAllMocks();
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: false,
    madeReal: madeRealSurfaceIds(),
  });
  h.versions.mockReturnValue({ data: [] });
  h.post.mockReset().mockRejectedValue(new Error("Unexpected POST"));
  h.get.mockReset().mockRejectedValue(new Error("Unexpected GET"));
  const noNetwork = () => {
    throw new Error("Network is forbidden in this test");
  };
  vi.stubGlobal("fetch", vi.fn(noNetwork));
  vi.spyOn(XMLHttpRequest.prototype, "open").mockImplementation(noNetwork);
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  setLaunchOverrideForTest(null);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("classic Add rows under the real launch claims", () => {
  it("enables file and empty rows without notices; HF stays inert and paste stays absent", () => {
    mount();
    for (const name of [/^Upload a file/, /^Add a blank row/]) {
      const door = screen.getByRole("button", { name });
      expect(door).toBeVisible();
      expect(door).not.toHaveAttribute("aria-disabled", "true");
      expect(
        within(door).queryByText(/turned off in this release/),
      ).not.toBeInTheDocument();
    }
    const hf = screen.getByRole("button", { name: /^Hugging Face/ });
    expect(hf).toHaveAttribute("aria-disabled", "true");
    expect(
      within(hf).getByText(
        "Hugging Face imports can leave imported cells blank; they are turned off in this release. Instead, create a dataset from an uploaded file or copy rows from another dataset.",
      ),
    ).toBeVisible();
    fireEvent.click(hf);
    fireEvent.keyDown(hf, { key: "Enter" });
    fireEvent.keyDown(hf, { key: " " });
    expect(
      screen.queryByRole("button", { name: /^Paste/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(h.close).not.toHaveBeenCalled();
    expect(h.post).not.toHaveBeenCalled();
    expect(screen.getByTestId("location")).toHaveTextContent(
      `/dashboard/develop/${DATASET}`,
    );
    for (const id of [
      "dataset.imports.sources",
      "dataset.versions",
      "dataset.imports.pasteRows",
    ]) {
      expect(isSurfaceShown(id), id).toBe(false);
    }
    expectNoStaging();
  });

  it.each([
    ["rows.csv", "text/csv", "question\nhello\n"],
    ["rows.json", "application/json", '[{"question":"hello"}]'],
    ["rows.jsonl", "text/plain", '{"question":"hello"}\n'],
  ])(
    "appends %s with the v1.56.1 multipart body, invalidates caches and refreshes",
    async (name, type, text) => {
      h.post.mockResolvedValue({ data: { result: "Rows added" } });
      const { invalidate } = mount();
      openDoor(/^Upload a file/, "Enter");
      const file = await chooseFile(text, name, type);
      expect(h.post).not.toHaveBeenCalled();
      expect(screen.queryByLabelText("question role")).not.toBeInTheDocument();
      expect(screen.queryByText(/pending changes/)).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Add rows" }));
      await waitFor(() => expect(h.close).toHaveBeenCalledOnce());
      expect(h.post).toHaveBeenCalledOnce();
      const [url, body, config] = h.post.mock.calls[0];
      expect(url).toBe(FILE_PATH);
      expect(body).toBeInstanceOf(FormData);
      expect([...body.entries()]).toEqual([
        ["dataset_id", DATASET],
        ["file", file],
      ]);
      expect(config).toEqual({
        headers: { "Content-Type": "multipart/form-data" },
      });
      expect(file.text).not.toHaveBeenCalled();
      expect(invalidate).toHaveBeenCalledWith({
        queryKey: ["dataset-detail", DATASET],
      });
      expect(invalidate).toHaveBeenCalledWith({
        queryKey: ["json-column-schema", DATASET],
      });
      expect(h.refresh).toHaveBeenCalledExactlyOnceWith(null, true);
      expect(h.snackbar).toHaveBeenCalledWith("Rows added to this dataset.", {
        variant: "success",
      });
      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
      );
      expectNoStaging();
    },
  );

  it("keeps a failed upload open, does not refresh, and retries the same file", async () => {
    h.post
      .mockRejectedValueOnce(new Error("Upload rejected"))
      .mockResolvedValueOnce({ data: {} });
    mount();
    openDoor(/^Upload a file/);
    const file = await chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Add rows" }));
    await waitFor(() =>
      expect(h.snackbar).toHaveBeenCalledWith("Upload rejected", {
        variant: "error",
      }),
    );
    expect(h.refresh).not.toHaveBeenCalled();
    expect(h.close).not.toHaveBeenCalled();
    expect(screen.getByText("rows.csv")).toBeVisible();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Add rows" })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Add rows" }));
    await waitFor(() => expect(h.close).toHaveBeenCalledOnce());
    expect(h.post).toHaveBeenCalledTimes(2);
    for (const [url, body] of h.post.mock.calls) {
      expect(url).toBe(FILE_PATH);
      expect(body.get("file")).toEqual(file);
    }
    expect(h.refresh).toHaveBeenCalledExactlyOnceWith(null, true);
    expectNoStaging();
  });

  it("adds five empty rows via the classic JSON endpoint and refreshes the data grid", async () => {
    h.post.mockResolvedValue({
      data: { result: "Successfully added 5 empty row(s)" },
    });
    mount();
    openDoor(/^Add a blank row/, " ");
    expect(screen.getByText("Add empty rows")).toBeVisible();
    expect(
      screen.getByRole("textbox", { name: "No. of rows to create" }),
    ).toHaveValue("5");
    expect(h.post).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Add rows" }));
    await waitFor(() => expect(h.close).toHaveBeenCalledOnce());
    expect(h.post).toHaveBeenCalledExactlyOnceWith(
      EMPTY_PATH,
      { num_rows: 5 },
      { headers: { "Content-Type": "application/json" } },
    );
    expect(h.refresh).toHaveBeenCalledExactlyOnceWith(null, true);
    expect(screen.getByTestId("location")).toHaveTextContent(
      `/dashboard/develop/${DATASET}?tab=data`,
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expectNoStaging();
  });

  it("does not claim an empty-row save on a rejected request", async () => {
    h.post.mockRejectedValue(new Error("Empty rows rejected"));
    mount();
    openDoor(/^Add a blank row/);
    const action = screen.getByRole("button", { name: "Add rows" });
    fireEvent.click(action);
    await waitFor(() => expect(h.post).toHaveBeenCalledOnce());
    await waitFor(() => expect(action).toBeEnabled());
    expect(h.post).toHaveBeenCalledWith(
      EMPTY_PATH,
      { num_rows: 5 },
      { headers: { "Content-Type": "application/json" } },
    );
    expect(h.refresh).not.toHaveBeenCalled();
    expect(h.close).not.toHaveBeenCalled();
    expect(h.snackbar).not.toHaveBeenCalled();
    expect(screen.getByText("Add empty rows")).toBeVisible();
    expectNoStaging();
  });

  it("still stages a blank row in the versioned build", () => {
    setLaunchOverrideForTest({ launch: false });
    mount();
    openDoor(/^Add a blank row/);
    expect(h.blankAddOp).toHaveBeenCalledWith({
      op: "add_row",
      temp_id: expect.stringMatching(/^new-/),
      cells: {},
      split_ids: [],
    });
    expect(h.openRow).toHaveBeenCalledWith(
      h.blankAddOp.mock.calls[0][0].temp_id,
    );
    expect(h.post).not.toHaveBeenCalled();
    expect(h.refresh).not.toHaveBeenCalled();
  });

  it("retains the staged file preview/commit when import and version surfaces are shown", async () => {
    setLaunchOverrideForTest({ launch: false });
    h.post.mockImplementation((url, body) => {
      if (url !== IMPORT_PATH) throw new Error("Unexpected endpoint");
      return Promise.resolve({
        data: {
          result:
            body.op === "preview"
              ? { summary: { in_file: 1, add: 1, update: 0, invalid: 0 } }
              : {
                  operations: [
                    {
                      op: "add_row",
                      cells_by_column_id: { question: "hello" },
                    },
                  ],
                },
        },
      });
    });
    mount();
    openDoor(/^Upload a file/);
    await chooseFile();
    const body = {
      source: {
        kind: "file",
        file_name: "rows.csv",
        format: "csv",
        text: "question\nhello\n",
      },
      target: { dataset_id: DATASET },
      mapping: {
        roles: { question: "input" },
        flatten: false,
        parse_json: false,
        not_in_file: "keep",
        archived_matches: "keep",
      },
    };
    await waitFor(() =>
      expect(h.post).toHaveBeenCalledWith(IMPORT_PATH, {
        op: "preview",
        ...body,
      }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Add 1 row to pending changes" }),
    );
    await waitFor(() => expect(h.close).toHaveBeenCalledOnce());
    expect(h.post).toHaveBeenCalledWith(IMPORT_PATH, { op: "commit", ...body });
    expect(h.addOp).toHaveBeenCalledWith(
      DATASET,
      expect.objectContaining({ op: "add_row", cells: { question: "hello" } }),
    );
    expect(h.reveal).toHaveBeenCalledWith(DATASET, [
      h.addOp.mock.calls[0][1].temp_id,
    ]);
    expect(h.refresh).not.toHaveBeenCalled();
    expect(h.post.mock.calls.every(([url]) => url === IMPORT_PATH)).toBe(true);
  });
});
