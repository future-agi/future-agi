import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import CreateKeyDialog from "./CreateKeyDialog";

const mockCreateApiKey = vi.fn();

vi.mock("src/components/iconify", () => ({
  default: (props) => <span data-testid="iconify" {...props} />,
}));

vi.mock("./hooks/useApiKeys", () => ({
  useCreateApiKey: () => ({
    isError: false,
    isPending: false,
    mutate: mockCreateApiKey,
  }),
}));

describe("CreateKeyDialog onboarding callbacks", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockCreateApiKey.mockImplementation((_payload, options) => {
      options?.onSuccess?.({
        gatewayKeyId: "gw-key-1",
        id: "key-1",
        key: "secret-key",
        keyPrefix: "fagi_",
        name: "production",
      });
    });
  });

  it("notifies onboarding when a gateway key is created and done", async () => {
    const onClose = vi.fn();
    const onDone = vi.fn();
    const onKeyCreated = vi.fn();

    render(
      <CreateKeyDialog
        gatewayId="gateway-1"
        onClose={onClose}
        onDone={onDone}
        onKeyCreated={onKeyCreated}
        open
      />,
    );

    fireEvent.change(screen.getByLabelText(/name/i), {
      target: { value: "production" },
    });
    fireEvent.change(screen.getByLabelText(/owner/i), {
      target: { value: "backend" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^create$/i }));

    await waitFor(() => {
      expect(onKeyCreated).toHaveBeenCalledWith(
        expect.objectContaining({
          gatewayKeyId: "gw-key-1",
          id: "key-1",
          keyPrefix: "fagi_",
        }),
        {
          allowedModels: [],
          allowedProviders: [],
          gatewayId: "gateway-1",
          keyName: "production",
          owner: "backend",
        },
      );
    });

    fireEvent.click(screen.getByRole("button", { name: /done/i }));

    expect(onDone).toHaveBeenCalledWith(
      expect.objectContaining({
        gatewayKeyId: "gw-key-1",
        id: "key-1",
      }),
    );
    expect(onClose).toHaveBeenCalled();
  });
});
