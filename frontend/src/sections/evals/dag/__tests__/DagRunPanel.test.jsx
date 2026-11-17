import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import axios from "src/utils/axios";
import DagRunPanel, { runRequestOf } from "../DagRunPanel";
import DagPathsTable from "../../types/DagPathsTable";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  endpoints: {
    develop: {
      getDatasetDetail: (id) => `/model-hub/develops/${id}/get-dataset-table/`,
      w7: { dagPaths: (id) => `/model-hub/eval-templates/${id}/dag/paths/` },
    },
  },
}));
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({
    data: [{ datasetId: "ds-1", name: "Replies" }],
  }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org-a" }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "ws-a" }),
}));

const TREE = "0b8e8f8e-6f55-4a39-9c7c-1d2b9f0c1a11";
const dag = {
  root: "n1",
  output_type: "score",
  nodes: [
    {
      id: "n1",
      kind: "judge",
      question: "Is it about {{topic}}?",
      answers: [
        { label: "Yes", next: "a" },
        { label: "No", next: "b" },
      ],
    },
    { id: "a", kind: "leaf", label: "On topic", score: 1 },
    { id: "b", kind: "leaf", label: "Off topic", score: 0 },
  ],
};

function mount(ui) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <MemoryRouter>
      <QueryClientProvider client={client}>{ui}</QueryClientProvider>
    </MemoryRouter>,
  );
}

async function pick(label, option) {
  const select = screen.getByRole("combobox", {
    name: (name) => name.startsWith(label),
  });
  fireEvent.mouseDown(select);
  fireEvent.click(await screen.findByRole("option", { name: option }));
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("decision tree runs", () => {
  it("names every variable a run still needs", () => {
    expect(
      runRequestOf({ datasetId: "", vars: ["topic"], mapping: {} }).reasons,
    ).toEqual([
      "Pick a dataset",
      "Map {{output}} to a column",
      "Map {{topic}} to a column",
    ]);
  });

  it("posts the mapped columns to the contracted runs route", async () => {
    axios.get.mockResolvedValue({
      data: {
        result: {
          column_config: [
            { id: "col-reply", name: "reply" },
            { id: "col-topic", name: "topic" },
          ],
        },
      },
    });
    axios.post.mockResolvedValue({ data: { result: { n: 3 } } });
    mount(<DagRunPanel templateId={TREE} dag={dag} />);
    const run = screen.getByRole("button", { name: "Run on dataset" });
    expect(run).toBeDisabled();
    await pick("Dataset", "Replies");
    await pick("{{output}}", "reply");
    await pick("{{topic}}", "topic");
    await waitFor(() => expect(run).toBeEnabled());
    fireEvent.click(run);
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    expect(axios.post).toHaveBeenCalledWith(
      `/model-hub/eval-templates/${TREE}/dag/runs/`,
      {
        dataset_id: "ds-1",
        mapping: { output: "col-reply", topic: "col-topic" },
      },
    );
  });

  it("refuses to run unsaved edits", () => {
    mount(<DagRunPanel templateId={TREE} dag={dag} dirty />);
    expect(
      screen.getByText(/Save the version before running it/),
    ).toBeInTheDocument();
  });

  it("lists stored paths without a results link when no experiment ran them", async () => {
    axios.get.mockResolvedValue({
      data: {
        result: {
          runs: [{ run_id: "run-1", run_label: "Replies · 3 rows" }],
          run_id: "run-1",
          experiment_id: null,
          n: 3,
          paths: [
            {
              path_key: "n1:Yes>a",
              label: "1 Yes → On topic",
              rows: ["r1", "r2"],
              n: 2,
              share: 2 / 3,
              mean: 1,
            },
            {
              path_key: "n1:No>b",
              label: "1 No → Off topic",
              rows: ["r3"],
              n: 1,
              share: 1 / 3,
              mean: 0,
            },
          ],
        },
      },
    });
    mount(<DagPathsTable templateId={TREE} />);
    const rows = await screen.findAllByTestId("dag-path-row");
    expect(
      rows.map((r) => within(r).getByTestId("dag-path-n").textContent),
    ).toEqual(["2", "1"]);
    expect(screen.queryByRole("button", { name: "Open rows" })).toBeNull();
  });
});
