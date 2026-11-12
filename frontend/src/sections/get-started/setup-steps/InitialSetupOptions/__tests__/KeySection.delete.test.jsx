/* eslint-disable react/prop-types */
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import {
  render,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import { handleError } from "src/utils/queryErrorHandler";
import KeySection from "../KeySection";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  del: vi.fn(),
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get, delete: mocks.del },
  endpoints: {
    develop: {
      apiKey: { delete: (id) => `/model-hub/api-keys/${id}/` },
    },
    settings: {
      customModal: {
        getCustomModal: "/model-hub/custom-models/",
        deleteModel: "/model-hub/custom-models/delete/",
      },
    },
  },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: mocks.enqueueSnackbar }));
vi.mock("src/api/model/api-keys", () => ({
  useApiKeysStatus: () => ({
    data: [
      {
        id: "key-1",
        provider: "openai",
        display_name: "OpenAI",
        type: "text",
        hasKey: true,
        maskedKey: "sk-****",
      },
    ],
    isFetching: false,
  }),
}));
// The card's own delete icon only calls onDeleteClick; a plain button stands in.
vi.mock("src/sections/develop-detail/Common/ConfigureKeys/KeyCard", () => ({
  default: ({ data, onDeleteClick }) => (
    <button type="button" onClick={onDeleteClick}>
      {`Delete ${data.display_name} key`}
    </button>
  ),
}));
vi.mock("src/components/custom-model-dropdown/CustomModalKeyCard", () => ({
  default: () => null,
}));
vi.mock("src/components/custom-model-dropdown/CloudProviderCard", () => ({
  default: () => null,
}));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
// The credits route above the cards is covered by
// get-started-credits-route.test.jsx; here the credits status stays loading.
vi.mock("src/sections/gateway/models/hooks/useManagedModels", () => ({
  useManagedModelsStatus: () => ({ data: undefined, isLoading: true }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 15, currentOrganizationId: "org-1" }),
}));

// The app's wiring (src/app.jsx): a failed mutation also reaches handleError.
function renderKeySection() {
  const queryClient = new QueryClient({
    mutationCache: new MutationCache({ onError: handleError }),
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <KeySection setCurrentLabel={vi.fn()} />
    </QueryClientProvider>,
  );
}

describe("Get started, Add keys step: deleting a saved provider key", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.get.mockResolvedValue({ data: { results: [] } });
    mocks.del.mockResolvedValue({ data: {} });
  });

  it("asks for confirmation, then deletes the key", async () => {
    const user = userEvent.setup();
    renderKeySection();

    await user.click(
      await screen.findByRole("button", { name: "Delete OpenAI key" }),
    );

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Delete API key")).toBeInTheDocument();
    expect(
      within(dialog).getByText("Are you sure you want to delete this API key?"),
    ).toBeInTheDocument();
    expect(mocks.del).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() =>
      expect(mocks.del).toHaveBeenCalledWith("/model-hub/api-keys/key-1/"),
    );
  });

  it("keeps the key when the user cancels", async () => {
    const user = userEvent.setup();
    renderKeySection();

    await user.click(
      await screen.findByRole("button", { name: "Delete OpenAI key" }),
    );
    const dialog = await screen.findByRole("dialog");

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(mocks.del).not.toHaveBeenCalled();
  });

  it("says the delete failed when the request gets no response, and keeps the dialog open to try again", async () => {
    // What the axios interceptor rejects with when no response arrives.
    mocks.del.mockRejectedValueOnce({
      message: "Network Error",
      statusCode: undefined,
      transportCode: "ERR_NETWORK",
    });
    const user = userEvent.setup();
    renderKeySection();

    await user.click(
      await screen.findByRole("button", { name: "Delete OpenAI key" }),
    );
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() =>
      expect(mocks.enqueueSnackbar).toHaveBeenCalledWith(
        "We couldn't delete this API key. Try again.",
        { variant: "error" },
      ),
    );
    expect(mocks.enqueueSnackbar).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await waitFor(() =>
      expect(
        within(dialog).getByRole("button", { name: "Delete" }),
      ).toBeEnabled(),
    );
  });
});
