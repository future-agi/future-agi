import { describe, expect, it } from "vitest";

import { hydrateStoredFilterList } from "src/api/contracts/filter-contract";
import { serializeTraceFiltersForPersistence } from "../filter_persistence";

describe("trace filter persistence", () => {
  it.each(["primary", "compare"])(
    "preserves registry identity for %s filters",
    () => {
      const stored = serializeTraceFiltersForPersistence([
        {
          id: "ui-row",
          registryId: "custom_attribute:model",
          column_id: "model",
          _meta: { parentProperty: "Attribute" },
          filter_config: {
            col_type: "SPAN_ATTRIBUTE",
            filter_type: "text",
            filter_op: "equals",
            filter_value: "tenant-model",
          },
        },
      ]);

      expect(stored).toEqual([
        {
          column_id: "model",
          property_id: "custom_attribute:model",
          filter_config: {
            col_type: "SPAN_ATTRIBUTE",
            filter_type: "text",
            filter_op: "equals",
            filter_value: "tenant-model",
          },
        },
      ]);
      expect(hydrateStoredFilterList(stored)).toEqual(stored);
    },
  );
});
