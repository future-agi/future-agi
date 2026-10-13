import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import { BillingPanel } from "./OmegaSquadPage";

describe("Omega Squad billing controls", () => {
  it("opens hosted checkout and confirms cancellation without collecting payment data", async () => {
    const onCheckout = vi.fn();
    const onCancelSubscription = vi.fn();
    vi.spyOn(globalThis, "confirm").mockReturnValue(true);
    const props = {
      accountsResult: {
        available: true,
        accounts: [{ id: "acct-1", name: "Acme" }],
      },
      plansResult: {
        available: true,
        plans: [{ id: "pro", name: "Pro", purchasable: true }],
      },
      accountId: "acct-1",
      onSelectAccount: vi.fn(),
      data: {
        subscription: {
          available: true,
          subscription: { status: "active", planId: "pro" },
        },
      },
      period: {
        start: "2026-10-01T00:00:00.000Z",
        end: "2026-11-01T00:00:00.000Z",
      },
      loading: false,
      mutation: { busy: false, error: null, checkoutUrl: "" },
      seatQuote: null,
      onSeatQuote: vi.fn(),
      onCheckout,
      onCancelSubscription,
      onRefresh: vi.fn(),
    };
    render(<BillingPanel {...props} />);

    expect(screen.getByText("Remote plan management")).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Cancel subscription" }),
    );
    await waitFor(() =>
      expect(onCancelSubscription).toHaveBeenCalledWith("acct-1"),
    );

    const activeProps = {
      ...props,
      data: {
        subscription: { available: true, subscription: { status: "none" } },
      },
    };
    render(<BillingPanel {...activeProps} />);
    await userEvent.click(
      screen.getByRole("button", { name: "Open hosted checkout" }),
    );
    await waitFor(() =>
      expect(onCheckout).toHaveBeenCalledWith("acct-1", "pro"),
    );
    expect(
      screen.queryByLabelText(/card|payment|secret/i),
    ).not.toBeInTheDocument();
  });
});
