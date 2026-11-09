import { describe, expect, it } from "vitest";

import { reservedReason, sampleValue, sendSnippet } from "../reservedNames";

describe("names the gateway does not record", () => {
  it("flags the gateway's reserved prefixes, in any case", () => {
    expect(reservedReason("org_tenant")).toMatch(/org_/);
    expect(reservedReason("Key_owner")).toMatch(/key_/);
    expect(reservedReason("budget_team")).toMatch(/budget_/);
    expect(reservedReason("stepper")).toMatch(/step/);
    expect(reservedReason("omega_run")).toMatch(/omega_/);
  });

  it("flags the gateway's own exact names", () => {
    expect(reservedReason("cost")).toMatch(/cost/);
    expect(reservedReason("authorization")).toBeTruthy();
    expect(reservedReason("ocr_pages")).toBeTruthy();
    expect(reservedReason("source_choice")).toBeTruthy();
  });

  it("lets ordinary business names through", () => {
    expect(reservedReason("cost_center")).toBeNull();
    expect(reservedReason("tenant")).toBeNull();
    expect(reservedReason("feature")).toBeNull();
    expect(reservedReason("agent")).toBeNull();
    expect(reservedReason("")).toBeNull();
  });
});

describe("the send example", () => {
  it("uses an allowed value when there is one, else a placeholder by type", () => {
    expect(
      sampleValue({ property_type: "string", allowed_values: ["upsell"] }),
    ).toBe("upsell");
    expect(sampleValue({ name: "tenant", property_type: "string" })).toBe(
      "<tenant>",
    );
    expect(sampleValue({ property_type: "number" })).toBe(1);
    expect(sampleValue({ property_type: "boolean" })).toBe(true);
  });

  it("builds the x-agentcc-metadata header from recorded properties only", () => {
    const snippet = sendSnippet([
      {
        name: "feature",
        property_type: "string",
        allowed_values: ["order-summary"],
      },
      { name: "tenant", property_type: "string", allowed_values: [] },
      { name: "org_unit", property_type: "string", allowed_values: [] },
    ]);
    expect(snippet.header).toBe(
      'x-agentcc-metadata: {"feature":"order-summary","tenant":"<tenant>"}',
    );
    expect(snippet.body).toContain('"metadata": {');
    expect(snippet.body).toContain('"feature": "order-summary"');
    expect(snippet.body).not.toContain("org_unit");
  });
});
