import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, act } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import { SnackbarProvider } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import HuggingFaceView from "../HuggingFaceView";

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { post: vi.fn() },
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: {},
  PropertyName: {},
}));

const sourceId = "example/synthetic-rows";
const detail = {
  id: sourceId,
  name: "Synthetic rows",
  description: "Local import fixture",
};
const configs = {
  data: {
    result: {
      dataset_info: {
        splits: {
          default: ["train", "test"],
          alternate: ["validation", "holdout"],
        },
      },
    },
  },
};
const clients = [];
let resolveConfigs;

beforeEach(() => {
  axios.post.mockReset();
  axios.post.mockImplementation((url) => {
    if (url === endpoints.huggingFace.list) {
      return Promise.resolve({
        data: { result: { datasets: [detail], total_datasets: 1 } },
      });
    }
    if (url === endpoints.huggingFace.detail) {
      return Promise.resolve({ data: { result: { dataset: detail } } });
    }
    if (url === endpoints.develop.getHuggingFaceDataset) {
      return new Promise((resolve) => {
        resolveConfigs = resolve;
      });
    }
    return Promise.resolve({
      data: { result: { dataset_id: "created-dataset" } },
    });
  });
});

afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});

function renderView(existing = true) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(client);
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter
        initialEntries={[
          {
            pathname: "/huggingface",
            state: existing ? { datasetId: "existing-dataset" } : null,
          },
        ]}
      >
        <Routes>
          <Route path="/huggingface" element={<HuggingFaceView />} />
          <Route
            path="/dashboard/develop/:id"
            element={<div>Import destination</div>}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
    // The route under test owns its router, rather than the shared BrowserRouter.
    {
      wrapper: ({ children }) => (
        <ThemeProvider theme={createTheme({ palette: palette("light") })}>
          <SnackbarProvider>{children}</SnackbarProvider>
        </ThemeProvider>
      ),
    },
  );
  return client;
}

async function openAndLoad() {
  const user = userEvent.setup();
  await user.click(await screen.findByText(sourceId));
  await waitFor(() => expect(resolveConfigs).toBeTypeOf("function"));
  expect(screen.getByLabelText(/Subset/)).toHaveValue("");
  await act(async () => resolveConfigs(configs));
  await waitFor(() =>
    expect(screen.getByLabelText(/Subset/)).toHaveValue("default"),
  );
  await waitFor(() =>
    expect(screen.getByLabelText(/Split/)).toHaveValue("train"),
  );
  return user;
}

async function choose(user, label, option) {
  await user.click(screen.getByLabelText(label));
  await user.click(await screen.findByText(option, { exact: true }));
}

async function submit(
  user,
  existing,
  subset,
  split,
  rows,
  name = "Synthetic rows",
) {
  await user.click(screen.getByRole("button", { name: "Start Experimenting" }));
  const payload = {
    huggingface_dataset_name: sourceId,
    huggingface_dataset_config: subset,
    huggingface_dataset_split: split,
    num_rows: rows,
    ...(existing ? {} : { name, model_type: "GenerativeLLM" }),
  };
  await waitFor(() =>
    expect(axios.post).toHaveBeenCalledWith(
      existing
        ? endpoints.huggingFace.addHuggingFaceRow("existing-dataset")
        : endpoints.develop.createHuggingFaceDataset,
      payload,
      expect.any(Object),
    ),
  );
  expect(await screen.findByText("Import destination")).toBeInTheDocument();
}

describe("Hugging Face parent import form", () => {
  it("submits asynchronous defaults when adding rows to an existing dataset", async () => {
    renderView();
    const user = await openAndLoad();
    expect(screen.queryByLabelText(/^Name/)).not.toBeInTheDocument();
    await submit(user, true, "default", "train", 1);
  });

  it("keeps the chosen subset, replaces its invalid split, and submits the manual row count", async () => {
    const client = renderView();
    const user = await openAndLoad();
    await choose(user, /Split/, "test");
    await choose(user, /Subset/, "alternate");
    await waitFor(() =>
      expect(screen.getByLabelText(/Split/)).toHaveValue("validation"),
    );
    await choose(user, /Split/, "holdout");
    await user.clear(screen.getByLabelText(/No. of rows/));
    await user.type(screen.getByLabelText(/No. of rows/), "7");
    act(() =>
      client.setQueryData(
        ["loadedDataset", sourceId],
        structuredClone(configs),
      ),
    );
    expect(screen.getByLabelText(/Subset/)).toHaveValue("alternate");
    expect(screen.getByLabelText(/Split/)).toHaveValue("holdout");
    await submit(user, true, "alternate", "holdout", 7);
  });

  it.each([true, false])(
    "preserves a cleared row-count edit during an asynchronous options refresh (existing=%s)",
    async (existing) => {
      const client = renderView(existing);
      const user = await openAndLoad();
      const rows = screen.getByLabelText(/No. of rows/);
      await user.clear(rows);
      const previousResolver = resolveConfigs;
      act(() => {
        void client.invalidateQueries({
          queryKey: ["loadedDataset", sourceId],
        });
      });
      await waitFor(() => expect(resolveConfigs).not.toBe(previousResolver));
      const refreshed = structuredClone(configs);
      refreshed.data.result.dataset_info.splits.fresh = ["test"];
      await act(async () => resolveConfigs(refreshed));
      await user.click(screen.getByLabelText(/Subset/));
      expect(
        await screen.findByText("fresh", { exact: true }),
      ).toBeInTheDocument();
      await user.click(screen.getByText("default", { exact: true }));
      await user.type(rows, "4");
      await submit(user, existing, "default", "train", 4);
    },
  );

  it("reinitializes the existing-dataset drawer after cancel and reopen", async () => {
    renderView();
    const user = await openAndLoad();
    await choose(user, /Subset/, "alternate");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await user.click(screen.getByText(sourceId));
    await waitFor(() =>
      expect(screen.getByLabelText(/Subset/)).toHaveValue("default"),
    );
    await waitFor(() =>
      expect(screen.getByLabelText(/Split/)).toHaveValue("train"),
    );
    await submit(user, true, "default", "train", 1);
  });

  it("preserves the new dataset name and selected subset through split updates", async () => {
    renderView(false);
    const user = await openAndLoad();
    await user.clear(screen.getByLabelText(/^Name/));
    await user.type(screen.getByLabelText(/^Name/), "Custom import");
    await choose(user, /Subset/, "alternate");
    await waitFor(() =>
      expect(screen.getByLabelText(/Split/)).toHaveValue("validation"),
    );
    expect(screen.getByLabelText(/^Name/)).toHaveValue("Custom import");
    await submit(user, false, "alternate", "validation", 1, "Custom import");
  });
});
