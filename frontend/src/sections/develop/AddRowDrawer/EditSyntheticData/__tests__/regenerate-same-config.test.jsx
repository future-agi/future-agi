// Restore row 4: Datasets > a synthetic dataset whose generation failed >
// summary drawer > "Re-Generate same Configuration". Production shows it when
// generation failed and sends the saved configuration again with
// regenerate true to PUT update-synthetic-config/. The backend still handles
// it: UpdateSyntheticDatasetConfigView (reject_unknown_fields) validates the
// body with SyntheticDatasetConfigSerializer, whose fields include
// regenerate, and on regenerate clears the rows and starts generation again.
import React from "react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const { get, put, navigate, refreshGrid } = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(),
  navigate: vi.fn(),
  refreshGrid: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: { get, put },
  endpoints: {
    develop: {
      getSyntheticConfig: (id) => `/model-hub/develops/${id}/synthetic-config/`,
      updateSyntheticDataset: (id) =>
        `/model-hub/develops/${id}/update-synthetic-config/`,
    },
  },
}));
vi.mock("react-router", () => ({
  useParams: () => ({ dataset: "ds-1" }),
  useNavigate: () => navigate,
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/sections/develop-detail/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ refreshGrid }),
}));
vi.mock("src/api/knowledge-base/files", () => ({
  useKnowledgeBaseList: () => ({ data: [] }),
}));
vi.mock("src/sections/develop-detail/states", () => ({
  useDatasetOriginStore: () => ({ processingComplete: true }),
}));
// The dataset table read is seeded below; the drawer only needs to know the
// dataset is synthetic before it reads the saved configuration.
vi.mock("src/api/develop/develop-detail", () => ({
  getDatasetQueryOptions: (id) => ({
    queryKey: ["ds-table", id],
    queryFn: () => Promise.resolve(undefined),
    enabled: false,
  }),
}));
vi.mock("src/sections/develop-detail/Common/CustomDialog/CustomDialog", () => ({
  default: () => null,
}));
vi.mock("src/hooks/use-drawer-stack", () => ({
  useDrawerCovered: () => false,
  coveredDrawerSx: () => ({}),
}));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock(
  "src/sections/develop/AddRowDrawer/CreateSyntheticData/Summary/EachColumnSummary",
  () => ({ default: () => null }),
);
vi.mock("src/sections/develop/AddRowDrawer/CreateSyntheticData/common", () => ({
  transformColumnPayload: (columns) => columns,
}));

import SyntheticSummaryDrawer from "../SyntheticSummaryDrawer";
import { resetEditSyntheticStates, useEditSyntheticDataStore } from "../state";

// Server truth, read from the backend at this tree.
const SERIALIZERS = readFileSync(
  resolve(
    process.cwd(),
    "../futureagi/model_hub/serializers/develop_dataset.py",
  ),
  "utf8",
);
const CONFIG_FIELDS = [
  ...SERIALIZERS.match(
    /class SyntheticDatasetConfigSerializer\(serializers\.Serializer\):\n([\s\S]*?)\n {4}def /,
  )[1].matchAll(/^ {4}(\w+) = [\w.]+\(/gm),
].map((m) => m[1]);
const SYNTHETIC_VIEW = readFileSync(
  resolve(
    process.cwd(),
    "../futureagi/model_hub/views/datasets/create/synthetic.py",
  ),
  "utf8",
);

// The saved configuration GET synthetic-config/ returns (snake_case, the
// create request's validated data).
const CONFIG = {
  num_rows: 20,
  columns: [
    {
      name: "question",
      data_type: "text",
      description: "A customer question",
      property: {},
    },
  ],
  dataset: {
    name: "Support tickets",
    description: "Tickets from customers",
    objective: "Train a support agent",
    patterns: "Short and polite",
  },
  kb_id: null,
};

const renderDrawer = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  queryClient.setQueryData(["ds-table", "ds-1"], {
    data: { result: { synthetic_dataset: true } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SyntheticSummaryDrawer />
    </QueryClientProvider>,
  );
};

const REGENERATE = "Re-Generate same Configuration";

beforeEach(() => {
  get.mockResolvedValue({ data: { result: { data: CONFIG } } });
  put.mockResolvedValue({
    data: {
      status: true,
      result: {
        message:
          "Synthetic dataset configuration updated successfully. Data generation started.",
        data: { dataset_id: "ds-1", dataset_name: "Support tickets" },
      },
    },
  });
});

afterEach(() => {
  vi.clearAllMocks();
  resetEditSyntheticStates();
});

describe("Synthetic summary drawer: Re-Generate same Configuration", () => {
  it("the server accepts regenerate on update-synthetic-config/", () => {
    expect(CONFIG_FIELDS).toEqual(
      expect.arrayContaining([
        "num_rows",
        "columns",
        "dataset",
        "kb_id",
        "regenerate",
      ]),
    );
    expect(SYNTHETIC_VIEW).toContain('if validated_data.get("regenerate"):');
  });

  it("shows the button when generation failed and sends the saved configuration with regenerate true", async () => {
    useEditSyntheticDataStore.setState({
      openSummaryDrawer: true,
      failedToGenerateData: true,
    });
    renderDrawer();

    // The saved configuration has loaded.
    expect(await screen.findByText(CONFIG.dataset.name)).toBeTruthy();
    expect(get).toHaveBeenCalledWith(
      "/model-hub/develops/ds-1/synthetic-config/",
    );

    const button = screen.getByRole("button", { name: REGENERATE });
    await waitFor(() => expect(button).not.toBeDisabled());
    fireEvent.click(button);

    await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
    const [url, body] = put.mock.calls[0];
    expect(url).toBe("/model-hub/develops/ds-1/update-synthetic-config/");
    expect(body).toEqual({ ...CONFIG, regenerate: true });
    // reject_unknown_fields: every top-level key is a serializer field.
    expect(Object.keys(body).filter((k) => !CONFIG_FIELDS.includes(k))).toEqual(
      [],
    );

    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith("/dashboard/develop/ds-1?tab=data", {
        replace: true,
      }),
    );
    expect(refreshGrid).toHaveBeenCalled();
    expect(useEditSyntheticDataStore.getState().openSummaryDrawer).toBe(false);
  });

  it("does not show the button when generation has not failed", async () => {
    useEditSyntheticDataStore.setState({
      openSummaryDrawer: true,
      failedToGenerateData: false,
    });
    renderDrawer();

    expect(await screen.findByText(CONFIG.dataset.name)).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Edit Configuration" }),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: REGENERATE })).toBeNull();
    expect(put).not.toHaveBeenCalled();
  });
});
