// W3-6 B2 / B3 dialogs, rendered with mocked reads: the Rows default (the
// grid's filter), the size line against the file really saved, Download
// on and off, the older-version line, the OpenAI formats' blockers, and the
// results dialog's "This view" taking the grid's search from its own read.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { render } from "src/utils/test-utils";

const mocks = vi.hoisted(() => ({ get: vi.fn(), notify: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get },
  endpoints: {
    develop: {
      getDatasetDetail: (id) => `/model-hub/develops/${id}/get-dataset-table/`,
      versions: {
        list: (id) => `/model-hub/datasets/${id}/versions/`,
        table: (id, v) => `/model-hub/datasets/${id}/versions/${v}/table/`,
      },
      splits: { list: (id) => `/model-hub/datasets/${id}/splits/` },
      datasetRuns: { results: (id) => `/model-hub/datasets/${id}/results/` },
    },
  },
}));
vi.mock("src/sections/common/ux/toast", () => ({ notify: mocks.notify }));

const { DatasetExportDialog, ResultsExportDialog } = await import(
  "../ExportDialog"
);
const { resultsViewOf } = await import("../resultsExport");
const { useRunResults } = await import("src/api/develop/run-results");
const { sizeText } = await import("../writers");

// ------------------------------------------------------------ the world
const C = (id, name, role, extra = {}) => ({
  id,
  name,
  role,
  data_type: "text",
  origin_type: "OTHERS",
  ...extra,
});
const INPUT = C("ci", "customer_message", "input");
const EXPECTED = C("ce", "expected_reply", "expected");
const TIER = C("ct", "customer_tier", "metadata");
const DRAFT = C("co", "Draft reply", "output", { origin_type: "run_prompt" });
const CORRECT = C("cc", "Correctness", "eval", {
  origin_type: "evaluation",
  data_type: "boolean",
  source_id: "M1",
});
const FULL = [INPUT, EXPECTED, TIER, DRAFT, CORRECT];
const row = (i, { split = [], expected = `reply ${i}` } = {}) => ({
  row_id: `r${i}`,
  order: i,
  ci: { cell_value: i % 2 ? `refund ${i}` : `where is ${i}` },
  ce: { cell_value: expected },
  ct: { cell_value: "pro" },
  co: { cell_value: `draft ${i}` },
  cc: {
    cell_value: i % 2 ? "Passed" : "Failed",
    value_infos: { reason: `why ${i}` },
  },
  row_meta: { split_ids: split, metadata: {} },
});
const LATEST_ROWS = [
  row(0, { split: ["S1"] }),
  row(1),
  row(2),
  row(3, { split: ["S1"] }),
];
const V3_ROWS = [row(0, { split: ["S1"] }), row(1)].map((r) => {
  const { co: _co, cc: _cc, ...user } = r;
  return user;
});
const VERSIONS = [
  { id: "V6", number: 6, is_latest: true, row_count: 4, tags: [] },
  { id: "V3", number: 3, is_latest: false, row_count: 2, tags: ["golden"] },
];
const SPLITS = [
  { id: "S1", name: "smoke" },
  { id: "S2", name: "empty" },
];

const narrow = (rows, params) => {
  const filters = JSON.parse(params.filters ?? "[]");
  const split = filters.find((f) => f.column_id === "system:splits");
  const search = params.search ? JSON.parse(params.search).key : "";
  return rows.filter(
    (r) =>
      (!split ||
        r.row_meta.split_ids.some((id) =>
          split.filter_config.filter_value.includes(id),
        )) &&
      (!search || r.ci.cell_value.includes(search)),
  );
};
const page = (rows, columns, params) => {
  const all = narrow(rows, params);
  const size = params.page_size;
  const start = params.current_page_index * size;
  return {
    data: {
      result: {
        column_config: columns,
        table: all.slice(start, start + size),
        metadata: {
          total_rows: all.length,
          dataset_name: "Support agent - golden set",
          has_more: start + size < all.length,
        },
      },
    },
  };
};

