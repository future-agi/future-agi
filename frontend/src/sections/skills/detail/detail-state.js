export function resolveViewedVersion(skill, ref) {
  if (!ref) return { value: skill.latestVersion, number: skill.latestVersion };
  if (ref.startsWith("label:")) {
    const label = ref.slice("label:".length);
    const number =
      label === "latest"
        ? skill.latestVersion
        : skill.labels.find((item) => item.name === label)?.version;
    return {
      value: ref,
      number: number || null,
      error: number
        ? null
        : `This skill has no ${label} label. Choose a different label or a version.`,
    };
  }
  const number = Number(ref);
  if (!/^[1-9]\d*$/.test(ref) || !Number.isSafeInteger(number)) {
    return {
      value: "",
      number: null,
      error: "The version in this link is not a valid published version.",
    };
  }
  return { value: number, number };
}

export function formatFetchedAt(value) {
  if (!value) return "Never";
  return new Date(value).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}
