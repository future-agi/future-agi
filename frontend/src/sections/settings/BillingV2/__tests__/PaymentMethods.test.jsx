import { describe, it, expect, vi, beforeEach } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, waitFor } from "src/utils/test-utils";
import {
  MutationCache,
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockDelete = vi.fn();
const mockPut = vi.fn();

vi.mock("src/utils/axios", () => ({
  default: {
    get: (...args) => mockGet(...args),
    post: (...args) => mockPost(...args),
    delete: (...args) => mockDelete(...args),
    put: (...args) => mockPut(...args),
  },
  endpoints: {
    settings: {
      v2: {
        paymentMethods: "/usage/v2/payment-methods/",
        paymentMethodSetupIntent: "/usage/v2/payment-methods/setup-intent/",
        paymentMethodDefault: (id) =>
          `/usage/v2/payment-methods/${id}/default/`,
        paymentMethodDelete: (id) => `/usage/v2/payment-methods/${id}/`,
      },
    },
  },
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

// Mirror app.jsx's global mutation error handler so these tests exercise the
// same double-toast surface that prod users hit.
const handleError = (error, variable, context, mutation) => {
  if (mutation?.options?.meta?.errorHandled) return;
  if (error?.result) {
    enqueueSnackbar(`${error.result}`, { variant: "error" });
  }
};

function renderWithQuery(ui) {
  const qc = new QueryClient({
    queryCache: new QueryCache({ onError: handleError }),
    mutationCache: new MutationCache({ onError: handleError }),
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const MOCK_CARDS = {
  data: {
    result: [
      {
        id: "pm_abc",
        brand: "Visa",
        last4: "4242",
        exp_month: 12,
        exp_year: 2028,
        is_default: true,
      },
    ],
  },
};

describe("PaymentMethods — single-toast on remove (TH-4510)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockResolvedValue(MOCK_CARDS);
  });

  it("shows exactly one warning toast when delete fails", async () => {
    // Axios interceptor flattens errors; mimic that rejection shape.
    mockDelete.mockRejectedValue({
      result: "Cannot remove the only payment method on a paid plan.",
      statusCode: 400,
    });

    const { default: PaymentMethods } = await import("../PaymentMethods");
    renderWithQuery(<PaymentMethods />);

    await waitFor(() => {
      expect(screen.getByText("Visa •••• 4242")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByTitle("Remove card"));
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));

    await waitFor(() => {
      expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    });
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "Cannot remove the only payment method on a paid plan.",
      { variant: "error" },
    );
  });

  it("shows the backend reason, not a generic fallback, when available", async () => {
    mockDelete.mockRejectedValue({
      result: "Stripe error: card already detached.",
      statusCode: 400,
    });

    const { default: PaymentMethods } = await import("../PaymentMethods");
    renderWithQuery(<PaymentMethods />);

    await waitFor(() => {
      expect(screen.getByText("Visa •••• 4242")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByTitle("Remove card"));
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));

    await waitFor(() => {
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        "Stripe error: card already detached.",
        { variant: "error" },
      );
    });
  });
});


describe("platform card checkout returns", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockResolvedValue(MOCK_CARDS);
    mockPut.mockResolvedValue({ data: { result: {} } });
  });

  it("confirms card=added once and leaves unrelated query parameters", async () => {
    const prior = window.location.pathname + window.location.search + window.location.hash;
    window.history.replaceState({}, "", "/dashboard/settings/billing?card=added&session_id=cs_card&keep=1");
    try {
      const { default: PaymentMethods } = await import("../PaymentMethods");
      const view = renderWithQuery(<PaymentMethods />);
      await waitFor(() => expect(mockPut).toHaveBeenCalledTimes(1));
      expect(mockPut).toHaveBeenCalledWith("/usage/v2/payment-methods/", { session_id: "cs_card" });
      await waitFor(() => expect(enqueueSnackbar).toHaveBeenCalledWith("Card added successfully", { variant: "success" }));
      expect(window.location.search).toBe("?keep=1");
      view.unmount();
      renderWithQuery(<PaymentMethods />);
      await screen.findByText("Visa •••• 4242");
      expect(mockPut).toHaveBeenCalledTimes(1);
      expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    } finally { window.history.replaceState({}, "", prior); }
  });

  it.each([
    "topup=success&session_id=cs_credit",
    "checkout_session=cs_credit",
    "topup=cancel&session_id=cs_credit",
  ])("does not confirm a platform card for a credit return (%s)", async (query) => {
    const prior = window.location.pathname + window.location.search + window.location.hash;
    window.history.replaceState({}, "", `/dashboard/settings/billing?${query}&keep=1#ai-gateway-credits`);
    try {
      const { default: PaymentMethods } = await import("../PaymentMethods");
      renderWithQuery(<PaymentMethods />);
      await screen.findByText("Visa •••• 4242");
      expect(mockPut).not.toHaveBeenCalled();
      expect(mockPost).not.toHaveBeenCalled();
      expect(enqueueSnackbar).not.toHaveBeenCalled();
      expect(window.location.search).toBe(`?${query}&keep=1`);
      expect(window.location.hash).toBe("#ai-gateway-credits");
    } finally { window.history.replaceState({}, "", prior); }
  });

  it("confirms nothing for card=cancelled", async () => {
    const prior = window.location.pathname + window.location.search + window.location.hash;
    window.history.replaceState({}, "", "/dashboard/settings/billing?card=cancelled&session_id=cs_card&keep=1");
    try {
      const { default: PaymentMethods } = await import("../PaymentMethods");
      renderWithQuery(<PaymentMethods />);
      await screen.findByText("Visa •••• 4242");
      expect(mockPut).not.toHaveBeenCalled();
      expect(enqueueSnackbar).toHaveBeenCalledWith("Card setup cancelled", { variant: "info" });
      expect(window.location.search).toBe("?keep=1");
    } finally { window.history.replaceState({}, "", prior); }
  });
});
