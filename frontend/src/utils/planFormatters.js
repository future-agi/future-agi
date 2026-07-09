export const formatCompact = (value) => {
  if (value === -1) return "Unlimited";
  if (value >= 1000000) return `${Number(value / 1000000).toPrecision(3)}M`;
  if (value >= 10000) return `${Number(value / 1000).toPrecision(3)}K`;
  return value?.toLocaleString?.() ?? value;
};

export const formatFeatureLabel = (key) =>
  key
    .replace(/^has_/, "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());

export const formatFeatures = (features) => {
  if (Array.isArray(features)) return features;
  if (!features) return [];

  return Object.entries(features)
    .filter(([, value]) => value === true || value === -1 || value > 0)
    .map(([key, value]) => {
      const label = formatFeatureLabel(key);
      if (typeof value === "number") return `${label}: ${formatCompact(value)}`;
      return label;
    })
    .slice(0, 8);
};
