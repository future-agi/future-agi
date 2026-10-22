export const defaultSamplingPolicy = () => ({
  version: 1,
  sampling_unit: "session",
  missing_id: "exclude",
  mode: "closed",
  window: {
    start: new Date(Date.now() - 86400000).toISOString(),
    end: new Date(Date.now() - 1000).toISOString(),
  },
  strata: [],
  group_by: [],
  default: { rate: 10 },
});

export const samplingStratumLabel = (identity) => {
  try {
    const [name, groups] = JSON.parse(identity);
    if (typeof name !== "string" || !Array.isArray(groups)) return identity;
    return [
      name === "default" ? "All remaining contacts" : name,
      ...groups.map((encoded) => {
        const [present, value] = JSON.parse(encoded);
        return present ? String(value) : "(missing)";
      }),
    ].join(" · ");
  } catch {
    return identity;
  }
};
