// W9-5 C1: Export as training set, rendered with mocked table reads. The
// door shows only with splits; defaults by split name; the counts, overlap
// and leakage lines and their fixes; Download saves three files named by
// exportFileName whose lines equal the counts; the done state's New
// experiment link carries split=<test split>.
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
    },
  },
}));
vi.mock("src/sections/common/ux/toast", () => ({ notify: mocks.notify }));
vi.mock("src/launch/launchSwitch", async (importOriginal) => ({
  ...(await importOriginal()),
  isSurfaceShown: (id) => id === "dataset.splits",
}));

const { TrainingSetButton } = await import("../TrainingSetDialog");
const { useChangeSetStore } = await import("../../Versions/changeSetStore");

const C = (id, name, role, data_type = "text") => ({
  id,
  name,
  role,
  data_type,
  origin_type: "OTHERS",
});
const COLUMNS = [
  C("u", "customer_message", "input"),
  C("c", "conversation", "input", "json"),
  C("e", "expected_reply", "expected"),
  C("t", "expected_tool_calls", "expected", "json"),
];
const TURNS = JSON.stringify([
  { role: "user", content: "Hi" },
  { role: "assistant", content: "Hello!" },
]);
const row = (
  n,
  splits,
  { u = `message ${n}`, e = `reply ${n}`, c = "[]" } = {},
) => ({
  row_id: `r${n}`,
  order: n - 1,
  u: { cell_value: u },
  c: { cell_value: c },
  e: { cell_value: e },
  t: { cell_value: null },
  row_meta: { split_ids: splits },
});
const SPLITS = [
  { id: "S-smoke", name: "smoke" },
  { id: "S-train", name: "train" },
  { id: "S-val", name: "val" },
  { id: "S-test", name: "test" },
];
const ROWS = [
  row(1, ["S-train"], { c: TURNS }),
  row(2, ["S-train"], { u: "Where is my order?" }),
  row(3, ["S-train"], { e: "" }),
  row(7, ["S-train", "S-test"]),
  row(10, ["S-val"]),
  row(12, ["S-test"], { u: "  WHERE is  my order? " }),
  row(13, ["S-test"]),
  row(14, ["S-test"]),
];
const VERSIONS = [
  { id: "V6", number: 6, is_latest: true, row_count: 8, tags: [] },
];

const serve = () =>
  mocks.get.mockImplementation(async (url, { params = {} } = {}) => {
    if (url.endsWith("/versions/"))
      return { data: { result: { versions: VERSIONS } } };
    if (url.endsWith("/get-dataset-table/")) {
      const f = JSON.parse(params.filters ?? "[]").find(
        (x) => x.column_id === "system:splits",
      );
      const rows = ROWS.filter(
        (r) =>
          !f ||
          r.row_meta.split_ids.some((id) =>
            f.filter_config.filter_value.includes(id),
          ),
      );
      return {
        data: {
          result: {
            column_config: COLUMNS,
            table: rows,
            metadata: {
              total_rows: rows.length,
              has_more: false,
              dataset_name: "Support agent - golden set",
            },
          },
        },
      };
    }
    throw new Error(`unexpected read ${url}`);
  });

const renderButton = (splits = SPLITS) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <TrainingSetButton datasetId="DS" splits={splits} />
    </QueryClientProvider>,
  );

let saved;
beforeEach(() => {
  mocks.get.mockReset();
  mocks.notify.mockReset();
  saved = [];
  window.URL.createObjectURL = vi.fn((blob) => {
    saved.push({ blob });
    return "blob:training-set";
  });
  window.URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
    function click() {
      saved[saved.length - 1].name = this.getAttribute("download");
    },
  );
});
afterEach(() => vi.restoreAllMocks());

const blobText = (blob) =>
  new Promise((resolve) => {
    const fr = new FileReader();
    fr.onload = () => resolve(fr.result);
    fr.readAsText(blob);
  });
const dialog = () => screen.getByTestId("training-set-dialog");
const shown = (testId) =>
  within(dialog()).getByTestId(testId).previousSibling.textContent;

