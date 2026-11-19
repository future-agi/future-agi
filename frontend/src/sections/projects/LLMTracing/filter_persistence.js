import { serializeFilterListForApi } from "src/api/contracts/filter-contract";
import { serializeFiltersWithSkills } from "./skillFilters";

const serializeCanonical = (filters) =>
  serializeFilterListForApi(
    (filters || []).map((filter) => ({
      ...filter,
      ...(filter?.property_id || filter?.registryId
        ? { property_id: filter.property_id || filter.registryId }
        : {}),
    })),
  );

/**
 * Canonical persistence boundary shared by default-tab localStorage and saved
 * views. It removes UI-only row state while translating `registryId` to the
 * API's `property_id`, so primary and compare filters round-trip identically.
 *
 * `SKILL` items (SKILL-TRACES §6.3) keep their correlation `group`: a second
 * skill ANDed as group 1 must not collapse into group 0 on save, which would
 * ask for one load that matches both skills.
 */
export const serializeTraceFiltersForPersistence = (filters) =>
  serializeFiltersWithSkills(filters || [], serializeCanonical);
