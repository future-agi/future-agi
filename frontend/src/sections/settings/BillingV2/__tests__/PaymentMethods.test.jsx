import { StrictMode } from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import userEvent from "@testing-library/user-event";
import { act, cleanup, render, screen, waitFor } from "src/utils/test-utils";
import {
  MutationCache,
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";

// Billing writes require authority in the selected organization.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 15 }),
}));

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
    mockPut.mockResolvedValue({ data: { status: true, result: { payment_method_id: "pm_abc", set_as_default: false } } });
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

const CONFIRMED_CARD = {
  data: {
    status: true,
    result: { payment_method_id: "pm_abc", set_as_default: false },
  },
};

const RETURN_URL =
  "/settings/billing?tab=payments&card=added&session_id=cs_return#cards";
const CONFIRMATION_ERROR =
  "We couldn't confirm your card. Retry confirmation before using it for billing.";

async function renderReturnedCard({ strict = false } = {}) {
  const { default: PaymentMethods } = await import("../PaymentMethods");
  return renderWithQuery(
    strict ? (
      <StrictMode>
        <PaymentMethods />
      </StrictMode>
    ) : (
      <PaymentMethods />
    ),
  );
}

function expectNoCardSuccess() {
  expect(enqueueSnackbar).not.toHaveBeenCalledWith("Card added successfully", {
    variant: "success",
  });
}

function expectReturnSession() {
  const params = new URLSearchParams(window.location.search);
  expect(params.get("card")).toBe("added");
  expect(params.get("session_id")).toBe("cs_return");
}

function deferredConfirmation() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

