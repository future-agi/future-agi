import { describe, expect, it } from "vitest";

import { orgConfigSections } from "../orgConfigSections";
import {
  batchPipelineNote,
  notEnforcedSectionsText,
  enforcedSectionsText,
} from "../gatewayReport";
import { sectionEnforcement } from "../sectionEnforcement";
import { hiddenQuickFilters } from "../../logs/filterChips";
import { offeredWebhookEvents } from "../../webhooks/webhookEvents";

const off = {
  isLoading: false,
  data: {
    guardrail_pipeline: {
      enabled: false,
      fail_open: false,
      mode: "sequential",
    },
    org_sections_enforced: ["alerting", "budgets", "ip_acl", "tool_policy"],
    org_sections_not_enforced: {
      ip_acl: "client IP unknown (trusted_proxies not set)",
    },
  },
};
const on = {
  isLoading: false,
  data: {
    guardrail_pipeline: { enabled: true, fail_open: false, mode: "sequential" },
    org_sections_enforced: ["alerting", "budgets", "cache", "ip_acl"],
  },
};

describe("What the console says runs, from the gateway's own config", () => {
  it.each([
    { cache: { enabled: false } },
    { cache: { enabled: false }, org_sections_enforced: ["cache"] },
    {
      org_sections_enforced: ["cache"],
      org_sections_not_enforced: { cache: "node disabled" },
    },
  ])("negative cache reports override positive or absent lists: %j", (data) => {
    expect(sectionEnforcement({ data }, "cache")).toBe("not_enforced");
    expect([...hiddenQuickFilters({ data })]).toContain("cache");
    expect(batchPipelineNote(data)).not.toContain("caching");
    expect(notEnforcedSectionsText(data)).toContain("cache:");
    expect(enforcedSectionsText(data)).not.toMatch(/\bcache\b/);
    expect(sectionEnforcement({ data, isLoading: true }, "cache")).toBeNull();
    expect([...hiddenQuickFilters({ data, isLoading: true })]).toEqual([]);
  });

  it.each([[[]], [["guardrails"]], [["routing"]]])(
    "includes the enabled pipeline exactly once: %j",
    (sections) => {
      const text = enforcedSectionsText({
        org_sections_enforced: sections,
        guardrail_pipeline: { enabled: true },
      });
      expect(
        text.split(", ").filter((entry) => entry === "guardrails"),
      ).toHaveLength(1);
    },
  );
  it("never calls Guardrails partly enforced while guardrails are off", () => {
    const row = orgConfigSections(
      { guardrails: { rules: [{ name: "x" }] } },
      off,
    ).find((r) => r.id === "guardrails");
    expect(row.gap.label).toBe("Not enforced on this gateway");
    expect(row.reasons[0]).toBe(
      "Guardrails are off on this gateway, so no check runs. Rules you save are stored and run when guardrails are turned on in the gateway config.",
    );
  });

  it("keeps the Guardrails row as it was while guardrails are on", () => {
    const row = orgConfigSections({ guardrails: {} }, on).find(
      (r) => r.id === "guardrails",
    );
    expect(row.gap.label).toBe("Partly enforced");
  });

  it("lists guardrails and cache under Not enforced when the gateway runs neither", () => {
    const text = notEnforcedSectionsText(off.data);
    expect(text).toContain(
      "ip_acl: client IP unknown (trusted_proxies not set)",
    );
    expect(text).toContain("guardrails: guardrails are off on this gateway");
    expect(text).toContain("cache: response caching is off on this gateway");
    expect(notEnforcedSectionsText(on.data)).toBeNull();
  });

  it("names in the Batch note only what this gateway applies", () => {
    expect(batchPipelineNote(off.data)).toBe(
      "Each request is sent through the gateway pipeline (routing) independently. Results include per-request cost and token usage.",
    );
    expect(batchPipelineNote(on.data)).toBe(
      "Each request is sent through the gateway pipeline (routing, guardrails, caching) independently. Results include per-request cost and token usage.",
    );
  });

  it("hides the Guardrails and Cache hits quick filters while those features are off", () => {
    expect([...hiddenQuickFilters(off)].sort()).toEqual([
      "cache",
      "guardrails",
    ]);
    expect([...hiddenQuickFilters(on)]).toEqual([]);
    expect([...hiddenQuickFilters({ isLoading: true })]).toEqual([]);
  });

  it("917: always offers the Guardrail triggered event, including while off", () => {
    const values = (list) => list.map((e) => e.value);
    expect(values(offeredWebhookEvents({ guardrailsOff: true }))).toContain(
      "guardrail.triggered",
    );
    expect(values(offeredWebhookEvents({ guardrailsOff: false }))).toContain(
      "guardrail.triggered",
    );
  });
});
