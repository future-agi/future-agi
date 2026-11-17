import React from "react";
import PropTypes from "prop-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { validateContractedRequestConfig } from "src/api/contracts/openapi-contract";
import { OPENAPI_CONTRACT } from "src/api/contracts/openapi-contract.generated";

const { put } = vi.hoisted(() => ({ put: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { put },
  endpoints: {
    gateway: { managedCreditSettings: "/agentcc/managed-credits/settings/" },
  },
}));

import { useSaveCreditSettings } from "../api";
import {
  AUTO_RELOAD_CONSENT_TERMS,
  AUTO_RELOAD_CONSENT_VERSION,
  CONSENT_REFUSAL_CODES,
  chosenChargeText,
  creditSettingsBody,
  settingsErrorText,
} from "../autoReloadConsent";

const SETTINGS = "/agentcc/managed-credits/settings/";
const contract = (data) =>
  validateContractedRequestConfig({ url: SETTINGS, method: "put", data });

// The server (agentcc/auto_reload_terms.py) refuses every save with
// auto-reload on unless it carries the current terms version, which the
// console sends only after the person agrees to the terms it shows.
describe("auto-reload consent", () => {
  it("the terms and version match the generated settings contract", () => {
    const consent =
      OPENAPI_CONTRACT.definitions.ManagedCreditAutoReloadRequest?.properties
        ?.consent_version;
    expect(
      consent,
      "Regenerate the shared managed-credit OpenAPI contract.",
    ).toBeDefined();
    expect(AUTO_RELOAD_CONSENT_VERSION).toBe("auto-reload-v1");
    expect(
      consent.description.startsWith(
        `Send exactly ${AUTO_RELOAD_CONSENT_VERSION} after acknowledging: ${AUTO_RELOAD_CONSENT_TERMS} Required on EVERY enabled settings save`,
      ),
    ).toBe(true);
    expect(consent.maxLength).toBeGreaterThanOrEqual(
      AUTO_RELOAD_CONSENT_VERSION.length,
    );
  });

  it.each([
    ["5.005", "30.555", "$5.005", "$30.555"],
    ["1.000001", "30.000001", "$1.000001", "$30.000001"],
    ["1e-6", "3e1", "$0.000001", "$30.00"],
    ["0", "1000000000", "$0.00", "$1000000000.00"],
    ["5", "30.5555555", "$5.00", "$30.5555555"],
  ])(
    "shows the exact numeric charge for threshold %s and amount %s",
    (threshold, amount, thresholdText, amountText) => {
      expect(chosenChargeText(threshold, amount)).toBe(
        `Future AGI will charge ${amountText} to your saved payment method whenever your credit balance is at or below ${thresholdText}.`,
      );
    },
  );

  it("an enabled body carries the acknowledged version and numeric USD and satisfies the PUT contract", () => {
    const body = creditSettingsBody({
      lowBalance: "",
      enabled: true,
      threshold: "5",
      amount: "30",
      consentVersion: "auto-reload-v1",
    });
    expect(body).toEqual({
      low_balance_threshold_usd: null,
      auto_reload: {
        enabled: true,
        consent_version: "auto-reload-v1",
        threshold_usd: 5,
        amount_usd: 30,
      },
    });
    const checked = contract(body);
    expect(checked.ok, checked.error?.message).toBe(true);
    expect(checked.skipped).toBeUndefined();

    const withAlert = creditSettingsBody({
      lowBalance: "2.5",
      enabled: true,
      threshold: "5",
      amount: "30",
      consentVersion: "auto-reload-v1",
    });
    expect(withAlert.low_balance_threshold_usd).toBe(2.5);
    expect(contract(withAlert).ok).toBe(true);
  });

  it("an enabled body is never built without the current acknowledged version", () => {
    [undefined, null, "", "auto-reload-v0"].forEach((consentVersion) => {
      expect(() =>
        creditSettingsBody({
          lowBalance: "",
          enabled: true,
          threshold: "5",
          amount: "30",
          consentVersion,
        }),
      ).toThrow("auto-reload consent not acknowledged");
    });
  });

  it("a disabled body never carries consent_version and satisfies the contract", () => {
    const body = creditSettingsBody({
      lowBalance: "5",
      enabled: false,
      threshold: "5",
      amount: "30",
      consentVersion: "auto-reload-v1",
    });
    expect(body).toEqual({
      low_balance_threshold_usd: 5,
      auto_reload: { enabled: false, threshold_usd: null, amount_usd: null },
    });
    expect(body.auto_reload).not.toHaveProperty("consent_version");
    const checked = contract(body);
    expect(checked.ok, checked.error?.message).toBe(true);
  });

  it("the base string body is refused by the request contract", () => {
    expect(
      contract({
        low_balance_threshold_usd: null,
        auto_reload: {
          enabled: true,
          consent_version: "auto-reload-v1",
          threshold_usd: "5",
          amount_usd: "30",
        },
      }).ok,
    ).toBe(false);
  });

  it("non-finite amounts are refused before any request", () => {
    const enabled = {
      lowBalance: "",
      enabled: true,
      threshold: "5",
      amount: "30",
      consentVersion: "auto-reload-v1",
    };
    expect(() => creditSettingsBody({ ...enabled, threshold: "abc" })).toThrow(
      RangeError,
    );
    expect(() => creditSettingsBody({ ...enabled, amount: "NaN" })).toThrow(
      RangeError,
    );
  });

  it("settings errors read plainly", () => {
    expect(
      settingsErrorText({
        code: "auto_reload_consent_required",
        result:
          "Enabling auto-reload requires acknowledgement of the current terms.",
      }),
    ).toBe(
      "Enabling auto-reload requires acknowledgement of the current terms.",
    );
    expect(
      settingsErrorText({
        code: "auto_reload_consent_outdated",
        result:
          "Auto-reload terms changed. Review and acknowledge the current version.",
      }),
    ).toBe(
      "Auto-reload terms changed. Review and acknowledge the current version. Reload this page to see the current terms.",
    );
    expect(
      settingsErrorText({
        code: "auto_reload_consent_actor_required",
        result: "An authenticated user must acknowledge the auto-reload terms.",
      }),
    ).toBe("An authenticated user must acknowledge the auto-reload terms.");
    expect(settingsErrorText({ result: { message: "X" } })).toBe("X");
    expect(
      settingsErrorText({
        message:
          "request body contract validation failed for PUT /agentcc/managed-credits/settings/",
      }),
    ).toBe(
      "request body contract validation failed for PUT /agentcc/managed-credits/settings/",
    );
    expect(
      settingsErrorText({
        code: "billing_unavailable",
        result: "The managed credit operation could not be completed.",
        statusCode: 503,
      }),
    ).toBe("The managed credit operation could not be completed.");
    expect(settingsErrorText({})).toBe(
      "The settings were not saved. Try again.",
    );
    expect(CONSENT_REFUSAL_CODES).toEqual([
      "auto_reload_consent_required",
      "auto_reload_consent_outdated",
      "auto_reload_consent_actor_required",
    ]);
  });
});