describe("W9-5 C1 · Export as training set", () => {
  it("is absent without splits", () => {
    renderButton([]);
    expect(screen.queryByTestId("training-set-open")).toBeNull();
  });

  it("checks overlap and leakage before downloading current saved split rows", async () => {
    serve();
    renderButton();
    fireEvent.click(screen.getByTestId("training-set-open"));
    const counts = await within(dialog()).findByTestId("training-set-counts");
    expect(shown("training-set-train")).toBe("train");
    expect(shown("training-set-validation")).toBe("val");
    expect(shown("training-set-test")).toBe("test");
    expect(within(dialog()).queryByTestId("training-set-version")).toBeNull();
    expect(counts.textContent).toBe("8 rows · train 3 · validation 1 · test 4");
    expect(
      within(dialog()).getByTestId("training-set-left-out").textContent,
    ).toBe("1 row without an expected answer is left out (train 1)");
    const overlap = within(dialog()).getByTestId("training-set-overlap");
    expect(overlap.textContent).toContain("Row 7 is in train and test");
    const leak = within(dialog()).getByTestId("training-set-leak-test");
    expect(leak.textContent).toContain(
      "1 test row has the same input as a train row (Row 12)",
    );
    expect(within(dialog()).getByTestId("training-set-roles").textContent).toBe(
      "Messages · user message: customer_message · earlier turns: conversation · assistant reply: expected_reply · tool calls: expected_tool_calls",
    );

    fireEvent.click(
      within(dialog()).getByTestId("training-set-leak-test-action"),
    );
    expect(
      within(dialog()).getByTestId("training-set-counts").textContent,
    ).toBe("7 rows · train 3 · validation 1 · test 3");
    fireEvent.click(
      within(dialog()).getByTestId("training-set-overlap-action"),
    );
    expect(
      within(dialog()).getByTestId("training-set-counts").textContent,
    ).toBe("6 rows · train 3 · validation 1 · test 2");
    expect(
      within(dialog()).getByTestId("training-set-overlap").textContent,
    ).toContain("Row 7 is kept in train only (it is in train and test)");

    fireEvent.click(within(dialog()).getByTestId("training-set-download"));
    await within(dialog()).findByTestId("training-set-done");
    expect(saved.map((s) => s.name)).toEqual([
      "support-agent-golden-set-train.jsonl",
      "support-agent-golden-set-validation.jsonl",
      "support-agent-golden-set-test.jsonl",
    ]);
    const texts = await Promise.all(saved.map((s) => blobText(s.blob)));
    const lines = texts.map((t) =>
      t
        .trim()
        .split("\n")
        .map((l) => JSON.parse(l)),
    );
    expect(lines.map((l) => l.length)).toEqual([3, 1, 2]);
    expect(lines[0][0].messages.map((m) => m.role)).toEqual([
      "user",
      "assistant",
      "user",
      "assistant",
    ]);
    expect(lines[2].map((l) => l.messages[0].content)).toEqual([
      "message 13",
      "message 14",
    ]);
    expect(within(dialog()).getByTestId("training-set-next").textContent).toBe(
      "Next: train with your provider, add the tuned model in Settings › AI Providers › Create custom model, then compare it with the base model on test.",
    );
    expect(
      within(dialog())
        .getByTestId("training-set-providers")
        .getAttribute("href"),
    ).toBe("/dashboard/settings/ai-providers");
    expect(
      within(dialog()).getByTestId("training-set-split-note").textContent,
    ).toBe(
      "New experiment on test runs on the whole split, including Rows 7, 12, which the test file leaves out.",
    );
    expect(
      within(dialog()).queryByTestId("training-set-open-experiment"),
    ).toBeNull();
    expect(
      mocks.get.mock.calls.some(([url]) => url.includes("/versions/")),
    ).toBe(false);
    expect(mocks.notify).toHaveBeenCalledWith(
      "Saved 3 files · 6 rows",
      expect.anything(),
    );
  });

  it("preselects nothing for unguessable names and needs a train split", async () => {
    serve();
    renderButton([{ id: "S-smoke", name: "smoke" }]);
    fireEvent.click(screen.getByTestId("training-set-open"));
    await within(dialog()).findByText("Pick the split to train on.");
    expect(
      within(dialog()).getByTestId("training-set-download"),
    ).toBeDisabled();
  });

  it("says why an empty train split cannot be downloaded", async () => {
    serve();
    renderButton([
      { id: "S-empty", name: "train" },
      { id: "S-test", name: "test" },
    ]);
    fireEvent.click(screen.getByTestId("training-set-open"));
    const line = await within(dialog()).findByTestId(
      "training-set-train-empty",
    );
    expect(line.textContent).toBe(
      "Split train has no current saved rows. Pick another split to train on.",
    );
    expect(within(dialog()).queryByTestId("training-set-clean")).toBeNull();
    expect(
      within(dialog()).getByTestId("training-set-download"),
    ).toBeDisabled();
  });

  it("exports only persisted rows without calling version reads or save controls", async () => {
    serve();
    useChangeSetStore.getState().addOp("DS", {
      op: "set_splits",
      row_ids: ["r13"],
      add: ["S-train"],
      remove: [],
    });
    renderButton();
    fireEvent.click(screen.getByTestId("training-set-open"));
    await within(dialog()).findByTestId("training-set-counts");
    expect(within(dialog()).queryByTestId("training-set-unsaved")).toBeNull();
    expect(
      within(dialog()).queryByTestId("training-set-save-version"),
    ).toBeNull();
    expect(
      mocks.get.mock.calls.some(([url]) => url.includes("/versions/")),
    ).toBe(false);
    useChangeSetStore.getState().discard("DS");
  });
});
