import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent, waitFor } from "src/utils/test-utils";
import { getCreatedDatasetName } from "./sdkCreateDatasetResponse";

const postMock = vi.fn();

vi.mock("src/utils/axios", () => ({
  default: { post: (...args) => postMock(...args) },
  endpoints: {
    develop: { createEmptyDataset: "/model-hub/develops/create-empty-dataset/" },
    row: { addRowSdk: "/model-hub/develops/add_rows_sdk/" },
  },
}));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: {},
  PropertyName: {},
}));
// Monaco is only shown in the second (post-create) modal and is heavy in jsdom.
vi.mock("src/components/form-code-editor", () => ({
  FormCodeEditor: () => null,
}));

import AddSDKModal from "./AddSDKModal";

// Mirrors the real CreateEmptyDatasetView response — snake_case, not camelCase.
const CREATE_RESPONSE = {
  data: {
    result: {
      message: "Empty dataset created successfully",
      dataset_id: "11111111-1111-1111-1111-111111111111",
      dataset_name: "vafvfdafvd",
      dataset_model_type: "GenerativeLLM",
    },
  },
};

const renderModal = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AddSDKModal open onClose={() => {}} refreshGrid={() => {}} />
    </QueryClientProvider>,
  );
};

describe("getCreatedDatasetName", () => {
  it("reads the canonical snake_case create-empty-dataset response", () => {
    expect(getCreatedDatasetName(CREATE_RESPONSE)).toBe("vafvfdafvd");
  });

  it("keeps the legacy camelCase response fallback", () => {
    expect(
      getCreatedDatasetName({ data: { result: { datasetName: "camel_ds" } } }),
    ).toBe("camel_ds");
  });

  it("falls back to a bare name key", () => {
    expect(getCreatedDatasetName({ result: { name: "bare_ds" } })).toBe(
      "bare_ds",
    );
  });

  it("returns null when nothing matches", () => {
    expect(getCreatedDatasetName({ data: { result: {} } })).toBeNull();
  });
});

describe("AddSDKModal — SDK dataset create flow", () => {
  beforeEach(() => {
    postMock.mockReset();
    postMock.mockImplementation((url) => {
      if (url.includes("create-empty-dataset")) {
        return Promise.resolve(CREATE_RESPONSE);
      }
      if (url.includes("add_rows_sdk")) {
        return Promise.resolve({
          data: {
            result: {
              dataset: { id: CREATE_RESPONSE.data.result.dataset_id, name: "vafvfdafvd" },
              api_keys: { api_key: "ak", secret_key: "sk" },
              code: {},
            },
          },
        });
      }
      return Promise.resolve({ data: { result: {} } });
    });
  });

  it("passes the created dataset's real snake_case name to add_rows_sdk (not undefined)", async () => {
    renderModal();

    fireEvent.change(screen.getByPlaceholderText("Enter dataset name"), {
      target: { value: "vafvfdafvd" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^next$/i }));

    await waitFor(() => {
      const addRowsCall = postMock.mock.calls.find(([url]) =>
        url.includes("add_rows_sdk"),
      );
      expect(addRowsCall).toBeTruthy();
      // Reverts to undefined if the create response is read via camelCase.
      expect(addRowsCall[1]).toEqual({ dataset_name: "vafvfdafvd" });
    });
  });
});
