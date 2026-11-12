import React from "react";
import { Socket } from "node:net";
import { AxiosError } from "axios";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { enqueueSnackbar } from "notistack";
import axios from "src/utils/axios";
import SchemaDrawer from "../SchemaDrawer";

const { dataset, refreshGrid } = vi.hoisted(() => ({
  dataset: {
    metadata: { dataset_name: "Schema save fixture", total_rows: 1 },
    dataset_config: {},
    column_config: [
      { id: "question", name: "question", role: "input", data_type: "text" },
    ],
    table: [{ row_id: "row-1", question: { cell_value: "Example question" } }],
  },
  refreshGrid: vi.fn(),
}));

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/config-global", () => ({ HOST_API: "http://localhost" }));
vi.mock("src/utils/Mixpanel", () => ({ resetUser: vi.fn() }));
vi.mock("src/utils/logger", () => ({
  default: { debug: vi.fn(), warn: vi.fn() },
}));
vi.mock("src/auth/context/jwt/utils", () => ({
  addToQueue: vi.fn(),
  clearTokens: vi.fn(),
  getIsRefreshing: vi.fn(() => false),
  getRefreshToken: vi.fn(() => null),
  getRememberMe: vi.fn(() => false),
  processQueue: vi.fn(),
  refreshTokenRequest: vi.fn(),
  setIsRefreshing: vi.fn(),
  setSession: vi.fn(),
}));

// Generated schema-route registration lands separately from this source lane.
// Supply only that registry entry as a fixture; keep apiPath and the shared
// Axios request/response interceptors real, and never edit generated files.
vi.mock(
  "src/api/contracts/api-surface.generated.js",
  async (importOriginal) => {
    const actual =
      /** @type {typeof import("src/api/contracts/api-surface.generated.js")} */ (
        await importOriginal()
      );
    return {
      ...actual,
      API_SURFACE_PATHS: {
        ...actual.API_SURFACE_PATHS,
        "/model-hub/datasets/{dataset_id}/schema/": ["put"],
      },
    };
  },
);

vi.mock("src/api/develop/develop-detail", () => ({
  useGetDatasetDetail: () => ({
    data: dataset,
    refetch: vi.fn(),
    isFetching: false,
    dataUpdatedAt: 1,
  }),
}));
vi.mock("src/sections/develop-detail/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ refreshGrid }),
}));
vi.mock("src/sections/develop-detail/DataTab/Versions/changeSetStore", () => ({
  useChangeSet: () => ({ addOp: vi.fn() }),
}));
vi.mock("src/launch/launchSwitch", () => ({
  isSurfaceShown: () => false,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../ColumnFromPathDialog", () => ({ default: () => null }));
vi.mock("../TransformsSection", () => ({ default: () => null }));

// Keep the existing Boolean props inference from SchemaDrawer's JSDoc out of
// this regression fixture without changing the production component's contract.
const TestSchemaDrawer = /** @type {import("react").ComponentType<any>} */ (
  SchemaDrawer
);

const DATASET_ID = "5e2b0c92-11aa-4b83-a1db-5f8e2a71c921";
const FALLBACK =
  "Schema could not be saved. Check its definition and try again.";
const originalAdapter = axios.defaults.adapter;
/** @type {QueryClient} */
let queryClient;
/** @type {number} */
let responseInterceptor;

const blockNetwork = () => {
  throw new Error("Network is disabled in the schema save regression");
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("fetch", vi.fn(blockNetwork));
  vi.spyOn(Socket.prototype, "connect").mockImplementation(blockNetwork);
  vi.spyOn(XMLHttpRequest.prototype, "send").mockImplementation(blockNetwork);
  // Contract generation is a separate integration gate; these tests exercise
  // the real error normalizer, not validation of the synthetic 400 payload.
  vi.stubEnv("VITE_API_CONTRACTS", "off");
  vi.stubEnv("VITE_API_CONTRACT_STRICT_RESPONSES", "false");
  axios.defaults.adapter = blockNetwork;
  queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
});

afterEach(() => {
  cleanup();
  queryClient.clear();
  axios.interceptors.response.eject(responseInterceptor);
  axios.defaults.adapter = originalAdapter;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

/** @param {Record<string, unknown>} body */
async function saveWithServerError(body) {
  const adapter = vi.fn(
    (/** @type {import("axios").InternalAxiosRequestConfig} */ config) =>
      Promise.reject(
        new AxiosError(
          "Request failed with status code 400",
          "ERR_BAD_REQUEST",
          config,
          undefined,
          {
            config,
            status: 400,
            statusText: "Bad Request",
            headers: {},
            data: body,
          },
        ),
      ),
  );
  axios.defaults.adapter = adapter;
  const normalizedError = vi.fn();
  responseInterceptor = axios.interceptors.response.use(undefined, (error) => {
    normalizedError(error);
    return Promise.reject(error);
  });

  render(
    <QueryClientProvider client={queryClient}>
      <TestSchemaDrawer open datasetId={DATASET_ID} onClose={vi.fn()} />
    </QueryClientProvider>,
  );
  const declare = screen.getByRole("button", { name: "Declare" });
  expect(declare).toBeEnabled();
  fireEvent.click(declare);

  await waitFor(() => expect(enqueueSnackbar).toHaveBeenCalledTimes(1));
  expect(adapter).toHaveBeenCalledTimes(1);
  const request = adapter.mock.calls[0][0];
  expect(request.method).toBe("put");
  expect(request.url).toBe(`/model-hub/datasets/${DATASET_ID}/schema/`);
  expect(JSON.parse(request.data)).toMatchObject({
    declared: { type: "object", properties: { question: { type: "string" } } },
    enforce: "off",
    preset: "inferred",
  });
  expect(normalizedError).toHaveBeenCalledTimes(1);
  expect(normalizedError).toHaveBeenCalledWith(
    expect.objectContaining({ ...body, statusCode: 400 }),
  );
  expect(normalizedError.mock.calls[0][0]).not.toHaveProperty("response");
  expect(refreshGrid).not.toHaveBeenCalled();
}

describe("SchemaDrawer save errors through the shared client", () => {
  it("shows the normalized server message, not the generic fallback", async () => {
    await saveWithServerError({ message: "Schema update denied" });
    expect(enqueueSnackbar).toHaveBeenCalledWith("Schema update denied", {
      variant: "error",
    });
  });

  it.each([
    { name: "missing", body: {} },
    { name: "null", body: { message: null } },
    { name: "non-string", body: { message: { reason: "invalid schema" } } },
    { name: "empty", body: { message: "" } },
    { name: "whitespace-only", body: { message: "   " } },
  ])("keeps the fallback for a $name message", async ({ body }) => {
    await saveWithServerError(body);
    expect(enqueueSnackbar).toHaveBeenCalledWith(FALLBACK, {
      variant: "error",
    });
  });
});
