/* eslint-disable react/prop-types */
import React from "react";
import { MemoryRouter } from "react-router-dom";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetAllStores } from "src/utils/zustand";
import SelectionBar from "../SelectionBar";

const h = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  native: vi.fn(),
  uuid: vi.fn(),
  canRun: true,
  canDownload: true,
}));
vi.mock("src/utils/axios", () => ({
  default: { get: h.get, post: h.post },
}));
vi.mock("src/api/develop/run-results", () => ({
  useRerunResultCells: () => ({ request: h.native }),
}));
vi.mock("src/sections/common/ux/oneview/ActionBar", () => ({
  default: ({ actions }) => (
    <div>
      {actions.map((action) =>
        action.menu ? (
          <fieldset key={action.id}>
            <legend>{action.label}</legend>
            {action.menu.map((item) => (
              <button key={item.id} disabled={item.disabled} onClick={item.run}>
                {item.label}
              </button>
            ))}
          </fieldset>
        ) : (
          <button
            key={action.id}
            disabled={action.disabled}
            onClick={action.run}
          >
            {action.label}
          </button>
        ),
      )}
    </div>
  ),
}));
vi.mock("src/sections/common/Access/useDatasetAccess", () => ({
  default: () => ({
    can: { run: h.canRun, edit: true, download: h.canDownload },
    reason: () => "Run access required",
  }),
}));
vi.mock("src/sections/common/Runs/useRescore", () => ({
  useRescore: () => ({ dialog: null }),
}));
vi.mock("src/sections/common/Runs/rescoreCopyModel", () => ({
  scoreWithChoices: () => [],
}));
vi.mock("src/sections/common/Selection/useSelectAllMatching", () => ({
  useSelectAllMatching: ({ selectedIds }) => ({ ids: selectedIds }),
}));
vi.mock("src/sections/common/Selection/SelectionCount", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/Selection/askFalconAboutRows", () => ({
  askFalconAboutRows: vi.fn(),
}));
vi.mock("src/sections/common/Collab/AssignDialog", () => ({
  default: () => null,
}));
vi.mock("src/sections/develop-detail/Review/StartReviewDialog", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/export/ExportDialog", () => ({
  ResultsExportDialog: () => null,
}));
vi.mock("../AddResultsToDatasetDialog", () => ({ default: () => null }));
vi.mock("../resultsCollabActions", () => ({ resultsCollabActions: () => [] }));
vi.mock("../../iterate/iterateModel", () => ({ canIterate: () => false }));
vi.mock("src/sections/common/RunSources/rerunBus", () => ({
  openRerunWithChanges: vi.fn(),
}));

const run = {
  run_id: "source-run",
  label: "#2",
  task_kind: "external",
  source: "external",
  kind: "execution",
  experiment_id: null,
};
const identities = [
  { example_id: "a", trial_number: 2 },
  { example_id: "b", trial_number: 5 },
];
const source = {
  id: run.run_id,
  task_kind: "external",
  dataset_version_id: "version-pinned",
  expected_items: identities,
  model_config: { model: "fixture" },
  metadata: { code_revision: "revision" },
  experiment_id: null,
  evaluator_bindings: [{ metric_id: "metric-a", evaluator_version_id: "old" }],
};
const newRunId = "90d5a374-6e50-4578-81fa-56cf3ca8dd01";
const ok = (result) => ({ data: { status: true, result } });
const mount = (props = {}) =>
  render(
    <MemoryRouter>
      <SelectionBar
        datasetId="dataset"
        selectedRows={[{ example_id: "b" }]}
        runs={[run]}
        onClear={() => {}}
        {...props}
      />
    </MemoryRouter>,
  );
const open = (scope = "Selected rows") =>
  fireEvent.click(screen.getByRole("button", { name: `${scope}, #2` }));
const prepare = async () =>
  waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Create run record" }).disabled,
    ).toBe(false),
  );

