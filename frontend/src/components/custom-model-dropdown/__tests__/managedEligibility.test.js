// OK-B (R1-01, R1-06, R2-13, R2-15): the managed-or-BYOK adapter that every
// model picker reads. RED on base 651ab5bb: ../managedEligibility does not
// exist and common.js has no pickerEligibility, so the picker has no way to
// tell a Future AGI managed model from one that needs a provider key.
import { describe, expect, it, vi } from "vitest";

// Keep the real eligibility adapter without importing the application's HTTP
// client (and its unrelated generated-contract initialization).
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: { develop: { modelList: "/model-hub/api/models_list/" } },
}));
import {
  CREDENTIAL_MODE,
  PAYER,
  PAYER_LABELS,
  getModelEligibility,
  isManagedEligible,
  omitContractFields,
  readCredentialMode,
  readPayer,
} from "src/sections/develop-detail/Common/modelAccess";
import { normalizeModelOption, pickerEligibility } from "../common";
import {
  BYOK_PAYER_LABEL,
  MANAGED_PAYER_LABEL,
  byokRow,
  legacyRow,
  managedRow,
  unavailableRow,
} from "./modelListContractFixtures";

describe("getModelEligibility: managed rows", () => {
  it("fails closed on contradictory availability without opening provider setup", () => {
    expect(
      getModelEligibility(managedRow({ is_available: false }), false),
    ).toEqual({
      mode: "managed",
      selectable: false,
      needsProviderSetup: false,
      payer: null,
      payerLabel: null,
    });
    expect(getModelEligibility(managedRow(), true).selectable).toBe(true);
  });

  it("reads the camelCase alias of the contract field", () => {
    const row = {
      model_name: "m",
      credentialMode: "managed",
      payer: "future_agi_credits",
    };
    expect(getModelEligibility(row, false)).toMatchObject({
      mode: "managed",
      selectable: true,
      payerLabel: MANAGED_PAYER_LABEL,
    });
  });
});

describe("getModelEligibility: BYOK and pre-contract rows keep the legacy verdict", () => {
  it.each([true, false, undefined, null, 0, 1, ""])(
    "a pre-contract row follows legacyAvailable=%s exactly",
    (legacy) => {
      const result = getModelEligibility(legacyRow(), legacy);
      expect(result).toEqual({
        mode: null,
        selectable: Boolean(legacy),
        needsProviderSetup: !legacy,
        payer: null,
        payerLabel: null,
      });
    },
  );

  it("a byok row with a stored key is selectable and says the user's key pays", () => {
    expect(getModelEligibility(byokRow(), true)).toEqual({
      mode: "byok",
      selectable: true,
      needsProviderSetup: false,
      payer: "own_provider_key",
      payerLabel: BYOK_PAYER_LABEL,
    });
  });

  it("a byok row the legacy flag marks unavailable still needs provider setup and names no payer", () => {
    expect(
      getModelEligibility(byokRow({ is_available: false }), false),
    ).toEqual({
      mode: "byok",
      selectable: false,
      needsProviderSetup: true,
      payer: null,
      payerLabel: null,
    });
  });

  it("fails closed on an unknown present mode", () => {
    const row = legacyRow({
      credential_mode: "shared",
      payer: "future_agi_credits",
    });
    expect(getModelEligibility(row, true)).toMatchObject({
      mode: null,
      selectable: false,
      payerLabel: null,
    });
    expect(getModelEligibility(row, false).needsProviderSetup).toBe(false);
  });
});

describe("getModelEligibility: unavailable rows fail closed", () => {
  it("never lets an unavailable row be picked, even if the legacy flag says available", () => {
    expect(
      getModelEligibility(unavailableRow({ is_available: true }), true),
    ).toEqual({
      mode: "unavailable",
      selectable: false,
      needsProviderSetup: true,
      payer: null,
      payerLabel: null,
    });
  });
});

