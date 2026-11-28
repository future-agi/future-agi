// Keep the seven newest versions in fixed slots across date-range changes.
// Older versions share one explicitly named series, never recycled colours;
// the daily table retains every version's individual counts.
export function buildUsageChart(byDay, latestVersion, days, now = new Date()) {
  const firstVersion = Math.max(1, latestVersion - 6);
  const visibleVersions = Array.from(
    { length: latestVersion - firstVersion + 1 },
    (_, index) => firstVersion + index,
  );
  const end = Date.UTC(
    now.getUTCFullYear(),
    now.getUTCMonth(),
    now.getUTCDate(),
  );
  const dates = Array.from({ length: days }, (_, index) =>
    new Date(end - (days - index - 1) * 86400000).toISOString().slice(0, 10),
  );
  const counts = new Map();
  byDay.forEach(({ day, version, count }) => {
    const key = `${day}:${version < firstVersion ? "earlier" : version}`;
    counts.set(key, (counts.get(key) || 0) + count);
  });
  const series = visibleVersions.map((version, colorIndex) => ({
    name: `v${version}`,
    colorIndex,
    data: dates.map((day) => counts.get(`${day}:${version}`) || 0),
  }));
  if (firstVersion > 1)
    series.push({
      name: `Earlier versions (v1–v${firstVersion - 1})`,
      colorIndex: 7,
      data: dates.map((day) => counts.get(`${day}:earlier`) || 0),
    });
  const activeSeries = series.filter((item) =>
    item.data.some((count) => count > 0),
  );
  return {
    dates,
    series: activeSeries,
    grouped: activeSeries.some((item) => item.colorIndex === 7),
  };
}
