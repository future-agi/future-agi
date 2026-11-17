/* eslint-disable react/prop-types */
// The Apply dialog names, under each evaluation of the group, the model it
// will run with: the group read's `apply_model` (the template's own model,
// else Add evaluation's default), or, when Apply would leave it out, why
// (`apply_refusal`, the server's sentence naming the evaluation and its
// model). After Apply, each member it left out is listed by that sentence.
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "src/utils/test-utils";
import {
  memberModelLine,
  notifyRefused,
  refusedText,
} from "../applyGroupModel";

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get: vi.fn(), post: vi.fn() },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
const COLUMNS = [
  { id: "c-out", name: "Draft reply", role: "output", data_type: "text" },
];
vi.mock("src/api/develop/develop-detail", () => ({
  getDatasetQueryOptions: (id) => ({
    queryKey: ["apply-group-model-table", id],
    queryFn: async () => ({ data: { result: { column_config: COLUMNS } } }),
  }),
}));
// One reason clause; before Apply the row says "Won't be added", after it the
// toast names the evaluation.
const reason = (model) =>
  `its model, ${model}, can't run on this deployment. Change the evaluation's model, then apply again.`;
const notAdded = (name, model) => `${name} was not added: ${reason(model)}`;
const WONT_BE_ADDED = `Won't be added: ${reason("Turing Large")}`;
const REFUSAL = notAdded("Safety", "Turing Large");
const MEMBERS = [
  {
    eval_template_id: "t-tone",
    name: "Tone",
    required_keys: ["output"],
    optional_keys: [],
    apply_model: "gpt-4o-mini",
    apply_refusal: null,
  },
  {
    eval_template_id: "t-polite",
    name: "Politeness",
    required_keys: ["output"],
    optional_keys: [],
    apply_model: "turing_large",
    apply_refusal: null,
  },
  {
    eval_template_id: "t-safe",
    name: "Safety",
    required_keys: ["output"],
    optional_keys: [],
    apply_model: null,
    apply_refusal: WONT_BE_ADDED,
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
const { enqueueSnackbar } = await import("notistack");
const { default: ApplyEvalGroupButton } = await import(
  "../ApplyEvalGroupDialog"
);

function open() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <ApplyEvalGroupButton
        page="experiment"
        datasetId="ds1"
        experimentId="ex1"
      />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "pick group" }));
}
const modelLine = (id) => screen.getByTestId(`apply-group-member-model-${id}`);

describe("the Apply dialog names each evaluation's model", () => {
  beforeEach(() => {
    axios.post.mockReset();
    enqueueSnackbar.mockReset();
  });

  it("words the line from the group read", () => {
    expect(memberModelLine(MEMBERS[0])).toBe("Model: gpt-4o-mini");
    expect(memberModelLine(MEMBERS[1])).toBe("Model: Turing Large");
    expect(memberModelLine(MEMBERS[2])).toBe(
      "Won't be added: its model, Turing Large, can't run on this deployment. Change the evaluation's model, then apply again.",
    );
    expect(memberModelLine({ eval_template_id: "t-old" })).toBeNull();
  });

  it("shows one line under each member's name", async () => {
    open();
    await waitFor(() => expect(modelLine("t-tone")).toBeInTheDocument());
    expect(modelLine("t-tone")).toHaveTextContent("Model: gpt-4o-mini");
    expect(modelLine("t-polite")).toHaveTextContent("Model: Turing Large");
    expect(modelLine("t-safe")).toHaveTextContent(WONT_BE_ADDED);
    // Apply sends no model: the server applies the model each line names.
    axios.post.mockResolvedValue({ data: { result: { run: "started" } } });
    fireEvent.click(screen.getByTestId("apply-group-apply"));
    await waitFor(() => expect(axios.post).toHaveBeenCalled());
    expect(axios.post.mock.calls[0][1].filters).toEqual({
      experiment_id: "ex1",
    });
  });

  it("names the members left out and counts only those added", async () => {
    axios.post.mockResolvedValue({
      data: {
        result: { run: "started" },
        refused: [
          { eval_template_id: "t-safe", name: "Safety", reason: REFUSAL },
        ],
      },
    });
    open();
    await waitFor(() => expect(modelLine("t-safe")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("apply-group-apply"));
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        "Added 2 evaluations from Checks · scored on this experiment's runs",
        { variant: "success" },
      ),
    );
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "Safety was not added: its model, Turing Large, can't run on this deployment. Change the evaluation's model, then apply again.",
      { variant: "warning" },
    );
  });

  it("lists each member left out by its own sentence", () => {
    const bias = notAdded("Bias", "jev-9.9.9");
    expect(refusedText([])).toBeNull();
    expect(
      refusedText([
        { name: "Safety", reason: REFUSAL },
        { name: "Bias", reason: bias },
      ]),
    ).toBe(`${REFUSAL} ${bias}`);
  });

  it("warns once after an apply that left members out, else not at all", () => {
    const notify = vi.fn();
    const left = { data: { result: [], refused: [{ reason: REFUSAL }] } };

    expect(notifyRefused(left, notify)).toBe(true);
    expect(notify).toHaveBeenCalledWith(REFUSAL, { variant: "warning" });
    notify.mockReset();
    expect(notifyRefused({ data: { result: [] } }, notify)).toBe(false);
    expect(notify).not.toHaveBeenCalled();
  });
});
