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

// The Run Prompt drawer's Test (preview) and Run (create) requests have no
// onError of their own: the app's MutationCache handler toasts their failure.
// A 400 that tells the user which model to replace must reach that toast.

const CATALOG_MISS_AS_CAPTURED =
  "Model 'gpt-3.5-turbo' is not available in the current model catalog. It may be deprecated or retired. Please select a supported model from the latest available models list.";
const CATALOG_MISS =
  "Model 'gpt-3.5-turbo' is not available in the current model catalog. It may be deprecated or retired. Please choose a supported model from the latest available models list.";
const RETIRED_ON_RUN =
  "Model 'gpt-3.5-turbo' is no longer available. Please select a supported model.";

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

// What src/utils/axios.js rejects with: the response body plus statusCode.
const rejectedWith = (statusCode, type, code, message) => ({
  status: false,
  type,
  code,
  detail: message,
  message,
  error: message,
  result: message,
  statusCode,
  transportCode: "ERR_BAD_REQUEST",
});

const theme = createTheme({ palette: palette("light") });

async function mountAndFindMutation(url) {
  const client = new QueryClient({
    mutationCache: new MutationCache({ onError: handleError }),
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const { RunPromptForm } = await import(
    "src/sections/develop-detail/RunPrompt/RunPrompt"
  );
  render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>
        <MemoryRouter initialEntries={["/develop/dataset-1"]}>
          <Routes>
            <Route
              path="/develop/:dataset"
              element={
                <RunPromptForm
                  onClose={() => {}}
                  editConfigData={null}
                  isConfirmationModalOpen={false}
                  setConfirmationModalOpen={() => {}}
                />
              }
            />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );

  // The latest render's options for the mutation that posts to the endpoint.
  // Probing calls every mutationFn; the ones that open a socket fail in jsdom.
  const target = [...mockMutationOptions].reverse().find((options) => {
    mockPost.mockClear();
    mockPost.mockReturnValue(new Promise(() => {}));
    try {
      Promise.resolve(options.mutationFn?.({})).catch(() => {});
    } catch {
      return false;
    }
    return mockPost.mock.calls[0]?.[0] === url;
  });
  expect(target, `RunPromptForm posts to ${url}`).toBeTruthy();
  return { client, options: target };
}

async function toastsForFailed(url, error) {
  const { client, options } = await mountAndFindMutation(url);
  mockPost.mockReset();
  mockPost.mockRejectedValue(error);
  mockEnqueueSnackbar.mockClear();
  await new MutationObserver(client, options).mutate({}).catch(() => {});
  return mockEnqueueSnackbar.mock.calls.map(([message]) => message);
}

describe("Run Prompt drawer request failures", () => {
  beforeEach(() => {
    mockMutationOptions.length = 0;
    mockPost.mockReset();
    mockEnqueueSnackbar.mockClear();
  });

  it.each([
    ["as captured", CATALOG_MISS_AS_CAPTURED],
    ["current wording", CATALOG_MISS],
  ])(
    "Test shows the catalog-miss 400 naming the model once (%s)",
    async (_name, message) => {
      const toasts = await toastsForFailed(
        endpoints.develop.runPrompt.preview,
        rejectedWith(400, "validation_error", "invalid", message),
      );

      expect(toasts).toEqual([message]);
    },
  );

  it("Run shows the retired-model 400 naming the model once", async () => {
    const toasts = await toastsForFailed(
      endpoints.develop.runPrompt.create,
      rejectedWith(400, "validation_error", "invalid", RETIRED_ON_RUN),
    );

    expect(toasts).toEqual([RETIRED_ON_RUN]);
  });

  it("Test still hides query text, even in a 400", async () => {
    const toasts = await toastsForFailed(
      endpoints.develop.runPrompt.preview,
      rejectedWith(
        400,
        "validation_error",
        "invalid",
        "Query failed: SELECT id FROM model_hub_row WHERE dataset_id = 'd1'",
      ),
    );

    expect(toasts).toEqual(["Something went wrong"]);
  });
});
