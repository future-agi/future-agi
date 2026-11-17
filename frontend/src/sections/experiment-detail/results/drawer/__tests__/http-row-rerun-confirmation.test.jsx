/* eslint-disable react/prop-types */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";

const h = vi.hoisted(() => ({ post: vi.fn(), get: vi.fn(), say: vi.fn() }));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { post: h.post, get: h.get },
}));
vi.mock("notistack", async (original) => ({
  ...(await original()),
  enqueueSnackbar: h.say,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/Mixpanel", () => ({ trackEvent: vi.fn(), Events: {} }));
vi.mock("src/sections/common/ValueViewer/ValueViewer", () => ({
  default: () => null,
}));
vi.mock("../OutputCard", async (original) => ({
  ...(await original()),
  default: () => null,
}));
vi.mock("../ProductionRetestCard", async (original) => ({
  ...(await original()),
  default: () => null,
}));
vi.mock("src/sections/common/BestOf/BestOfCard", async (original) => ({
  ...(await original()),
  default: () => null,
}));
vi.mock(
  "src/sections/annotations/queues/components/add-to-queue-dialog",
  async (original) => ({ ...(await original()), default: () => null }),
);
vi.mock("src/sections/common/Lenses/ModalityLens", async (original) => ({
  ...(await original()),
  default: () => null,
}));
vi.mock("../ScoreCard", async (original) => ({
  ...(await original()),
  default: () => null,
}));
vi.mock("../../AddResultsToDatasetDialog", async (original) => ({
  ...(await original()),
  default: () => null,
}));
vi.mock(
  "src/sections/develop-detail/Review/CorrectedOutputsBlock",
  async (original) => ({ ...(await original()), default: () => null }),
);
vi.mock(
  "src/sections/develop-detail/Review/PairVoteStrip",
  async (original) => ({ ...(await original()), default: () => null }),
);
vi.mock("../ScoreMatrix", async (original) => ({
  ...(await original()),
  default: () => null,
  hasTrialGlyphs: () => false,
}));
vi.mock("../usePairVote", async (original) => ({
  ...(await original()),
  usePairVote: () => ({}),
}));

import { datasetColumnsKey } from "src/sections/develop/useDatasetColumnsLite";
import ResultCompareTab from "../ResultCompareTab";
const clients = [];
const run = {
  run_id: "http-run",
  source: "http",
  task_kind: "external",
  experiment_id: "experiment-a",
  output_column_id: "output-h",
  label: "Endpoint",
};
const row = {
  example_id: "example-a",
  inputs: { question: "selected" },
  expected: { value: null },
  results: {
    "http-run": {
      status: "completed",
      output: "success",
      trials_detail: [{ output: "success" }],
    },
  },
};
function page(readOnly = false) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(client);
  client.setQueryData(datasetColumnsKey("dataset"), {
    data: { result: { columns: [] } },
  });
  return (
    <QueryClientProvider client={client}>
      <ResultCompareTab
        datasetId="dataset"
        row={row}
        runs={[run]}
        baselineId={run.run_id}
        focusedRunId={run.run_id}
        trial={1}
        summary={{ metrics: [] }}
        colors={{}}
        diff="off"
        onDiffChange={vi.fn()}
        onTrialChange={vi.fn()}
        onOpenTab={vi.fn()}
        readOnly={readOnly}
        rowIds={[row.example_id]}
      />
    </QueryClientProvider>
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  h.post.mockResolvedValue({ data: { status: true } });
  h.get.mockResolvedValue({ data: { status: true, result: {} } });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.unstubAllGlobals();
});

describe("drawer canonical HTTP rerun", () => {
  it.each(["This row, all runs", "This row, Endpoint only"])(
    "requires confirmation for %s, preserving selected successful identities",
    async (menu) => {
      render(page());
      fireEvent.click(screen.getByRole("button", { name: "Re-run" }));
      fireEvent.click(screen.getByRole("menuitem", { name: menu }));
      let dialog = screen.getByRole("dialog");
      expect(dialog).toHaveTextContent(
        "The endpoint may already have processed the earlier requests",
      );
      expect(h.post).not.toHaveBeenCalled();
      fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      expect(h.post).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", { name: "Re-run" }));
      fireEvent.click(screen.getByRole("menuitem", { name: menu }));
      dialog = screen.getByRole("dialog");
      fireEvent.click(
        within(dialog).getByRole("button", { name: "Send again" }),
      );
      await waitFor(() => expect(h.post).toHaveBeenCalledTimes(1));
      expect(h.post.mock.calls[0][1]).toEqual({
        cells: [
          {
            column_id: "output-h",
            run_id: "http-run",
            example_id: "example-a",
          },
        ],
        failed_only: false,
        acknowledge_http_side_effects: true,
      });
      expect(h.post.mock.calls[0][2].headers["Idempotency-Key"]).toMatch(
        /^fa-http-/,
      );
      await waitFor(() =>
        expect(h.say).toHaveBeenCalledWith("Re-running this row", {
          variant: "success",
        }),
      );
    },
  );
  it("preserves the drawer read-only gate", () => {
    render(page(true));
    expect(screen.getByRole("button", { name: "Re-run" })).toBeDisabled();
    expect(h.post).not.toHaveBeenCalled();
  });
});
