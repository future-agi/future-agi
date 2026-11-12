import React from "react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";

import AutoReloadForm from "../AutoReloadForm";

const save = vi.hoisted(() => ({ mutate: vi.fn(), isPending: false }));
vi.mock("../api", () => ({ useSaveCreditSettings: () => save }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

beforeEach(() => {
  // Returning the mock would register it as a Vitest cleanup callback.
  save.mutate.mockReset();
});

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

const ENABLED = {
  ...SUMMARY,
  auto_reload: {
    enabled: true,
    threshold_usd: "5.00",
    amount_usd: "30.00",
    disabled_reason: "",
  },
};
// The copy is written out here, not imported, so a change to what the person
// agrees to is a deliberate change to these tests.
const TERMS =
  "I authorize Future AGI to charge my saved payment method for the selected auto-reload amount whenever my credit balance is at or below the selected threshold, subject to configured limits. These charges may occur without me being present. I can stop future auto-reloads by disabling auto-reload in my credit settings; a payment already initiated may still complete.";
const CHARGE =
  "Future AGI will charge $30.00 to your saved payment method whenever your credit balance is at or below $5.00.";
const AGREE = "I agree to the auto-reload authorization above";
const NOT_AGREED =
  "Agree to the auto-reload authorization before saving with auto-reload on.";
const ENABLED_BODY = {
  low_balance_threshold_usd: 5,
  auto_reload: {
    enabled: true,
    consent_version: "auto-reload-v1",
    threshold_usd: 5,
    amount_usd: 30,
  },
};

const agreement = () => screen.getByRole("checkbox", { name: AGREE });
const saveButton = () => screen.getByRole("button", { name: "Save" });
const autoReload = () => screen.getByRole("checkbox", { name: "Auto-reload" });

// The server refuses every save with auto-reload on unless the person agreed
// to the current terms for that save; turning it off needs no agreement.
describe("AutoReloadForm authorization", () => {
  it("with auto-reload on, the versioned terms and the chosen charge are shown before saving", () => {
    render(<AutoReloadForm summary={ENABLED} canBuy />);
    const region = screen.getByRole("region", {
      name: "Low balance and auto-reload",
    });
    expect(
      within(region).getByText("Auto-reload authorization"),
    ).toBeInTheDocument();
    expect(
      within(region).getByText("Terms version auto-reload-v1"),
    ).toBeInTheDocument();
    expect(within(region).getByText(TERMS)).toBeInTheDocument();
    expect(within(region).getByText(CHARGE)).toBeInTheDocument();
    expect(
      within(region).getByRole("checkbox", { name: AGREE }),
    ).not.toBeChecked();
    expect(
      within(region).getAllByRole("checkbox", { name: "Auto-reload" }),
    ).toHaveLength(1);
    expect(screen.queryAllByRole("alert")).toHaveLength(0);
  });

  it("an enabled save without agreement is refused locally", async () => {
    const user = userEvent.setup();
    render(<AutoReloadForm summary={ENABLED} canBuy />);
    await user.click(saveButton());
    expect(screen.getByRole("alert").textContent).toBe(NOT_AGREED);
    expect(save.mutate).not.toHaveBeenCalled();
  });

  it("after agreement the save sends consent_version and numeric values", async () => {
    const user = userEvent.setup();
    render(<AutoReloadForm summary={ENABLED} canBuy />);
    await user.click(agreement());
    expect(agreement()).toBeChecked();
    await user.click(saveButton());
    expect(save.mutate).toHaveBeenCalledTimes(1);
    expect(save.mutate.mock.calls[0][0]).toEqual(ENABLED_BODY);
  });

  it("changing the threshold or amount after agreeing clears the agreement", async () => {
    const user = userEvent.setup();
    render(<AutoReloadForm summary={ENABLED} canBuy />);
    await user.click(agreement());
    const amount = screen.getByLabelText("Reload amount (USD)");
    await user.clear(amount);
    await user.type(amount, "40");
    expect(agreement()).not.toBeChecked();
    await user.click(saveButton());
    expect(screen.getByRole("alert").textContent).toBe(NOT_AGREED);
    expect(save.mutate).not.toHaveBeenCalled();

    await user.click(agreement());
    const threshold = screen.getByLabelText(
      "Reload when balance falls below (USD)",
    );
    await user.clear(threshold);
    await user.type(threshold, "6");
    expect(agreement()).not.toBeChecked();
    await user.click(saveButton());
    expect(save.mutate).not.toHaveBeenCalled();
  });

  it("turning auto-reload off and on again needs a new agreement", async () => {
    const user = userEvent.setup();
    render(<AutoReloadForm summary={ENABLED} canBuy />);
    await user.click(agreement());
    await user.click(autoReload());
    await user.click(autoReload());
    expect(autoReload()).toBeChecked();
    expect(agreement()).not.toBeChecked();
    await user.click(saveButton());
    expect(screen.getByRole("alert").textContent).toBe(NOT_AGREED);
    expect(save.mutate).not.toHaveBeenCalled();
  });

  it("a low-balance-only change while auto-reload is on still needs agreement", async () => {
    const user = userEvent.setup();
    render(<AutoReloadForm summary={ENABLED} canBuy />);
    const lowBalance = screen.getByLabelText(
      "Alert when balance falls below (USD)",
    );
    await user.clear(lowBalance);
    await user.type(lowBalance, "7");
    await user.click(saveButton());
    expect(screen.getByRole("alert").textContent).toBe(NOT_AGREED);
    expect(save.mutate).not.toHaveBeenCalled();

    await user.click(agreement());
    await user.click(saveButton());
    expect(save.mutate).toHaveBeenCalledTimes(1);
    expect(save.mutate.mock.calls[0][0]).toEqual({
      ...ENABLED_BODY,
      low_balance_threshold_usd: 7,
    });
  });

  it("the agreement clears after every settled save", async () => {
    const user = userEvent.setup();
    save.mutate.mockImplementation((_body, options) => options.onSuccess());
    render(<AutoReloadForm summary={ENABLED} canBuy />);
    await user.click(agreement());
    await user.click(saveButton());
    expect(save.mutate).toHaveBeenCalledTimes(1);
    expect(agreement()).not.toBeChecked();

    save.mutate.mockImplementation((_body, options) =>
      options.onError({
        code: "billing_unavailable",
        result: "The managed credit operation could not be completed.",
        statusCode: 503,
      }),
    );
    await user.click(agreement());
    await user.click(saveButton());
    expect(save.mutate).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("alert").textContent).toBe(
      "The managed credit operation could not be completed.",
    );
    expect(agreement()).not.toBeChecked();
  });

  it("an outdated-terms refusal says so plainly with reload guidance", async () => {
    const user = userEvent.setup();
    save.mutate.mockImplementation((_body, options) =>
      options.onError({
        status: false,
        code: "auto_reload_consent_outdated",
        message:
          "Auto-reload terms changed. Review and acknowledge the current version.",
        result:
          "Auto-reload terms changed. Review and acknowledge the current version.",
        statusCode: 400,
      }),
    );
    render(<AutoReloadForm summary={ENABLED} canBuy />);
    await user.click(agreement());
    await user.click(saveButton());
    expect(screen.getByRole("alert").textContent).toBe(
      "Auto-reload terms changed. Review and acknowledge the current version. Reload this page to see the current terms.",
    );
    expect(agreement()).not.toBeChecked();
  });

  it("turning auto-reload off saves without consent or agreement", async () => {
    const user = userEvent.setup();
    render(<AutoReloadForm summary={ENABLED} canBuy />);
    await user.click(autoReload());
    expect(screen.queryByRole("checkbox", { name: AGREE })).toBeNull();
    expect(screen.queryByText("Auto-reload authorization")).toBeNull();
    await user.click(saveButton());
    expect(save.mutate).toHaveBeenCalledTimes(1);
    const body = save.mutate.mock.calls[0][0];
    expect(body).toEqual({
      low_balance_threshold_usd: 5,
      auto_reload: { enabled: false, threshold_usd: null, amount_usd: null },
    });
    expect(body.auto_reload).not.toHaveProperty("consent_version");
  });

  it("server-enabled auto-reload can be revoked without agreement where purchases are unavailable", async () => {
    const user = userEvent.setup();
    render(<AutoReloadForm summary={ENABLED} canBuy={false} />);
    const region = screen.getByRole("region", {
      name: "Low balance and auto-reload",
    });
    expect(within(region).getByText(TERMS)).toBeInTheDocument();
    expect(within(region).getByText(CHARGE)).toBeInTheDocument();
    expect(agreement()).toBeDisabled();
    await user.click(autoReload());
    await user.click(saveButton());
    expect(save.mutate).toHaveBeenCalledTimes(1);
    expect(save.mutate.mock.calls[0][0]).toEqual({
      low_balance_threshold_usd: 5,
      auto_reload: { enabled: false, threshold_usd: null, amount_usd: null },
    });
  });

  it("the agreement cannot be given until both values are set", async () => {
    const user = userEvent.setup();
    render(<AutoReloadForm summary={SUMMARY} canBuy />);
    await user.click(autoReload());
    expect(
      screen.getByText(
        "Set the reload threshold and amount to see the charge you authorize.",
      ),
    ).toBeInTheDocument();
    expect(agreement()).toBeDisabled();
    await user.type(
      screen.getByLabelText("Reload when balance falls below (USD)"),
      "5",
    );
    expect(agreement()).toBeDisabled();
    await user.type(screen.getByLabelText("Reload amount (USD)"), "30");
    expect(agreement()).toBeEnabled();
    expect(screen.getByText(CHARGE)).toBeInTheDocument();
    expect(
      screen.queryByText(
        "Set the reload threshold and amount to see the charge you authorize.",
      ),
    ).toBeNull();
  });
});

// This release runs no automatic reload, and the server says so for every
// organization: disabled_reason "auto_reload_unavailable".
const UNAVAILABLE = "Automatic reloads are not available in this release.";
const NOT_RUNNING = `Auto-reload is on in your settings but is not running. ${UNAVAILABLE}`;
const TURN_OFF_TO_SAVE = `${UNAVAILABLE} Turn off auto-reload to save your other settings.`;
const OFF_UNAVAILABLE = {
  ...SUMMARY,
  auto_reload: {
    ...SUMMARY.auto_reload,
    disabled_reason: "auto_reload_unavailable",
  },
};
const ON_UNAVAILABLE = {
  ...ENABLED,
  auto_reload: {
    ...ENABLED.auto_reload,
    disabled_reason: "auto_reload_unavailable",
  },
};

describe("AutoReloadForm when automatic reloads are not available", () => {
  it.each([
    ["where credit can be bought", true],
    ["where credit cannot be bought", false],
  ])(
    "keeps the switch visible and disabled with the sentence beside it, %s",
    (_where, canBuy) => {
      render(<AutoReloadForm summary={OFF_UNAVAILABLE} canBuy={canBuy} />);
      const region = screen.getByRole("region", {
        name: "Low balance and auto-reload",
      });
      const control = within(region).getByRole("checkbox", {
        name: "Auto-reload",
      });
      expect(control).toBeDisabled();
      expect(control).not.toBeChecked();
      expect(control).toHaveAccessibleDescription(UNAVAILABLE);
      expect(within(region).getByText(UNAVAILABLE)).toBeInTheDocument();
      expect(screen.queryByText(/will charge/i)).toBeNull();
      expect(screen.queryByText(/payment method|card/i)).toBeNull();
      expect(screen.queryByText(/auto_reload_unavailable/)).toBeNull();
      expect(screen.queryByText(/was turned off/i)).toBeNull();
      expect(screen.queryByRole("checkbox", { name: AGREE })).toBeNull();
    },
  );

  it("an organization whose saved setting is on is told it is not running, and offered no agreement", () => {
    render(<AutoReloadForm summary={ON_UNAVAILABLE} canBuy />);
    const region = screen.getByRole("region", {
      name: "Low balance and auto-reload",
    });
    const control = within(region).getByRole("checkbox", {
      name: "Auto-reload",
    });
    expect(control).toBeChecked();
    expect(control).toHaveAccessibleDescription(NOT_RUNNING);
    expect(within(region).getByText(NOT_RUNNING)).toBeInTheDocument();
    expect(screen.queryByText(CHARGE)).toBeNull();
    expect(screen.queryByText(TERMS)).toBeNull();
    expect(screen.queryByText("Auto-reload authorization")).toBeNull();
    expect(screen.queryByRole("checkbox", { name: AGREE })).toBeNull();
    expect(screen.queryByText(/payment method|card/i)).toBeNull();
    expect(screen.queryByText(/auto_reload_unavailable/)).toBeNull();
  });

  it("a saved-on setting can still be turned off, not back on, and saved without agreement", async () => {
    const user = userEvent.setup();
    render(<AutoReloadForm summary={ON_UNAVAILABLE} canBuy />);
    await user.click(autoReload());
    expect(autoReload()).not.toBeChecked();
    expect(autoReload()).toBeDisabled();
    await user.click(saveButton());
    expect(save.mutate).toHaveBeenCalledTimes(1);
    const body = save.mutate.mock.calls[0][0];
    expect(body).toEqual({
      low_balance_threshold_usd: 5,
      auto_reload: { enabled: false, threshold_usd: null, amount_usd: null },
    });
    expect(body.auto_reload).not.toHaveProperty("consent_version");
  });

  it("saving while the saved-on setting is still on is refused here with the sentence", async () => {
    const user = userEvent.setup();
    render(<AutoReloadForm summary={ON_UNAVAILABLE} canBuy />);
    await user.click(saveButton());
    expect(screen.getByRole("alert").textContent).toBe(TURN_OFF_TO_SAVE);
    expect(save.mutate).not.toHaveBeenCalled();
  });
});