let world;
const serveDataset = () =>
  mocks.get.mockImplementation(async (url, { params = {} } = {}) => {
    if (url.endsWith("/versions/"))
      return { data: { result: { versions: VERSIONS } } };
    if (url.endsWith("/splits/"))
      return { data: { result: { splits: SPLITS } } };
    if (url.includes("/versions/V3/table/"))
      return page(
        V3_ROWS,
        world.columns
          .filter((c) => c.origin_type === "OTHERS")
          .map(({ role: _r, ...c }) => c),
        params,
      );
    if (url.endsWith("/get-dataset-table/")) {
      if (params.column_config_only)
        return { data: { result: { column_config: world.columns } } };
      return page(world.rows, world.columns, params);
    }
    throw new Error(`unexpected read ${url}`);
  });

const renderWith = (ui) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
};

let saved;
beforeEach(() => {
  world = { columns: FULL, rows: LATEST_ROWS };
  mocks.get.mockReset();
  mocks.notify.mockReset();
  saved = [];
  window.URL.createObjectURL = vi.fn((blob) => {
    saved.push(blob);
    return "blob:export";
  });
  window.URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
    function click() {
      saved.name = this.getAttribute("download");
    },
  );
});
afterEach(() => {
  vi.restoreAllMocks();
});

const blobText = (blob) =>
  new Promise((resolve) => {
    const fr = new FileReader();
    fr.onload = () => resolve(fr.result);
    fr.readAsText(blob);
  });
const dialog = () => screen.getByTestId("dataset-export-dialog");
const sizeLine = () => within(dialog()).findByTestId("export-size");
const downloadButton = () => within(dialog()).getByTestId("export-download");
const pick = async (testId, optionText) => {
  fireEvent.mouseDown(within(dialog()).getByTestId(testId).previousSibling);
  const list = await screen.findByRole("listbox");
  const options = within(list)
    .getAllByRole("option")
    .map((o) => o.textContent);
  fireEvent.click(within(list).getByText(optionText));
  return options;
};

