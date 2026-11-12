import React from "react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import AutoReloadForm from "../AutoReloadForm";

const save = vi.hoisted(() => ({ mutate: vi.fn(), isPending: false }));
vi.mock("../api", () => ({ useSaveCreditSettings: () => save }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const SUMMARY = {
  enabled: true,
  billing_available: false,
  low_balance_threshold_usd: "5.00",
  min_topup_usd: "25.00",
  auto_reload: { enabled: true, threshold_usd: "5.00", amount_usd: "30.00" },
};
const AGREE = "I agree to the auto-reload authorization above";
const NOT_AGREED =
  "Agree to the auto-reload authorization before saving with auto-reload on.";
const UNAVAILABLE =
  "Credit purchase is unavailable. You can turn off auto-reload and save without agreeing again.";
const DISABLED_BODY = {
  low_balance_threshold_usd: 5,
  auto_reload: { enabled: false, threshold_usd: null, amount_usd: null },
};
const ENABLED_BODY = {
  low_balance_threshold_usd: 5,
  auto_reload: {
    enabled: true,
    threshold_usd: 5,
    amount_usd: 30,
    consent_version: "auto-reload-v1",
  },
};
const toggle = () => screen.getByRole("checkbox", { name: "Auto-reload" });
const agreement = () => screen.getByRole("checkbox", { name: AGREE });
const submit = () => screen.getByRole("button", { name: "Save" });

beforeEach(() => {
  save.mutate.mockReset();
  save.isPending = false;
});

describe("auto-reload revocation and billing restoration", () => {
  it("revokes a persisted enabled preference while purchasing is unavailable, without consent", async () => {
    const user = userEvent.setup();
    render(<AutoReloadForm summary={SUMMARY} canBuy={false} />);
    expect(
      screen.getByRole("region", { name: "Low balance and auto-reload" }),
    ).toBeInTheDocument();
    expect(toggle()).toBeChecked();
    expect(toggle()).toBeEnabled();
    expect(agreement()).not.toBeChecked();
    await user.click(toggle());
    expect(screen.queryByRole("checkbox", { name: AGREE })).toBeNull();
    await user.click(submit());
    expect(save.mutate).toHaveBeenCalledTimes(1);
    expect(save.mutate.mock.calls[0][0]).toEqual(DISABLED_BODY);
    expect(save.mutate.mock.calls[0][0].auto_reload).not.toHaveProperty(
      "consent_version",
    );
  });

  it("cannot re-enable while unavailable, and invalid old reload values do not block revocation", async () => {
    const user = userEvent.setup();
    render(
      <AutoReloadForm
        summary={{
          ...SUMMARY,
          auto_reload: { enabled: true, threshold_usd: null, amount_usd: null },
        }}
        canBuy={false}
      />,
    );
    await user.click(toggle());
    expect(toggle()).not.toBeChecked();
    expect(toggle()).toBeDisabled();
    expect(screen.queryByRole("checkbox", { name: AGREE })).toBeNull();
    expect(save.mutate).not.toHaveBeenCalled();
    await user.click(submit());
    expect(save.mutate).toHaveBeenCalledTimes(1);
    expect(save.mutate.mock.calls[0][0]).toEqual(DISABLED_BODY);
  });

  it("never accepts a fresh enabled save when billing is unavailable", async () => {
    const user = userEvent.setup();
    render(<AutoReloadForm summary={SUMMARY} canBuy={false} />);
    expect(agreement()).toBeDisabled();
    expect(agreement()).not.toBeChecked();
    expect(save.mutate).not.toHaveBeenCalled();
    await user.click(submit());
    expect(screen.getByRole("alert")).toHaveTextContent(UNAVAILABLE);
    expect(save.mutate).not.toHaveBeenCalled();
  });

  it("clears agreement on an outage and requires a fresh agreement after restoration", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<AutoReloadForm summary={SUMMARY} canBuy />);
    await user.click(agreement());
    expect(agreement()).toBeChecked();
    rerender(
      <AutoReloadForm
        summary={{ ...SUMMARY, billing_available: false }}
        canBuy={false}
      />,
    );
    expect(agreement()).not.toBeChecked();
    expect(agreement()).toBeDisabled();
    expect(toggle()).toBeEnabled();
    rerender(
      <AutoReloadForm
        summary={{ ...SUMMARY, billing_available: true }}
        canBuy
      />,
    );
    expect(agreement()).not.toBeChecked();
    expect(save.mutate).not.toHaveBeenCalled();
    await user.click(submit());
    expect(screen.getByRole("alert")).toHaveTextContent(NOT_AGREED);
    expect(save.mutate).not.toHaveBeenCalled();
    await user.click(agreement());
    await user.click(submit());
    expect(save.mutate).toHaveBeenCalledTimes(1);
    expect(save.mutate.mock.calls[0][0]).toEqual(ENABLED_BODY);
  });

  it("availability changes do not undo an unsaved revocation or restore its agreement", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<AutoReloadForm summary={SUMMARY} canBuy />);
    await user.click(agreement());
    await user.click(toggle());
    rerender(
      <AutoReloadForm
        summary={{ ...SUMMARY, billing_available: false }}
        canBuy={false}
      />,
    );
    expect(toggle()).not.toBeChecked();
    rerender(
      <AutoReloadForm
        summary={{ ...SUMMARY, billing_available: true }}
        canBuy
      />,
    );
    expect(toggle()).not.toBeChecked();
    expect(save.mutate).not.toHaveBeenCalled();
    await user.click(toggle());
    expect(agreement()).not.toBeChecked();
    await user.click(submit());
    expect(save.mutate).not.toHaveBeenCalled();
    await user.click(agreement());
    await user.click(submit());
    expect(save.mutate.mock.calls[0][0]).toEqual(ENABLED_BODY);
  });

  it("a 503 while revoking is not success, and retry still needs no opt-in", async () => {
    const user = userEvent.setup();
    save.mutate.mockImplementationOnce((_body, options) =>
      options.onError({
        code: "billing_unavailable",
        result: "The managed credit operation could not be completed.",
        statusCode: 503,
      }),
    );
    save.mutate.mockImplementationOnce((_body, options) => options.onSuccess());
    render(<AutoReloadForm summary={SUMMARY} canBuy={false} />);
    await user.click(toggle());
    await user.click(submit());
    expect(screen.getByRole("alert")).toHaveTextContent(
      "The managed credit operation could not be completed.",
    );
    expect(screen.queryByRole("checkbox", { name: AGREE })).toBeNull();
    await user.click(submit());
    expect(save.mutate).toHaveBeenCalledTimes(2);
    expect(save.mutate.mock.calls.map(([body]) => body)).toEqual([
      DISABLED_BODY,
      DISABLED_BODY,
    ]);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("a submitted agreement cannot authorize a second save before settlement", async () => {
    const user = userEvent.setup();
    render(<AutoReloadForm summary={SUMMARY} canBuy />);
    await user.click(agreement());
    await user.click(submit());
    expect(save.mutate).toHaveBeenCalledTimes(1);
    expect(agreement()).not.toBeChecked();
    await user.click(submit());
    expect(save.mutate).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("alert")).toHaveTextContent(NOT_AGREED);
  });

  it("shows the exact submitted charge and threshold rather than rounding at one dollar", async () => {
    const user = userEvent.setup();
    render(
      <AutoReloadForm
        summary={{
          ...SUMMARY,
          auto_reload: {
            enabled: true,
            threshold_usd: "5.005001",
            amount_usd: "30.555001",
          },
        }}
        canBuy
      />,
    );
    expect(
      screen.getByText(
        "Future AGI will charge $30.555001 to your saved payment method whenever your credit balance is at or below $5.005001.",
      ),
    ).toBeInTheDocument();
    await user.click(agreement());
    await user.click(submit());
    expect(save.mutate.mock.calls[0][0]).toEqual({
      ...ENABLED_BODY,
      auto_reload: {
        ...ENABLED_BODY.auto_reload,
        threshold_usd: 5.005001,
        amount_usd: 30.555001,
      },
    });
  });

  it("a summary refresh with changed terms never carries the previous agreement", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<AutoReloadForm summary={SUMMARY} canBuy />);
    await user.click(agreement());
    rerender(
      <AutoReloadForm
        summary={{
          ...SUMMARY,
          auto_reload: { ...SUMMARY.auto_reload, amount_usd: "45.25" },
        }}
        canBuy
      />,
    );
    expect(agreement()).not.toBeChecked();
    expect(
      screen.getByText(
        "Future AGI will charge $45.25 to your saved payment method whenever your credit balance is at or below $5.00.",
      ),
    ).toBeInTheDocument();
    await user.click(submit());
    expect(save.mutate).not.toHaveBeenCalled();
  });

  it("restoration of a server-revoked preference does not enable it or imply agreement", async () => {
    const user = userEvent.setup();
    const disabled = { ...SUMMARY, auto_reload: { enabled: false } };
    const { rerender } = render(
      <AutoReloadForm summary={disabled} canBuy={false} />,
    );
    expect(screen.queryByRole("checkbox", { name: "Auto-reload" })).toBeNull();
    rerender(<AutoReloadForm summary={disabled} canBuy />);
    expect(toggle()).not.toBeChecked();
    expect(save.mutate).not.toHaveBeenCalled();
    await user.click(toggle());
    await user.type(
      screen.getByLabelText("Reload when balance falls below (USD)"),
      "5",
    );
    await user.type(screen.getByLabelText("Reload amount (USD)"), "30");
    expect(agreement()).not.toBeChecked();
    await user.click(submit());
    expect(save.mutate).not.toHaveBeenCalled();
    await user.click(agreement());
    await user.click(submit());
    expect(save.mutate.mock.calls[0][0]).toEqual(ENABLED_BODY);
  });
});
