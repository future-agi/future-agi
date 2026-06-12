export const DEFAULT_TIME_PRESET = "30D";

export function buildTimeRangePayload(timePreset, customDateRange) {
  if (timePreset === "custom" && customDateRange) {
    return {
      custom_start: customDateRange[0].toISOString(),
      custom_end: customDateRange[1].toISOString(),
    };
  }
  return {
    preset: timePreset === "custom" ? DEFAULT_TIME_PRESET : timePreset,
  };
}

export function resolveInitialTimeRange(savedTimeRange, urlPreset) {
  const tr = savedTimeRange || {};
  if (!urlPreset && tr.custom_start && tr.custom_end) {
    return {
      timePreset: "custom",
      customDateRange: [new Date(tr.custom_start), new Date(tr.custom_end)],
    };
  }
  return {
    timePreset: urlPreset || tr.preset || DEFAULT_TIME_PRESET,
    customDateRange: null,
  };
}