describe("W3-6 B2 · dataset Download… dialog", () => {
  it("with a grid filter, 'The current filter (n)' is the default; the size line is the saved file's size", async () => {
    serveDataset();
    renderWith(
      <DatasetExportDialog
        open
        onClose={() => {}}
        datasetId="DS"
        currentFilter={{ filters: [], search: "refund" }}
      />,
    );
    const line = await sizeLine();
    expect(
      within(dialog()).getByTestId("export-rows").previousSibling.textContent,
    ).toBe("The current filter (2)");
    await waitFor(() => expect(downloadButton()).toBeEnabled());
    const claimed = line.textContent;
    const fileName =
      within(dialog()).getByTestId("export-file-name").textContent;
    expect(fileName).toBe("support-agent-golden-set-v6-filtered.csv");
    fireEvent.click(downloadButton());
    expect(saved).toHaveLength(1);
    expect(saved.name).toBe(fileName);
    expect(claimed).toBe(`2 rows · about ${sizeText(saved[0].size)}`);
    const text = await blobText(saved[0]);
    const lines = text.trimEnd().split("\r\n");
    expect(lines).toHaveLength(3);
    expect(lines.slice(1).every((l) => l.includes("refund"))).toBe(true);
    expect(mocks.notify).toHaveBeenCalledWith(
      expect.stringMatching(
        /^Saved support-agent-golden-set-v6-filtered\.csv · 2 rows · /,
      ),
      expect.anything(),
    );
  });

  it("without a filter the default is All; splits are counted and a split with 0 rows is not listed", async () => {
    serveDataset();
    renderWith(<DatasetExportDialog open onClose={() => {}} datasetId="DS" />);
    await sizeLine();
    expect(
      within(dialog()).getByTestId("export-rows").previousSibling.textContent,
    ).toBe("All 4");
    await waitFor(() =>
      expect(
        mocks.get.mock.calls.filter(([, o]) => o?.params?.page_size === 1)
          .length,
      ).toBe(2),
    );
    const options = await pick("export-rows", /^Split smoke/);
    expect(options).toEqual(["All 4", "Split smoke (2)"]);
    await waitFor(() =>
      expect(within(dialog()).getByTestId("export-file-name").textContent).toBe(
        "support-agent-golden-set-v6-smoke.csv",
      ),
    );
    expect((await sizeLine()).textContent).toMatch(/^2 rows · about /);
  });

  it("an older version: the generated columns go, with 'Generated columns exist only on the latest version'", async () => {
    serveDataset();
    renderWith(<DatasetExportDialog open onClose={() => {}} datasetId="DS" />);
    await sizeLine();
    const generated = within(dialog()).getByTestId("export-generated");
    expect(generated).toBeChecked();
    expect(generated).toBeEnabled();
    expect(
      within(dialog()).getByText(
        "Generated columns (Draft reply, Correctness)",
      ),
    ).toBeInTheDocument();
    expect(
      within(dialog()).queryByText(
        "Generated columns exist only on the latest version",
      ),
    ).toBeNull();
    await pick("export-version", /^v3/);
    await within(dialog()).findByText(
      "Generated columns exist only on the latest version",
    );
    await waitFor(() =>
      expect(within(dialog()).getByTestId("export-file-name").textContent).toBe(
        "support-agent-golden-set-v3.csv",
      ),
    );
    expect(
      mocks.get.mock.calls.some(([u]) =>
        u.includes("/datasets/DS/versions/V3/table/"),
      ),
    ).toBe(true);
    await waitFor(() => expect(downloadButton()).toBeEnabled());
    fireEvent.click(downloadButton());
    const header = (await blobText(saved[0])).split("\r\n")[0];
    expect(header).toBe(
      "row_id,row,splits,customer_message,expected_reply,customer_tier",
    );
  });

  it("the OpenAI formats need an Expected column and an Input column", async () => {
    world = { columns: [INPUT, TIER, DRAFT], rows: LATEST_ROWS };
    serveDataset();
    const view = renderWith(
      <DatasetExportDialog open onClose={() => {}} datasetId="DS" />,
    );
    await sizeLine();
    const radio = (label) =>
      within(dialog()).getByText(label).closest("label").querySelector("input");
    expect(radio("OpenAI fine-tune JSONL")).toBeDisabled();
    expect(radio("OpenAI Evals JSONL")).toBeDisabled();
    expect(
      within(dialog()).getAllByText("Needs an Expected column"),
    ).toHaveLength(2);
    expect(radio("CSV")).toBeEnabled();
    view.unmount();

    world = { columns: [EXPECTED, TIER], rows: LATEST_ROWS };
    renderWith(<DatasetExportDialog open onClose={() => {}} datasetId="DS2" />);
    await sizeLine();
    expect(within(dialog()).getAllByText("Needs an Input column")).toHaveLength(
      2,
    );
    expect(radio("OpenAI fine-tune JSONL")).toBeDisabled();
  });

  it("Download is off while the rows are read and when the choice has no line", async () => {
    let release;
    const gate = new Promise((r) => {
      release = r;
    });
    serveDataset();
    const serve = mocks.get.getMockImplementation();
    mocks.get.mockImplementation(async (url, opts) => {
      if (
        url.endsWith("/get-dataset-table/") &&
        opts?.params?.page_size === 500
      )
        await gate;
      return serve(url, opts);
    });
    renderWith(
      <DatasetExportDialog
        open
        onClose={() => {}}
        datasetId="DS"
        currentFilter={{ filters: [], search: "nothing matches" }}
      />,
    );
    await within(dialog()).findByText(/^Reading rows/);
    expect(downloadButton()).toBeDisabled();
    release();
    await waitFor(() =>
      expect(
        within(dialog()).getByTestId("export-rows").previousSibling.textContent,
      ).toBe("The current filter (0)"),
    );
    await waitFor(() =>
      expect(within(dialog()).queryByText(/^Reading rows/)).toBeNull(),
    );
    expect(downloadButton()).toBeDisabled();
    await pick("export-rows", "All 4");
    await sizeLine();
    await waitFor(() => expect(downloadButton()).toBeEnabled());
  });

  it("fine-tune lines leave out rows without an expected value and say how many", async () => {
    world = {
      columns: FULL,
      rows: [row(0), row(1, { expected: "" }), row(2)],
    };
    serveDataset();
    renderWith(<DatasetExportDialog open onClose={() => {}} datasetId="DS" />);
    await sizeLine();
    fireEvent.click(within(dialog()).getByText("OpenAI fine-tune JSONL"));
    await within(dialog()).findByTestId("export-roles");
    expect(within(dialog()).getByTestId("export-skipped").textContent).toBe(
      "1 row without an expected value is left out",
    );
    expect((await sizeLine()).textContent).toMatch(/^2 rows · about /);
    fireEvent.click(downloadButton());
    const lines = (await blobText(saved[0])).trimEnd().split("\n");
    expect(lines).toHaveLength(2);
    expect(saved.name).toBe("support-agent-golden-set-v6-finetune.jsonl");
  });
});

