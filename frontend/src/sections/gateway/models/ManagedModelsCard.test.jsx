import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import {
  render,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import ManagedModelsCard from "./ManagedModelsCard";

const { post, get, snackbars } = vi.hoisted(() => ({
  post: vi.fn(),
  get: vi.fn(),
  snackbars: [],
}));

vi.mock("src/utils/axios", () => ({
  default: { post, get },
  endpoints: {
    gateway: {
      updateConfig: (id) => `/gateway/${id}/config/update`,
    },
  },
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: (message, options) => snackbars.push({ message, options }),
}));

const STATUS = {
  available: true,
  enabled: true,
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
  get.mockReset();
  post.mockReset();
  snackbars.length = 0;
});

const SWITCH = "Future AGI credits";

describe("ManagedModelsCard", () => {
  it("is titled Future AGI credits and never says managed", () => {
    const { container } = renderCard({
      status: { ...STATUS, enabled: true, balance_usd: 0 },
    });
    expect(screen.getByText("Future AGI credits")).toBeInTheDocument();
    expect(screen.getByLabelText(SWITCH)).toBeInTheDocument();
    expect(container).toHaveTextContent(
      "billed at the Future AGI price (provider + 5%)",
    );
    expect(container).not.toHaveTextContent(/managed/i);
  });

  it("qualifies the one-key promise with credit eligibility", () => {
    renderCard();
    expect(
      screen.getByText(
        /Call credit-eligible catalog models with one Future AGI key/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /Models marked .Not available on credits. cannot use this balance/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/every catalog(?:ue)? model/i),
    ).not.toBeInTheDocument();
  });

  it("shows markup and balance", () => {
    renderCard();
    expect(screen.getByText("Markup")).toBeInTheDocument();
    expect(screen.getAllByText(/5%/).length).toBeGreaterThan(0);
    expect(screen.getByText("$20.00")).toBeInTheDocument();
    expect(screen.getByText("Enabled")).toBeInTheDocument();
  });

  it("does not expose the new credits switch to an organization that is not enabled", () => {
    renderCard({ status: { ...STATUS, enabled: false } });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Future AGI credits are off for this organization.",
    );
    expect(screen.queryByLabelText(SWITCH)).toBeNull();
    expect(screen.queryByText("Credit balance")).toBeNull();
    expect(post).not.toHaveBeenCalled();
  });

  it("sends nothing when the confirmation is cancelled", async () => {
    renderCard();
    await userEvent.click(screen.getByLabelText(SWITCH));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(post).not.toHaveBeenCalled();
  });

  it("confirms before disabling too", async () => {
    post.mockResolvedValue({ data: { status: true, result: {} } });
    renderCard({ status: { ...STATUS, enabled: true } });

    await userEvent.click(screen.getByLabelText(SWITCH));
    expect(await screen.findByRole("dialog")).toHaveTextContent(
      "Requests to Future AGI models will be refused",
    );
    await userEvent.click(screen.getByRole("button", { name: "Turn off" }));

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
    await userEvent.click(screen.getByLabelText(SWITCH));
    await userEvent.click(screen.getByRole("button", { name: "Turn off" }));

    // NG-34: saved, not applied: one warning that says so, never the
    // "Future AGI credits turned on" success toast.
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
    await userEvent.click(screen.getByLabelText(SWITCH));
    await userEvent.click(screen.getByRole("button", { name: "Turn off" }));

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
      /credits are used up.*refuses requests to Future AGI models \(HTTP 402\)/,
    );
  });

  it("cuts the balance to the cent, never rounding it up", () => {
    renderCard({ status: { ...STATUS, balance_usd: 34.999336 } });
    expect(screen.getByText("$34.999336")).toBeInTheDocument();
    expect(screen.queryByText("$35.00")).not.toBeInTheDocument();
  });

  // The console sells no credit (no /agentcc/managed-credits/ API): a used-up
  // balance says who adds credit, with no Billing link and no credit read.
  it("says who adds used-up credit, with no Billing link and no credit request", () => {
    renderCard({ status: { ...STATUS, enabled: true, balance_usd: 0 } });
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(
      "Your Future AGI credits are used up. The gateway refuses requests to Future AGI models (HTTP 402) until credit is added; contact your Future AGI account team to add credit. Requests through your own provider keys are not affected.",
    );
    expect(within(alert).queryByRole("link")).toBeNull();
    expect(screen.queryByText(/Billing/)).toBeNull();
    expect(get).not.toHaveBeenCalled();
  });

  it("shows the switch state and balance under the labels the console uses", () => {
    renderCard({ status: { ...STATUS, enabled: true } });
    expect(screen.getByText("Enabled")).toBeInTheDocument();
    expect(screen.getByText("Credit balance")).toBeInTheDocument();
  });

  it("states the off state without a credit exhaustion warning", () => {
    renderCard({ status: { ...STATUS, enabled: false, balance_usd: 0 } });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Future AGI credits are off for this organization.",
    );
    expect(screen.queryByText(/credits are used up/)).toBeNull();
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
    expect(screen.getByLabelText(SWITCH)).toBeDisabled();
    expect(
      screen.getByText(/only an organization Owner or Admin can turn/),
    ).toBeInTheDocument();
  });
});
