import {
  MutationCache,
  MutationObserver,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { endpoints } from "src/utils/axios";
import { handleError } from "src/utils/queryErrorHandler";
import { palette } from "src/theme/palette";

// Every dataset-create entry point must show the backend's retry message,
// exactly once, when the plan's dataset limit could not be verified (503).

const RETRY_MESSAGE =
  "Could not verify your plan's dataset limit. Please try again in a moment.";

const mockPost = vi.fn();
const mockEnqueueSnackbar = vi.fn();
const mockMutationOptions = [];

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useMutation: (options, client) => {
      mockMutationOptions.push(options);
      return actual.useMutation(options, client);
    },
  };
});

vi.mock("src/utils/axios", async () => {
  const actual = await vi.importActual("src/utils/axios");
  const pending = () => new Promise(() => {});
  return {
    ...actual,
    default: {
      ...actual.default,
      get: pending,
      put: pending,
      delete: pending,
      post: (...args) => mockPost(...args),
    },
  };
});

vi.mock("notistack", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    enqueueSnackbar: (...args) => mockEnqueueSnackbar(...args),
    useSnackbar: () => ({
      enqueueSnackbar: (...args) => mockEnqueueSnackbar(...args),
      closeSnackbar: () => {},
    }),
  };
});

vi.mock("src/utils/Mixpanel", async (importOriginal) => ({
  ...(await importOriginal()),
  trackEvent: () => {},
}));

vi.mock("src/auth/hooks", async (importOriginal) => ({
  ...(await importOriginal()),
  useAuthContext: () => ({ role: "Owner", user: {} }),
}));

vi.mock(
  "src/sections/develop-detail/Context/DevelopDetailContext",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useDevelopDetailContext: () => ({
      refreshGrid: () => {},
      gridApi: { current: null },
    }),
  }),
);

vi.mock("src/sections/develop-detail/states", async (importOriginal) => ({
  ...(await importOriginal()),
  useDevelopSelectedRowsStoreShallow: (select) =>
    select({
      selectAll: false,
      toggledNodes: ["row-1"],
      resetSelectedRows: () => {},
    }),
}));

// What src/utils/axios.js rejects with: the response body plus statusCode.
const rejectedWith = (statusCode, code, message) => ({
  status: false,
  type: "service_unavailable",
  code,
  detail: message,
  message,
  error: message,
  result: message,
  statusCode,
  transportCode: "ERR_BAD_RESPONSE",
});

const dialog = { open: false, onClose: () => {}, refreshGrid: () => {} };

