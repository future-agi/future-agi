import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import ManagedModelsCard from "./ManagedModelsCard";

const { post, snackbars } = vi.hoisted(() => ({
  post: vi.fn(),
  snackbars: [],
}));

vi.mock("src/utils/axios", () => ({
  default: { post, get: vi.fn() },
  endpoints: {
    gateway: { updateConfig: (id) => `/gateway/${id}/config/update` },
  },
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: (message, options) => snackbars.push({ message, options }),
}));

const STATUS = {
  available: true,
  enabled: false,
  balance_usd: 20,
  markup_percent: 5,
};

const renderCard = (props = {}) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          mutationCache: new MutationCache({ onError: () => {} }),
          defaultOptions: { mutations: { retry: false } },
        })
      }
    >
      <ManagedModelsCard
        status={STATUS}
        gatewayId="gw-1"
        canWrite
        onRetry={vi.fn()}
        {...props}
      />
    </QueryClientProvider>,
  );

beforeEach(() => {
  post.mockReset();
  snackbars.length = 0;
});

describe("ManagedModelsCard", () => {
  it("shows markup and balance", () => {
    renderCard();
    expect(screen.getByText("Markup")).toBeInTheDocument();
    expect(screen.getAllByText(/5%/).length).toBeGreaterThan(0);
    expect(screen.getByText("$20.00")).toBeInTheDocument();
    expect(screen.getByText("Off")).toBeInTheDocument();
  });

  it("asks before enabling, then opts the org in", async () => {
    post.mockResolvedValue({
      data: { status: true, result: { version: 2, gateway_synced: true } },
    });
    renderCard();

    await userEvent.click(screen.getByLabelText("Use managed models"));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Enable managed models?");
    expect(dialog).toHaveTextContent(
      "Requests to managed models are billed to your organization at provider cost + 5%",
    );
    expect(post).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Enable" }));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post).toHaveBeenCalledWith(
      "/gateway/gw-1/config/update",
      { managed_models: { enabled: true } },
      expect.any(Object),
    );
    await waitFor(() =>
      expect(snackbars).toContainEqual({
        message: "Managed models enabled",
        options: { variant: "success" },
      }),
    );
  });

  it("sends nothing when the confirmation is cancelled", async () => {
    renderCard();
    await userEvent.click(screen.getByLabelText("Use managed models"));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(post).not.toHaveBeenCalled();
  });

  it("confirms before disabling too", async () => {
    post.mockResolvedValue({ data: { status: true, result: {} } });
    renderCard({ status: { ...STATUS, enabled: true } });

    await userEvent.click(screen.getByLabelText("Use managed models"));
    expect(await screen.findByRole("dialog")).toHaveTextContent(
      "Requests to managed models will be refused",
    );
    await userEvent.click(screen.getByRole("button", { name: "Disable" }));

    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        "/gateway/gw-1/config/update",
        { managed_models: { enabled: false } },
        expect.any(Object),
      ),
    );
  });

  it("warns that a sync failure leaves the gateway on the old setting", async () => {
    post.mockResolvedValue({
      data: {
        status: true,
        result: { gateway_synced: false, gateway_warning: "Sync failed." },
      },
    });
    renderCard();
    await userEvent.click(screen.getByLabelText("Use managed models"));
    await userEvent.click(screen.getByRole("button", { name: "Enable" }));

    // Saved, not applied: one warning that says so, never the
    // "Managed models enabled" success toast.
    await waitFor(() =>
      expect(snackbars).toContainEqual({
        message: "Saved, but the gateway did not apply it: Sync failed.",
        options: { variant: "warning" },
      }),
    );
    expect(snackbars).not.toContainEqual(
      expect.objectContaining({ options: { variant: "success" } }),
    );
  });

  it("reports a failed save", async () => {
    post.mockRejectedValue({ statusCode: 400, result: "Gateway error: down" });
    renderCard();
    await userEvent.click(screen.getByLabelText("Use managed models"));
    await userEvent.click(screen.getByRole("button", { name: "Enable" }));

    await waitFor(() =>
      expect(snackbars).toContainEqual({
        message: "Gateway error: down",
        options: { variant: "error" },
      }),
    );
  });

  it("warns that requests are refused when an enabled org has no credit", () => {
    renderCard({ status: { ...STATUS, enabled: true, balance_usd: 0 } });
    expect(screen.getByRole("alert")).toHaveTextContent(
      /credit is used up.*refuses requests to managed models \(HTTP 402\)/,
    );
  });

  it("does not warn about credit while managed models are off", () => {
    renderCard({ status: { ...STATUS, balance_usd: 0 } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  // A deployment that offers no managed models has nothing to switch on, so
  // the Models page shows no card rather than a refusal.
  it("renders nothing when the deployment has no managed models", () => {
    const { container } = renderCard({
      status: {
        available: false,
        enabled: true,
        balance_usd: null,
        markup_percent: null,
      },
    });
    expect(container).toBeEmptyDOMElement();
  });

  it("locks the switch without write access", () => {
    renderCard({ canWrite: false });
    expect(screen.getByLabelText("Use managed models")).toBeDisabled();
    expect(
      screen.getByText(/only an organization Owner or Admin can turn/),
    ).toBeInTheDocument();
  });
});
