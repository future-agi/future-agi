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
// The app's own global query/mutation error handler, so these tests exercise
// the same double-toast surface that prod users hit.
import { handleError } from "src/utils/queryErrorHandler";

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockDelete = vi.fn();

vi.mock("src/utils/axios", () => ({
  default: {
    get: (...args) => mockGet(...args),
    post: (...args) => mockPost(...args),
    delete: (...args) => mockDelete(...args),
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
