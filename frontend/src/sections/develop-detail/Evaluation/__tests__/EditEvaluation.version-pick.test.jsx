/* eslint-disable react/prop-types -- Test-only harness. */
// The dataset page's Edit evaluation reopens a binding on its stored version
// and saves the version the picker hands back: the stored one unless the
// person picked another.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";
import EditEvaluation from "../EditEvaluation";

const h = vi.hoisted(() => ({
  picker: null,
  setConfigureEval: vi.fn(),
  refreshGrid: vi.fn(),
}));

vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { get: vi.fn(), post: vi.fn() },
}));
vi.mock("react-router", async (original) => ({
  ...(await original()),
  useParams: () => ({ dataset: "dataset-1" }),
}));
vi.mock("react-router-dom", async (original) => ({
  ...(await original()),
  useSearchParams: () => [new URLSearchParams()],
}));
vi.mock("notistack", async (original) => ({
  ...(await original()),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("src/sections/develop-detail/states", () => ({
  useConfigureEvalStore: () => ({
    configureEval: { id: "binding-1" },
    setConfigureEval: h.setConfigureEval,
  }),
}));
vi.mock("src/sections/develop-detail/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ refreshGrid: h.refreshGrid }),
}));
vi.mock("src/api/develop/develop-detail", () => ({
  useDatasetColumnConfig: () => [],
}));
vi.mock("src/sections/common/EvalPicker/EvalPickerDrawer", () => ({
  default: (props) => {
    h.picker = props;
    return null;
  },
}));

const storedBinding = (fields = {}) => ({
  id: "binding-1",
  template_id: "tpl-1",
  name: "release_scorer",
  eval_type: "llm",
  selected_model: "gpt-4o",
  mapping: { output: "col-output" },
  run_config: {},
  ...fields,
});

const mount = (stored) => {
  axios.get.mockResolvedValue({
    data: { status: true, result: { eval: stored } },
  });
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <EditEvaluation />
    </QueryClientProvider>,
  );
};

const save = async (selection) => {
  await waitFor(() => expect(h.picker?.initialEval).toBeTruthy());
  await act(async () => {
    await h.picker.onEvalAdded({
      name: "release_scorer",
      mapping: { output: "col-output" },
      config: {},
      ...selection,
    });
  });
  expect(axios.post).toHaveBeenCalledTimes(1);
  return axios.post.mock.calls[0][1];
};

beforeEach(() => {
  vi.clearAllMocks();
  h.picker = null;
  axios.post.mockResolvedValue({ data: { status: true } });
});

describe("Edit evaluation on the dataset page and the version a binding runs", () => {
  it("reopens the binding on its stored version", async () => {
    mount(storedBinding({ pinned_version_id: "ver-1" }));
    await waitFor(() => expect(h.picker?.initialEval).toBeTruthy());
    expect(h.picker.initialEval.pinned_version_id).toBe("ver-1");
  });

  it.each([
    ["the stored version, when no other is picked", "ver-1"],
    ["another version, when it is picked", "ver-2"],
    ["no version, when Default version is picked", null],
  ])("saves %s", async (_case, versionId) => {
    mount(storedBinding({ pinned_version_id: "ver-1" }));
    const payload = await save({ versionId });
    expect(payload.pinned_version_id).toBe(versionId);
  });

  it("leaves the version out when the picker leaves it out (an edited definition)", async () => {
    mount(storedBinding({ pinned_version_id: "ver-1" }));
    const payload = await save({});
    expect(payload).not.toHaveProperty("pinned_version_id");
  });

  it("leaves the version out when the reopened binding does not say which it runs", async () => {
    mount(storedBinding());
    const payload = await save({ versionId: "ver-2" });
    expect(payload).not.toHaveProperty("pinned_version_id");
  });
});
