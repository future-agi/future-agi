// HO D9 review fix: on the experiment page a group's output keys read each
// run's stored output (fixed, "Run output"), never a dataset column; on a
// dataset every key still maps to a column.
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "src/utils/test-utils";
import {
  applyBlocked,
  applyBody,
  RUN_OUTPUT,
  runOutputMapping,
} from "../applyGroupModel";

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get: vi.fn(), post: vi.fn() },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
const COLUMNS = [
  { id: "c-in", name: "customer_message", role: "input", data_type: "text" },
  { id: "c-exp", name: "expected_reply", role: "expected", data_type: "text" },
  { id: "c-out", name: "Draft reply", role: "output", data_type: "text" },
];
vi.mock("src/api/develop/develop-detail", () => ({
  getDatasetQueryOptions: (id) => ({
    queryKey: ["ho-d9-table", id],
    queryFn: async () => ({ data: { result: { column_config: COLUMNS } } }),
  }),
}));
const MEMBERS = [
  {
    eval_template_id: "t-corr",
    name: "Correctness",
    required_keys: ["output", "expected"],
    optional_keys: ["input"],
  },
  {
    eval_template_id: "t-tone",
    name: "Tone",
    required_keys: ["response"],
    optional_keys: [],
  },
];
const PICK = {
  group: { id: "g1", name: "Checks" },
  detail: { eval_group: { id: "g1", name: "Checks" }, members: MEMBERS },
  skipped: [],
};
vi.mock("../AddEvalGroupButton", () => ({
  default: ({ onPick }) => (
    <button type="button" onClick={() => onPick(PICK)}>
      pick group
    </button>
  ),
}));

const axios = (await import("src/utils/axios")).default;
const { default: ApplyEvalGroupButton } = await import(
  "../ApplyEvalGroupDialog"
);

function open(page) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <ApplyEvalGroupButton
        page={page}
        datasetId="ds1"
        experimentId={page === "experiment" ? "ex1" : undefined}
        savedEvals={[{ name: "old", mapping: { output: "Draft reply" } }]}
      />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "pick group" }));
}
const keyInput = (k) =>
  document.querySelector(`input[data-testid="apply-group-key-${k}"]`);

describe("HO D9 · Run output on the experiment page", () => {
  beforeEach(() => {
    axios.post.mockReset();
    axios.post.mockResolvedValue({ data: { result: {} } });
  });

  it("fixes the output keys to the run output on an experiment only", () => {
    const keys = ["output", "response", "actual_output", "expected", "input"];
    expect(runOutputMapping(keys, "experiment")).toEqual({
      output: RUN_OUTPUT,
      response: RUN_OUTPUT,
      actual_output: RUN_OUTPUT,
    });
    expect(runOutputMapping(keys, "dataset")).toEqual({});
    // Fixed keys count as mapped; the body sends the run output.
    const mapping = {
      ...runOutputMapping(keys, "experiment"),
      expected: "c-exp",
    };
    const members = [{ eval_template_id: "a", required_keys: keys }];
    expect(applyBlocked({ members, mapping: { ...mapping } })).toMatch(
      /^Map input/,
    );
    const body = applyBody({
      groupId: "g1",
      page: "experiment",
      experimentId: "ex1",
      mapping,
    });
    expect(body.mapping.output).toBe(RUN_OUTPUT);
    expect(body.filters).toEqual({ experiment_id: "ex1" });
  });

  it("experiment page: output keys read Run output (no column select); Apply sends output", async () => {
    open("experiment");
    await waitFor(() => expect(keyInput("expected")).toHaveValue("c-exp"));
    expect(keyInput("output")).toHaveValue("Run output");
    expect(keyInput("output")).toHaveAttribute("readonly");
    expect(keyInput("output")).toHaveAttribute("data-fixed", RUN_OUTPUT);
    expect(keyInput("response")).toHaveValue("Run output");
    fireEvent.click(screen.getByTestId("apply-group-apply"));
    await waitFor(() => expect(axios.post).toHaveBeenCalled());
    const [, body] = axios.post.mock.calls[0];
    expect(body.page_id).toBe("EXPERIMENT");
    expect(body.mapping).toMatchObject({
      output: RUN_OUTPUT,
      response: RUN_OUTPUT,
      expected: "c-exp",
    });
  });

  it("dataset page: output maps to a dataset column as before", async () => {
    open("dataset");
    await waitFor(() => expect(keyInput("output")).toHaveValue("c-out"));
    expect(keyInput("output")).not.toHaveAttribute("data-fixed");
    fireEvent.click(screen.getByTestId("apply-group-apply"));
    await waitFor(() => expect(axios.post).toHaveBeenCalled());
    expect(axios.post.mock.calls[0][1].mapping.output).toBe("c-out");
  });
});
