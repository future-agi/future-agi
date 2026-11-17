// "Save as a column…" in Filter › Expression…, where this build has no route
// for custom columns: it stays in the dialog, turned off with the shared
// line, also once the expression counts; a click opens no dialog; nothing
// asks for the columns, and none is saved. Where the routes are offered, it
// opens Add a column with the expression, as before.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import ResultsExpressionDialog from "../ResultsExpressionDialog";

const COLUMNS = "/model-hub/datasets/{dataset_id}/result-columns/";
const VALUES = "/model-hub/datasets/{dataset_id}/result-columns/values/";

const h = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), absent: null }));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { ...actual.default, get: h.get, put: h.put },
  };
});
// A case names the routes this build lacks; otherwise the real list.
vi.mock("src/launch/absentPathGuard", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    absentTemplateOf: (url, ...rest) =>
      h.absent
        ? h.absent.includes(url)
          ? url
          : null
        : actual.absentTemplateOf(url, ...rest),
  };
});
vi.mock("src/api/develop/run-results", async (importOriginal) => ({
  ...(await importOriginal()),
  useRunResults: () => ({ data: null }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

// Two runs in view; the page passes the Filter menu's view.
const VIEW = {
  datasetId: "d1",
  runs: [
    { run_id: "r1", label: "#1A" },
    { run_id: "r2", label: "#1B" },
  ],
  baselineId: "r1",
  metrics: [],
  metadataKeys: ["language"],
};
const EXPR = 'metadata.language = "es"';
const WAIT = { timeout: 3000 };

const resultColumnsCalls = () =>
  [...h.get.mock.calls, ...h.put.mock.calls].filter(([url]) =>
    String(url).includes("/result-columns/"),
  );

let client;
const mount = () =>
  render(
    <QueryClientProvider client={client}>
      <ResultsExpressionDialog
        open
        onClose={() => {}}
        onApply={() => {}}
        datasetId="d1"
        view={VIEW}
      />
    </QueryClientProvider>,
  );
// Types an expression and waits until it counts: Apply turns on, and with
// it "Save as a column…" wherever that door is open.
const typeAndCount = async () => {
  fireEvent.change(screen.getByTestId("expression-input"), {
    target: { value: EXPR },
  });
  await waitFor(
    () => expect(screen.getByTestId("expression-apply")).toBeEnabled(),
    WAIT,
  );
};
const pressSaveAsColumn = async () => {
  fireEvent.click(screen.getByTestId("expression-save-column"));
  await act(async () => {});
};
// Where a column dialog did open, goes on through it: a name, then Save.
const saveThroughColumnDialog = async () => {
  if (!screen.queryByTestId("custom-column-dialog")) return;
  fireEvent.change(screen.getByTestId("custom-column-name"), {
    target: { value: "es_rows" },
  });
  await waitFor(
    () => expect(screen.getByTestId("custom-column-save")).toBeEnabled(),
    WAIT,
  );
  fireEvent.click(screen.getByTestId("custom-column-save"));
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  h.get.mockReset().mockResolvedValue({ data: { result: { columns: [] } } });
  h.put.mockReset().mockResolvedValue({ data: { result: { columns: [] } } });
  h.absent = null;
});
afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
});

describe("Save as a column… where custom columns have no route", () => {
  beforeEach(() => {
    setLaunchOverrideForTest({ launch: true });
    h.absent = [COLUMNS, VALUES];
  });

  it("stays in the dialog, turned off with the shared line", async () => {
    mount();
    await typeAndCount();
    const save = screen.getByRole("button", { name: "Save as a column…" });
    expect(save).toBeDisabled();
    expect(save).toHaveAttribute("aria-disabled", "true");
    expect(save).toHaveAccessibleDescription(NOT_IN_RELEASE);
  });

  it("opens no dialog on a click", async () => {
    mount();
    await typeAndCount();
    await pressSaveAsColumn();
    expect(screen.queryByTestId("custom-column-dialog")).toBeNull();
  });

  it("asks for no columns and saves none", async () => {
    mount();
    await typeAndCount();
    await pressSaveAsColumn();
    await saveThroughColumnDialog();
    expect(resultColumnsCalls()).toEqual([]);
  });
});

describe("Save as a column… where custom columns are offered", () => {
  it("opens Add a column with the expression, and asks for the columns", async () => {
    setLaunchOverrideForTest({ launch: true });
    h.absent = [];
    mount();
    await typeAndCount();
    expect(
      screen.getByRole("button", { name: "Save as a column…" }),
    ).toBeEnabled();
    await pressSaveAsColumn();
    expect(await screen.findByTestId("custom-column-dialog")).toBeTruthy();
    expect(screen.getByTestId("custom-column-expr")).toHaveValue(EXPR);
    await waitFor(() => expect(resultColumnsCalls().length).toBeGreaterThan(0));
  });
});
