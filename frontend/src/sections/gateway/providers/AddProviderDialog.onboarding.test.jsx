import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AddProviderDialog from "./AddProviderDialog";

const mockUpdateProvider = vi.fn();
const mockFetchModels = vi.fn();

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
  MaterialDesignContent: "div",
}));

vi.mock("./hooks/useGatewayConfig", () => ({
  useFetchProviderModels: () => ({
    isPending: false,
    mutate: mockFetchModels,
  }),
  useUpdateProvider: () => ({
    isError: false,
    isPending: false,
    mutate: mockUpdateProvider,
  }),
}));

describe("AddProviderDialog onboarding callbacks", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockFetchModels.mockImplementation((_payload, options) => {
      options?.onSuccess?.({ models: ["gpt-4o-mini"] });
    });
    mockUpdateProvider.mockImplementation((_payload, options) => {
      options?.onSuccess?.({});
    });
  });

  it("notifies onboarding after a provider is added", async () => {
    const onClose = vi.fn();
    const onProviderSaved = vi.fn();

    render(
      <AddProviderDialog
        gatewayId="gateway-1"
        onClose={onClose}
        onProviderSaved={onProviderSaved}
        open
      />,
    );

    fireEvent.change(screen.getByLabelText(/api key/i), {
      target: { value: "test-key" },
    });

    await waitFor(() => expect(mockFetchModels).toHaveBeenCalled());
    fireEvent.click(await screen.findByRole("button", { name: /select all/i }));

    fireEvent.click(screen.getByRole("button", { name: /add provider/i }));

    await waitFor(() => {
      expect(onProviderSaved).toHaveBeenCalledWith({
        apiFormat: "openai",
        gatewayId: "gateway-1",
        modelCount: 1,
        providerName: "openai",
      });
    });
    expect(onClose).toHaveBeenCalled();
  });
});
