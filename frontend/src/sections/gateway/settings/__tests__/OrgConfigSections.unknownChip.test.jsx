// A row's chip and its sentence come from one state: when the gateway's
// settings report could not be read, a section it reports on reads
// "Enforcement unknown" in its chip too, never "Partly enforced".
import { describe, expect, it } from "vitest";

import { orgConfigSections } from "../orgConfigSections";

const READ_FAILED = { isLoading: false, isError: true };

describe("Org config sections: chip and sentence agree when enforcement is unknown", () => {
  it("shows Enforcement unknown on the Cache row, whose sentence says so", () => {
    const rows = orgConfigSections(
      { cache: { enabled: true, default_ttl: 300 } },
      READ_FAILED,
    );
    const cache = rows.find((row) => row.id === "cache");
    expect(cache.reasons[0]).toBe(
      "Cache: enforcement unknown. The gateway's settings report couldn't be read.",
    );
    expect(cache.gap).toEqual({
      label: "Enforcement unknown",
      tone: "neutral",
    });
  });
});
