export const formatCompact = (value) => {
  if (value === -1) return "Unlimited";
  if (value >= 1000000) return `${Number(value / 1000000).toPrecision(3)}M`;
  if (value >= 10000) return `${Number(value / 1000).toPrecision(3)}K`;
  return value?.toLocaleString?.() ?? value;
};

// Plan entitlements whose capability is not built (ruling 383): SCIM
// provisioning has no endpoint, and no role or permission is scoped to a
// project. The backend's plans still carry the flags, so no plan copy may list
// them as something a plan includes. Both spellings, since some client-side
// objects carry a camelCase alias next to the snake_case key (canonicalEntries
// in src/utils/utils.js).
const NOT_BUILT_PLAN_FEATURES = new Set([
  "has_scim",
  "hasScim",
  "has_project_rbac",
  "hasProjectRbac",
]);

export const isNotBuiltPlanFeature = (key) => NOT_BUILT_PLAN_FEATURES.has(key);

export const formatFeatureLabel = (key) =>
  key
    .replace(/^has_/, "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());

export const formatFeatures = (features) => {
  if (Array.isArray(features)) return features;
  if (!features) return [];

  return Object.entries(features)
    .filter(([key]) => !isNotBuiltPlanFeature(key))
    .filter(([, value]) => value === true || value === -1 || value > 0)
    .map(([key, value]) => {
      const label = formatFeatureLabel(key);
      if (typeof value === "number") return `${label}: ${formatCompact(value)}`;
      return label;
    })
    .slice(0, 8);
};