describe("payer display", () => {
  it("uses the contract wording", () => {
    expect(PAYER_LABELS[PAYER.FUTURE_AGI_CREDITS]).toBe(MANAGED_PAYER_LABEL);
    expect(PAYER_LABELS[PAYER.OWN_PROVIDER_KEY]).toBe(BYOK_PAYER_LABEL);
  });

  it("hides a payer that contradicts the mode instead of showing the wrong one", () => {
    expect(
      getModelEligibility(managedRow({ payer: "own_provider_key" }), true),
    ).toMatchObject({
      selectable: true,
      payer: null,
      payerLabel: null,
    });
    expect(
      getModelEligibility(byokRow({ payer: "future_agi_credits" }), true),
    ).toMatchObject({
      selectable: true,
      payer: null,
      payerLabel: null,
    });
  });

  it("hides an unknown or missing payer", () => {
    expect(
      getModelEligibility(managedRow({ payer: "someone_else" }), true)
        .payerLabel,
    ).toBeNull();
    expect(
      getModelEligibility(managedRow({ payer: null }), true).payerLabel,
    ).toBeNull();
  });
});

describe("readers and helpers", () => {
  it("recognises exactly the three contract modes", () => {
    expect(Object.values(CREDENTIAL_MODE).sort()).toEqual([
      "byok",
      "managed",
      "unavailable",
    ]);
    expect(readCredentialMode({ credential_mode: "managed" })).toBe("managed");
    expect(readCredentialMode({ credential_mode: "MANAGED" })).toBeNull();
    expect(readCredentialMode(undefined)).toBeNull();
    expect(readPayer({ payer: "own_provider_key" })).toBe("own_provider_key");
    expect(readPayer({ payer: "x" })).toBeNull();
  });

  it("isManagedEligible is true only for managed rows", () => {
    expect(isManagedEligible(managedRow())).toBe(true);
    expect(isManagedEligible(byokRow())).toBe(false);
    expect(isManagedEligible(unavailableRow())).toBe(false);
    expect(isManagedEligible(legacyRow())).toBe(false);
    expect(isManagedEligible(null)).toBe(false);
  });

  it("does not mutate the row and returns only gating/display fields", () => {
    const row = managedRow();
    const before = JSON.stringify(row);
    const result = getModelEligibility(row, false);
    expect(JSON.stringify(row)).toBe(before);
    expect(Object.keys(result).sort()).toEqual([
      "mode",
      "needsProviderSetup",
      "payer",
      "payerLabel",
      "selectable",
    ]);
  });

  it("omitContractFields strips the contract fields and nothing else", () => {
    const row = { ...managedRow(), credentialMode: "managed", id: "r1" };
    const stripped = omitContractFields(row);
    expect(stripped).not.toHaveProperty("credential_mode");
    expect(stripped).not.toHaveProperty("credentialMode");
    expect(stripped).not.toHaveProperty("payer");
    expect(stripped).toMatchObject({
      model_name: "gpt-5.5-mini",
      id: "r1",
      is_available: true,
    });
    expect(row).toHaveProperty("credential_mode", "managed");
    expect(omitContractFields(legacyRow())).toEqual(legacyRow());
  });
});

describe("pickerEligibility (shared picker)", () => {
  it("keeps the pre-contract gate: only the normalized isAvailable counts", () => {
    // ShowModelDropdown used `!option.isAvailable`; a raw row that only has
    // snake_case is_available must still need provider setup, as before.
    expect(
      pickerEligibility({ model_name: "x", is_available: true })
        .needsProviderSetup,
    ).toBe(true);
    expect(
      pickerEligibility(normalizeModelOption(legacyRow({ is_available: true })))
        .selectable,
    ).toBe(true);
    expect(
      pickerEligibility(normalizeModelOption(legacyRow())).needsProviderSetup,
    ).toBe(true);
  });

  it("lets a normalized managed row through without provider setup", () => {
    const option = normalizeModelOption(managedRow());
    expect(pickerEligibility(option)).toMatchObject({
      selectable: true,
      needsProviderSetup: false,
      payerLabel: MANAGED_PAYER_LABEL,
    });
  });

  it("normalizeModelOption adds no contract fields to pre-contract rows", () => {
    const option = normalizeModelOption(legacyRow());
    expect(option).not.toHaveProperty("credential_mode");
    expect(option).not.toHaveProperty("credentialMode");
    expect(option).not.toHaveProperty("payer");
  });
});