beforeEach(() => {
  vi.resetAllMocks();
  h.canRun = true;
  h.canDownload = true;
  h.uuid
    .mockReturnValueOnce(newRunId)
    .mockReturnValueOnce("90d5a374-6e50-4578-81fa-56cf3ca8dd02");
  vi.stubGlobal("crypto", { randomUUID: h.uuid });
  h.get.mockImplementation((url) =>
    Promise.resolve(
      url.includes("/versions/")
        ? ok({
            version: {
              id: "version-pinned",
              dataset: "dataset",
              item_count: 2,
            },
            items: [
              { example_id: "a", input: "pinned A", expected: null },
              { example_id: "b", input: "pinned B", expected: null },
            ],
            offset: 0,
            limit: 500,
            has_more: false,
          })
        : ok(source),
    ),
  );
  h.post.mockImplementation((url, body) =>
    Promise.resolve(
      ok({
        ...source,
        id: body.run_id,
        expected_items: body.expected_items,
        evaluator_bindings: [
          {
            metric_id: "metric-a",
            evaluator_version_id: "new-pin",
            version_number: 4,
          },
        ],
      }),
    ),
  );
});
afterEach(() => {
  cleanup();
  resetAllStores();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Results external execution handoff", () => {
  it.each([
    ["Selected rows", [identities[1]]],
    ["Full run", identities],
  ])(
    "opens %s without executing and records the exact plan on confirmation",
    async (scope, expected) => {
      mount();
      expect(screen.queryByText("Re-run")).toBeNull();
      open(scope);
      await prepare();
      const downloads = [];
      const revoke = vi.fn();
      vi.stubGlobal(
        "Blob",
        class extends Blob {
          constructor(parts, options) {
            super(parts, options);
            downloads.push(JSON.parse(parts[0]));
          }
        },
      );
      vi.stubGlobal(
        "URL",
        class extends URL {
          static createObjectURL() {
            return "blob:handoff";
          }
          static revokeObjectURL = revoke;
        },
      );
      vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
        () => {},
      );
      fireEvent.click(
        screen.getByRole("button", { name: "Download handoff JSON" }),
      );
      expect(downloads[0].recipe).toBe(
        screen.getByLabelText("REST recipe").value,
      );
      expect(
        screen
          .getByRole("dialog")
          .textContent.includes(
            "Execution happens in your code, not in Future AGI.",
          ),
      ).toBe(true);
      expect(
        screen.getByLabelText("REST recipe").value.split("\n").slice(2, 5),
      ).toEqual([
        "POST /model-hub/experiment-runs/ with handoff.body.",
        "Create retries must use the same authenticated principal as the original create.",
        "Inspect result.evaluator_bindings: a new run resolves current evaluator pins.",
      ]);
      expect(h.post.mock.calls).toEqual([]);
      fireEvent.click(
        screen.getByRole("button", { name: "Create run record" }),
      );
      await screen.findByText("Run record created. No code was executed.");
      expect(h.post.mock.calls.map(([url, body]) => [url, body])).toEqual([
        [
          "/model-hub/experiment-runs/",
          {
            run_id: newRunId,
            dataset_version_id: "version-pinned",
            experiment_id: null,
            expected_items: expected,
            model_config: { model: "fixture" },
            metadata: { code_revision: "revision" },
            evaluator_metric_ids: ["metric-a"],
          },
        ],
      ]);
      expect(screen.getByTestId("created-evaluator-pins").textContent).toBe(
        "metric-a → new-pin (v4)",
      );
      const recipe = screen.getByLabelText("REST recipe").value.split("\n");
      expect(recipe.slice(2, 4)).toEqual([
        `GET /model-hub/experiment-runs/${newRunId}/ and inspect result.evaluator_bindings.`,
        "This record was created in the UI. Do not replay creation from your code.",
      ]);
      expect(recipe.filter((line) => line.startsWith("POST "))).toEqual([
        `POST /model-hub/experiment-runs/${newRunId}/results/ with:`,
      ]);
      fireEvent.click(
        screen.getByRole("button", { name: "Download handoff JSON" }),
      );
      expect(downloads[1]).toEqual({
        ...downloads[0],
        recipe: screen.getByLabelText("REST recipe").value,
      });
      expect(downloads[1].body).toEqual(h.post.mock.calls[0][1]);
      await waitFor(() =>
        expect(revoke.mock.calls).toEqual([["blob:handoff"], ["blob:handoff"]]),
      );
      expect(h.native.mock.calls).toEqual([]);
    },
  );

  it("retains the same handoff through an interrupted create, close and remount", async () => {
    h.post.mockRejectedValueOnce(new Error("connection interrupted"));
    mount();
    open();
    await prepare();
    const button = screen.getByRole("button", { name: "Create run record" });
    fireEvent.click(button);
    fireEvent.click(button);
    await screen.findByRole("button", { name: "Retry same create" });
    expect(h.post.mock.calls.length).toBe(1);
    const firstBody = h.post.mock.calls[0][1];
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    cleanup();
    mount();
    open("Full run");
    expect(screen.getByText("Selected rows · #2").textContent).toBe(
      "Selected rows · #2",
    );
    fireEvent.click(screen.getByRole("button", { name: "Retry same create" }));
    await screen.findByText("Run record created. No code was executed.");
    expect(h.post.mock.calls.map((call) => call[1])).toEqual([
      firstBody,
      firstBody,
    ]);
    expect(h.get.mock.calls.length).toBe(2);
    expect(h.uuid.mock.calls.length).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    open("Full run");
    await prepare();
    fireEvent.click(screen.getByRole("button", { name: "Create run record" }));
    await screen.findByText("Run record created. No code was executed.");
    expect(h.post.mock.calls[2][1].run_id).toBe(
      "90d5a374-6e50-4578-81fa-56cf3ca8dd02",
    );
    expect(h.post.mock.calls[2][1].expected_items).toEqual(identities);
  });

  it("retains an unresolved create through later refusals and remounts until confirmed", async () => {
    h.post.mockRejectedValueOnce(new Error("connection interrupted"));
    mount();
    open();
    await prepare();
    fireEvent.click(screen.getByRole("button", { name: "Create run record" }));
    await screen.findByRole("button", { name: "Retry same create" });
    const request = [
      "/model-hub/experiment-runs/",
      {
        run_id: newRunId,
        dataset_version_id: "version-pinned",
        experiment_id: null,
        expected_items: [identities[1]],
        evaluator_metric_ids: ["metric-a"],
        model_config: { model: "fixture" },
        metadata: { code_revision: "revision" },
      },
      { headers: { "Content-Type": "application/json" } },
    ];
    const originalRecipe = screen.getByLabelText("REST recipe").value;
    h.get.mockRejectedValue(new Error("source no longer readable"));
    const requests = [request];
    for (const status of [403, 429, 400, 401, 404, 409, 422]) {
      h.post.mockRejectedValueOnce({ response: { status } });
      fireEvent.click(
        screen.getByRole("button", { name: "Retry same create" }),
      );
      await waitFor(() =>
        expect(
          screen.getByRole("button", { name: "Retry same create" }).disabled,
        ).toBe(false),
      );
      requests.push(request);
      expect(h.post.mock.calls).toEqual(requests);
      expect(
        screen.getByText(
          "Could not confirm creation. Retry the same UUID and body; no code was executed.",
        ).textContent,
      ).toBe(
        "Could not confirm creation. Retry the same UUID and body; no code was executed.",
      );
      fireEvent.click(screen.getByRole("button", { name: "Close" }));
      cleanup();
      mount({ runs: [{ ...run, run_id: "another-source" }] });
      open("Full run");
      expect(screen.getByText("Selected rows · #2").textContent).toBe(
        "Selected rows · #2",
      );
      expect(screen.getByLabelText("REST recipe").value).toBe(originalRecipe);
      expect(h.uuid.mock.calls.length).toBe(1);
      expect(h.get.mock.calls.length).toBe(2);
    }
    fireEvent.click(screen.getByRole("button", { name: "Retry same create" }));
    await screen.findByText("Run record created. No code was executed.");
    expect(h.post.mock.calls).toEqual([...requests, request]);
    expect(h.uuid.mock.calls.length).toBe(1);
    expect(h.get.mock.calls.length).toBe(2);
    expect(h.native.mock.calls).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    cleanup();
    mount();
    open("Full run");
    await screen.findByText(
      "Could not read the pinned run and inputs. Check access and retry.",
    );
    expect(h.uuid.mock.calls.length).toBe(2);
  });

  it.each(["read-only", "no run access", "no download access"])(
    "respects %s",
    (gate) => {
      h.canRun = gate !== "no run access";
      h.canDownload = gate !== "no download access";
      mount({ readOnly: gate === "read-only" });
      expect(
        screen.getByRole("button", { name: "Selected rows, #2" }).disabled,
      ).toBe(true);
      expect(
        screen.getByRole("button", { name: "Full run, #2" }).disabled,
      ).toBe(true);
      open();
      expect(h.get.mock.calls).toEqual([]);
      expect(h.post.mock.calls).toEqual([]);
    },
  );

  it("refuses a selection outside the source plan and closing sends nothing", async () => {
    mount({ selectedRows: [{ example_id: "not-planned" }] });
    open();
    await screen.findByText("No selected examples belong to this run.");
    expect(
      screen.getByRole("button", { name: "Create run record" }).disabled,
    ).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(h.get.mock.calls.length).toBe(1);
    expect(h.post.mock.calls).toEqual([]);
    expect(h.native.mock.calls).toEqual([]);
  });

  it("shows a read failure without falling back to native rerun", async () => {
    h.get.mockRejectedValueOnce({ statusCode: 403 });
    mount();
    open();
    await screen.findByText(
      "Could not read the pinned run and inputs. Check access and retry.",
    );
    expect(
      screen.getByRole("button", { name: "Create run record" }).disabled,
    ).toBe(true);
    expect(h.post.mock.calls).toEqual([]);
    expect(h.native.mock.calls).toEqual([]);
  });

  it("never includes an external run in a mixed native rerun request", () => {
    const prompt = {
      ...run,
      run_id: "prompt",
      task_kind: "prompt",
      source: "native",
    };
    mount({ runs: [run, prompt] });
    fireEvent.click(
      screen.getByRole("button", { name: "Selected rows, all runs" }),
    );
    expect(h.native.mock.calls).toEqual([[[{ run: prompt, exampleId: "b" }]]]);
    expect(h.post.mock.calls).toEqual([]);
  });

  it("leaves prompt and HTTP cells on their existing rerun path", () => {
    const nativeRuns = [
      { ...run, run_id: "prompt", task_kind: "prompt", source: "native" },
      { ...run, run_id: "http", source: "http" },
    ];
    mount({ runs: nativeRuns });
    expect(screen.queryByText("Run again in your code…")).toBeNull();
    fireEvent.click(
      screen.getByRole("button", { name: "Selected rows, all runs" }),
    );
    expect(h.native.mock.calls).toEqual([
      [nativeRuns.map((nativeRun) => ({ run: nativeRun, exampleId: "b" }))],
    ]);
    expect(h.get.mock.calls).toEqual([]);
  });
});
