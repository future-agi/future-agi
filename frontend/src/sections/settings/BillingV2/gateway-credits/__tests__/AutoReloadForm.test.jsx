import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import AutoReloadForm from "../AutoReloadForm";

vi.mock("../api", () => ({
  useSaveCreditSettings: () => ({ mutate: vi.fn(), isPending: false }),
}));

const SUMMARY = {
  enabled: true,
  low_balance_threshold_usd: "5.00",
  min_topup_usd: "25.00",
  auto_reload: { enabled: false, threshold_usd: null, amount_usd: null },
};

// The heading names only the controls the form shows.
describe("AutoReloadForm heading", () => {
  it("F4 renders no wallet settings for a disabled organization even when canBuy is true", () => {
    const { container } = render(
      <AutoReloadForm
        summary={{
          ...SUMMARY,
          enabled: false,
          billing_available: true,
          auto_reload: { enabled: false, disabled_reason: "the card failed" },
        }}
        canBuy
      />,
    );
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText(/Turn it on again/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  });
  it("without canBuy the heading does not mention auto-reload", () => {
    render(<AutoReloadForm summary={SUMMARY} canBuy={false} />);
    expect(
      screen.getByRole("region", { name: "Low balance alert" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/auto-reload/i)).not.toBeInTheDocument();
  });

  it("with canBuy the heading names the auto-reload switch it shows", () => {
    render(<AutoReloadForm summary={SUMMARY} canBuy />);
    expect(
      screen.getByRole("region", { name: "Low balance and auto-reload" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "Auto-reload" }),
    ).toBeInTheDocument();
  });

  it("without canBuy, a server-side turn-off is stated, with no switch to turn back on", () => {
    render(
      <AutoReloadForm
        summary={{
          ...SUMMARY,
          auto_reload: { enabled: false, disabled_reason: "the card failed" },
        }}
        canBuy={false}
      />,
    );
    expect(
      screen.getByText("Auto-reload was turned off: the card failed."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/turn it on again/i)).not.toBeInTheDocument();
  });
});