describe("PaymentMethods — checkout return confirmation (W02-003)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockPut.mockReset();
    mockGet.mockResolvedValue(MOCK_CARDS);
    window.history.replaceState({ billingReturn: true }, "", RETURN_URL);
  });

  afterEach(() => {
    cleanup();
    window.history.replaceState({}, "", "/");
  });

  it("waits for accepted confirmation before success and preserves unrelated URL state", async () => {
    const confirmation = deferredConfirmation();
    mockPut.mockReturnValueOnce(confirmation.promise);
    await renderReturnedCard();

    await screen.findByText("Confirming your card…");
    expect(mockPut).toHaveBeenCalledExactlyOnceWith(
      "/usage/v2/payment-methods/",
      { session_id: "cs_return" },
    );
    expectReturnSession();
    expectNoCardSuccess();

    await act(async () => confirmation.resolve(CONFIRMED_CARD));

    await waitFor(() => {
      expect(enqueueSnackbar).toHaveBeenCalledExactlyOnceWith(
        "Card added successfully",
        { variant: "success" },
      );
      expect(mockGet).toHaveBeenCalledTimes(2);
    });
    expect(window.location.pathname).toBe("/settings/billing");
    expect(window.location.search).toBe("?tab=payments");
    expect(window.location.hash).toBe("#cards");
    // The router adds its own idx to history state; the confirmation must keep the billing return marker.
    expect(window.history.state).toMatchObject({ billingReturn: true });
    expect(screen.queryByText("Confirming your card…")).not.toBeInTheDocument();
  });

  it("shows failure without success, retains the session, and retries that session", async () => {
    mockPut.mockRejectedValueOnce({ result: "Service unavailable" });
    const retry = deferredConfirmation();
    mockPut.mockReturnValueOnce(retry.promise);
    await renderReturnedCard();

    await screen.findByText(CONFIRMATION_ERROR);
    expectNoCardSuccess();
    expectReturnSession();
    expect(mockGet).toHaveBeenCalledTimes(1);

    await userEvent.click(
      screen.getByRole("button", { name: "Retry confirmation" }),
    );
    expect(mockPut).toHaveBeenNthCalledWith(2, "/usage/v2/payment-methods/", {
      session_id: "cs_return",
    });
    expect(screen.getByText("Confirming your card…")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Retry confirmation" }),
    ).not.toBeInTheDocument();
    expectReturnSession();
    expectNoCardSuccess();

    await act(async () => retry.resolve(CONFIRMED_CARD));
    await waitFor(() => {
      expect(enqueueSnackbar).toHaveBeenCalledExactlyOnceWith(
        "Card added successfully",
        { variant: "success" },
      );
    });
    expect(screen.queryByText(CONFIRMATION_ERROR)).not.toBeInTheDocument();
    expect(window.location.search).toBe("?tab=payments");
  });

  it("keeps retry available after a second confirmation failure", async () => {
    mockPut.mockRejectedValue({ result: "Service unavailable" });
    await renderReturnedCard();
    await screen.findByText(CONFIRMATION_ERROR);
    await userEvent.click(
      screen.getByRole("button", { name: "Retry confirmation" }),
    );

    await screen.findByText(CONFIRMATION_ERROR);
    expect(mockPut).toHaveBeenCalledTimes(2);
    expectReturnSession();
    expectNoCardSuccess();
    expect(
      screen.getByRole("button", { name: "Retry confirmation" }),
    ).toBeEnabled();
  });

  it("retries a failed return on remount using the retained URL", async () => {
    mockPut.mockRejectedValueOnce({ result: "Service unavailable" });
    const first = await renderReturnedCard();
    await screen.findByText(CONFIRMATION_ERROR);
    first.unmount();

    mockPut.mockResolvedValueOnce(CONFIRMED_CARD);
    await renderReturnedCard();
    await waitFor(() => {
      expect(mockPut).toHaveBeenCalledTimes(2);
      expect(enqueueSnackbar).toHaveBeenCalledExactlyOnceWith(
        "Card added successfully",
        { variant: "success" },
      );
    });
    expect(mockPut).toHaveBeenNthCalledWith(2, "/usage/v2/payment-methods/", {
      session_id: "cs_return",
    });
  });

  it.each(["", "&session_id="])(
    "does not claim success for a missing or empty session (%s)",
    async (sessionQuery) => {
      window.history.replaceState(
        {},
        "",
        `/settings/billing?card=added${sessionQuery}`,
      );
      await renderReturnedCard();

      await screen.findByText(
        "Missing card setup session. Please add your card again.",
      );
      expect(mockPut).not.toHaveBeenCalled();
      expectNoCardSuccess();
      expect(
        screen.queryByRole("button", { name: "Retry confirmation" }),
      ).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Add Card" })).toBeEnabled();
    },
  );

  it.each([
    { status: false, result: { payment_method_id: "pm_abc" } },
    { status: true, result: {} },
    { result: { payment_method_id: "pm_abc" } },
  ])("does not accept an unsuccessful or incomplete response (%j)", async (data) => {
    mockPut.mockResolvedValueOnce({ data });
    await renderReturnedCard();

    await screen.findByText(CONFIRMATION_ERROR);
    expectNoCardSuccess();
    expectReturnSession();
    expect(mockGet).toHaveBeenCalledTimes(1);
    expect(
      screen.getByRole("button", { name: "Retry confirmation" }),
    ).toBeEnabled();
  });

  it("does not double-confirm when the mount effect runs twice", async () => {
    const confirmation = deferredConfirmation();
    mockPut.mockReturnValue(confirmation.promise);
    await renderReturnedCard({ strict: true });

    await screen.findByText("Confirming your card…");
    expect(mockPut).toHaveBeenCalledTimes(1);
    expectReturnSession();
    await act(async () => confirmation.resolve(CONFIRMED_CARD));
    await waitFor(() => {
      expect(enqueueSnackbar).toHaveBeenCalledExactlyOnceWith(
        "Card added successfully",
        { variant: "success" },
      );
    });
    expect(mockPut).toHaveBeenCalledTimes(1);
  });

  it("keeps cancellation informational without attempting confirmation", async () => {
    window.history.replaceState(
      {},
      "",
      "/settings/billing?tab=payments&card=cancelled&session_id=cs_return#cards",
    );
    await renderReturnedCard({ strict: true });

    await waitFor(() => {
      expect(enqueueSnackbar).toHaveBeenCalledExactlyOnceWith(
        "Card setup cancelled",
        { variant: "info" },
      );
    });
    expect(mockPut).not.toHaveBeenCalled();
    expect(window.location.search).toBe("?tab=payments");
    expect(window.location.hash).toBe("#cards");
    expectNoCardSuccess();
  });

  it("does not confirm an ordinary billing page visit", async () => {
    window.history.replaceState({}, "", "/settings/billing?tab=payments#cards");
    await renderReturnedCard();

    await screen.findByText("Visa •••• 4242");
    expect(mockPut).not.toHaveBeenCalled();
    expect(enqueueSnackbar).not.toHaveBeenCalled();
    expect(window.location.search).toBe("?tab=payments");
    expect(window.location.hash).toBe("#cards");
  });
});