function Providers({ children }) {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
Providers.propTypes = { children: PropTypes.node };

// A guard, not a fix: the save hook forwards the form's body as it is, so an
// agreement can only come from the form, never be added on the way out.
describe("useSaveCreditSettings", () => {
  it("GUARD: useSaveCreditSettings forwards the body unchanged and never adds consent", async () => {
    put.mockReset();
    put.mockResolvedValue({ data: { status: true, result: {} } });
    const { result } = renderHook(() => useSaveCreditSettings(), {
      wrapper: Providers,
    });
    const disabled = {
      low_balance_threshold_usd: null,
      auto_reload: { enabled: false, threshold_usd: null, amount_usd: null },
    };
    const enabledWithoutConsent = {
      low_balance_threshold_usd: null,
      auto_reload: { enabled: true, threshold_usd: 5, amount_usd: 30 },
    };
    await act(() => result.current.mutateAsync(disabled));
    await act(() => result.current.mutateAsync(enabledWithoutConsent));
    expect(put).toHaveBeenCalledTimes(2);
    expect(put.mock.calls[0][0]).toBe(SETTINGS);
    expect(put.mock.calls[0][1]).toBe(disabled);
    expect(put.mock.calls[1][0]).toBe(SETTINGS);
    expect(put.mock.calls[1][1]).toBe(enabledWithoutConsent);
    expect(put.mock.calls[1][1].auto_reload).not.toHaveProperty(
      "consent_version",
    );
  });
});