// ------------------------------------------------------- results dialog
const RUNS = [
  {
    run_id: "RA",
    label: "#2A",
    dataset_version: { number: 5 },
    status: "Completed",
  },
  {
    run_id: "RB",
    label: "#2B",
    dataset_version: { number: 5 },
    status: "Completed",
  },
];
const R_SUMMARY = {
  baseline: "RA",
  runs: RUNS,
  metrics: [{ key: "eval:correctness", name: "Correctness", kind: "eval" }],
};
const serveResults = () =>
  mocks.get.mockImplementation(async (url, { params = {} } = {}) => {
    if (url.endsWith("/get-dataset-table/"))
      return {
        data: { result: { metadata: { dataset_name: "GS" }, table: [] } },
      };
    const total = params.search ? 19 : params.filters ? 57 : 238;
    return { data: { result: { total, rows: [], summary: R_SUMMARY } } };
  });
const PILLS = [{ type: "fails", metric: "eval:correctness", run: "any_run" }];
const URL_SEARCH = `?filters=${encodeURIComponent(JSON.stringify(PILLS))}&sort=${encodeURIComponent("latency:RB")}`;

// The page: the grid's live read, then More › Download… (the dialog opens
// once the grid has its rows, as it does on the page).
function Page({ gridView, pageView }) {
  const q = useRunResults("DS", gridView);
  const [open, setOpen] = React.useState(false);
  return (
    <>
      <div data-testid="grid-total">{q.data?.total ?? ""}</div>
      <button type="button" onClick={() => setOpen(true)}>
        Open download
      </button>
      <ResultsExportDialog
        open={open}
        onClose={() => setOpen(false)}
        datasetId="DS"
        view={pageView}
      />
    </>
  );
}
Page.propTypes = { gridView: () => null, pageView: () => null };
const openWhenGridHas = async (n) => {
  await waitFor(() =>
    expect(screen.getByTestId("grid-total").textContent).toBe(String(n)),
  );
  fireEvent.click(screen.getByText("Open download"));
};

describe("W3-6 B3 · results Download… takes the grid's search (M1)", () => {
  const urlView = () =>
    resultsViewOf({
      search: URL_SEARCH,
      runIds: ["RA", "RB"],
      baselineId: "RA",
    });

  it("the grid searched for 'refund': This view counts the grid's 19 rows and names the search", async () => {
    serveResults();
    const v = urlView();
    renderWith(<Page gridView={{ ...v, search: "refund" }} pageView={v} />);
    await openWhenGridHas(19);
    const viewOption = await screen.findByTestId("results-export-scope-view");
    await waitFor(() =>
      expect(viewOption.textContent).toBe(
        'This view: 19 rows (Fails Correctness · any run · search "refund" · sorted by Slowest · #2B)',
      ),
    );
    expect(viewOption.querySelector("input")).toBeChecked();
    expect(screen.getByTestId("results-export-scope-all").textContent).toBe(
      "Every row (238)",
    );
  });

  it("no search: This view is the URL's filter (57)", async () => {
    serveResults();
    const v = urlView();
    renderWith(<Page gridView={v} pageView={v} />);
    await openWhenGridHas(57);
    const viewOption = await screen.findByTestId("results-export-scope-view");
    await waitFor(() =>
      expect(viewOption.textContent).toMatch(/^This view: 57 rows/),
    );
  });

  it("a grid read that can't be matched: no 'This view', Every row is the choice", async () => {
    serveResults();
    const v = urlView();
    renderWith(
      <Page
        gridView={{ ...v, sort: "cost:RA", search: "refund" }}
        pageView={v}
      />,
    );
    await openWhenGridHas(19);
    const all = await screen.findByTestId("results-export-scope-all");
    await waitFor(() => expect(all.textContent).toBe("Every row (238)"));
    expect(screen.queryByTestId("results-export-scope-view")).toBeNull();
    expect(all.querySelector("input")).toBeChecked();
  });
});