const SITES = [
  // Sites with their own onError report the failure themselves.
  {
    name: "ManuallyCreateDataset",
    load: () =>
      import("src/sections/develop/AddDatasetDrawer/ManuallyCreateDataset"),
    props: dialog,
    url: () => endpoints.develop.createDatasetManually,
  },
  {
    name: "AddDatasetDrawer/UploadFileModal",
    load: () => import("src/sections/develop/AddDatasetDrawer/UploadFileModal"),
    props: dialog,
    url: () => endpoints.develop.uploadDatasetLocalFile,
  },
  {
    name: "ImportFromHuggingFace",
    load: () =>
      import("src/sections/develop/AddDatasetDrawer/ImportFromHuggingFace"),
    props: dialog,
    url: () => endpoints.develop.createHuggingFaceDataset,
  },
  {
    name: "HuggingFaceView",
    load: () => import("src/sections/huggingface/HuggingFaceView"),
    props: {},
    url: () => endpoints.develop.createHuggingFaceDataset,
  },
  {
    name: "AddRowDrawer/CreateSyntheticData",
    load: () => import("src/sections/develop/AddRowDrawer/CreateSyntheticData"),
    props: dialog,
    url: () => endpoints.develop.createSyntheticDataset,
  },
  {
    name: "CreateSyntheticDataView",
    load: () =>
      import("src/sections/develop/AddRowDrawer/CreateSyntheticDataView"),
    props: { onClose: () => {} },
    url: () => endpoints.develop.createSyntheticDataset,
  },
  {
    name: "ExistingDatasetModal",
    load: () =>
      import("src/sections/develop/AddRowDrawer/ExistingDatasetModal"),
    props: { ...dialog, closeDrawer: () => {} },
    url: () => endpoints.develop.createFromExistingDataset,
  },
  {
    name: "AddDatasetDrawer/AddSDKModal",
    load: () => import("src/sections/develop/AddDatasetDrawer/AddSDKModal"),
    props: dialog,
    url: () => endpoints.develop.createEmptyDataset,
  },
  // Sites without an onError rely on the global handler.
  {
    name: "AddNewDataset (tracer add_to_new_dataset)",
    load: () =>
      import("src/components/traceDetailDrawer/addToDataset/AddNewDataset"),
    props: {
      handleclose: () => {},
      selectedNode: {},
      observationFields: [],
      selectedTraces: [],
      selectedSpans: [],
      selectAll: false,
      currentTab: "spans",
    },
    url: () => endpoints.project.addNewDataset,
  },
  {
    name: "CloneDevelopDataset",
    load: () =>
      import("src/sections/develop/AddDatasetDrawer/CloneDevelopDataset"),
    props: dialog,
    variables: { new_dataset_id: "source-dataset", name: "Copy" },
    url: () => endpoints.develop.cloneDataset("source-dataset"),
  },
  {
    name: "DuplicateDataset",
    load: () =>
      import("src/sections/develop/DuplicateDataset/DuplicateDataset"),
    props: {
      ...dialog,
      selected: { id: "source-dataset", datasetType: "GenerativeLLM" },
    },
    url: () => endpoints.develop.cloneDataset("source-dataset"),
  },
  {
    name: "ConfigureAddAsNewDatasetModal (experiment)",
    load: () =>
      import("src/pages/dashboard/models/ConfigureAddAsNewDatasetModal"),
    props: { open: false, onClose: () => {} },
    route: ["/experiment/exp-1", "/experiment/:individualExperimentId"],
    url: () => endpoints.develop.addAsNewDataset("exp-1"),
  },
  {
    name: "DevelopDataSelectionActive (rows to new dataset)",
    load: () =>
      import(
        "src/sections/develop-detail/DevelopBarRightSection/DevelopDataSelectionActive"
      ),
    props: {},
    route: ["/develop/dataset-1", "/develop/:dataset"],
    variables: "New dataset",
    url: () => endpoints.develop.createDatasetRows("dataset-1"),
  },
];

const theme = createTheme({ palette: palette("light") });

async function mountAndFindCreateMutation(site) {
  const client = new QueryClient({
    mutationCache: new MutationCache({ onError: handleError }),
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const { default: Component } = await site.load();
  const [entry, path] = site.route || ["/", "/"];
  render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>
        <MemoryRouter initialEntries={[entry]}>
          <Routes>
            <Route path={path} element={<Component {...site.props} />} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );

  // The latest render's options for the mutation that posts to the endpoint.
  const target = [...mockMutationOptions].reverse().find((options) => {
    mockPost.mockClear();
    mockPost.mockReturnValue(new Promise(() => {}));
    try {
      options.mutationFn?.(site.variables ?? {});
    } catch {
      return false;
    }
    return mockPost.mock.calls[0]?.[0] === site.url();
  });
  expect(target, `${site.name} posts to ${site.url()}`).toBeTruthy();
  return { client, options: target };
}

async function runFailedCreate(site, error) {
  const { client, options } = await mountAndFindCreateMutation(site);
  mockPost.mockReset();
  mockPost.mockRejectedValue(error);
  mockEnqueueSnackbar.mockClear();
  await new MutationObserver(client, options)
    .mutate(site.variables ?? {})
    .catch(() => {});
}

describe("dataset limit check failure (503) on every dataset-create entry point", () => {
  beforeEach(() => {
    mockMutationOptions.length = 0;
    mockPost.mockReset();
    mockEnqueueSnackbar.mockClear();
  });

  it.each(SITES.map((site) => [site.name, site]))(
    "%s shows the retry message exactly once",
    async (_name, site) => {
      await runFailedCreate(
        site,
        rejectedWith(503, "dataset_limit_check_failed", RETRY_MESSAGE),
      );

      expect(
        mockEnqueueSnackbar.mock.calls.map(([message]) => message),
      ).toEqual([RETRY_MESSAGE]);
    },
  );

  it("keeps hiding a raw 500 from the tracer route", async () => {
    const tracer = SITES.find(
      (site) => site.url() === endpoints.project.addNewDataset,
    );
    await runFailedCreate(
      tracer,
      rejectedWith(
        500,
        "server_error",
        "Error creating the dataset observe Code: 241. DB::Exception: Memory limit",
      ),
    );

    expect(mockEnqueueSnackbar.mock.calls.map(([message]) => message)).toEqual([
      "Something went wrong",
    ]);
  });
});
