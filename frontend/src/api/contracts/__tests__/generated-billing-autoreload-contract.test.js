import { describe, expect, it } from "vitest";

import { UsageUpdateAutoReloadSettingsCreateBody } from "src/generated/api-contracts/api.zod";

const settings = {
  autoreload_enabled: false,
  autoreload_walletamount: "41.00",
  autoreload_walletthreshold: "7.00",
};

describe("generated EE auto-reload request contract", () => {
  it("defaults consent and charge-now to false for legacy settings", () => {
    expect(UsageUpdateAutoReloadSettingsCreateBody.parse(settings)).toEqual({
      ...settings,
      autoreload_consent: false,
      autoreload_charge_now: false,
    });
  });

  it.each([null, "50.00"])("retains explicit cap %s and opt-ins", (cap) => {
    const payload = {
      ...settings,
      autoreload_enabled: true,
      autoreload_maxcharge: cap,
      autoreload_consent: true,
      autoreload_charge_now: true,
    };
    expect(UsageUpdateAutoReloadSettingsCreateBody.parse(payload)).toEqual(
      payload,
    );
  });

  it.each([
    ["autoreload_maxcharge", 50],
    ["autoreload_consent", "true"],
    ["autoreload_charge_now", "false"],
  ])("rejects the wrong type for %s", (field, value) => {
    const result = UsageUpdateAutoReloadSettingsCreateBody.safeParse({
      ...settings,
      [field]: value,
    });
    expect(result.success).toBe(false);
    expect(result.error.issues[0].path).toEqual([field]);
  });

  it.each(Object.keys(settings))("still requires legacy field %s", (field) => {
    const payload = { ...settings };
    delete payload[field];
    const result = UsageUpdateAutoReloadSettingsCreateBody.safeParse(payload);
    expect(result.success).toBe(false);
    expect(result.error.issues[0].path).toEqual([field]);
  });
});
