import { serializeFilterListForApi } from "src/api/contracts/filter-contract";

/**
 * Canonical persistence boundary shared by default-tab localStorage and saved
 * views. It removes UI-only row state while translating `registryId` to the
 * API's `property_id`, so primary and compare filters round-trip identically.
 */
export const serializeTraceFiltersForPersistence = (filters) =>
  serializeFilterListForApi(
    (filters || []).map((filter) => ({
      ...filter,
      ...(filter?.property_id || filter?.registryId
        ? { property_id: filter.property_id || filter.registryId }
        : {}),
    })),
  );
