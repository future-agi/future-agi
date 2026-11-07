export function resolveViewedVersion(skill, ref) {
  if (!ref) return { value: skill.latestVersion, number: skill.latestVersion };
  if (ref.startsWith("label:")) {
    const label = ref.slice("label:".length);
    const number = label === "latest" ? skill.latestVersion : skill.labels.find((item) => item.name === label)?.version;
    return { value: ref, number: number || null, error: number ? null : `This skill has no ${label} label. Choose a different label or a version.` };
  }
  const number = Number(ref);
  if (!/^[1-9]\d*$/.test(ref) || !Number.isSafeInteger(number)) {
    return { value: "", number: null, error: "The version in this link is not a valid published version." };
  }
  return { value: number, number };
}

export function formatFetchedAt(value) {
  if (!value) return "Never";
  return new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

// Keep version colors stable when the date filter changes. At most seven recent
// versions plus one explicitly labelled older-version group use the eight-color
// palette. The accompanying table retains every individual version.
export function buildUsageChart(byDay, latestVersion, days, now = new Date()) {
  const end = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  const dates = Array.from({ length: days }, (_, index) => {
    const date = new Date(end);
    date.setUTCDate(date.getUTCDate() - (days - 1 - index));
    return date.toISOString().slice(0, 10);
  });
  const firstRecent = Math.max(1, latestVersion - 6);
  const groups = Array.from({ length: latestVersion - firstRecent + 1 }, (_, index) => ({
    name: `v${firstRecent + index}`,
    version: firstRecent + index,
    colorIndex: index,
  }));
  if (firstRecent > 1) groups.push({ name: `Earlier versions (v1–v${firstRecent - 1})`, version: null, colorIndex: 7 });
  const totals = new Map();
  byDay.forEach(({ day, version, count }) => {
    const group = version < firstRecent ? "earlier" : version;
    const key = `${day}:${group}`;
    totals.set(key, (totals.get(key) || 0) + count);
  });
  return {
    dates,
    series: groups.map((group) => ({
      name: group.name,
      colorIndex: group.colorIndex,
      data: dates.map((date) => totals.get(`${date}:${group.version ?? "earlier"}`) || 0),
    })).filter((group) => group.data.some((count) => count > 0)),
    grouped: byDay.some(({ version }) => version < firstRecent),
  };
}
