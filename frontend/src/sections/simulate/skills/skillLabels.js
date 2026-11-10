export const UNSUPPORTED_SOURCES = new Set(["provider", "phone", "livekit"]);

export const VERDICTS = {
  helps: { label: "Helps", status: "passed" },
  helps_with_regression: { label: "Helps, with a regression", status: "flaky" },
  no_improvement: { label: "No improvement", status: "stopped" },
  not_used: { label: "Not used", status: "needs_review" },
  inconclusive: { label: "Inconclusive", status: "unmeasured" },
};

export const testBadge = (state, verdict) => {
  if (state === "running") return { status: "running", label: "Testing" };
  if (state === "failed") return { status: "failed", label: "Test failed" };
  return VERDICTS[verdict] || { status: "not_run", label: "Not tested" };
};

export const tagVersion = (skill, name) =>
  (skill?.tags || []).find((tag) => tag.name === name)?.version ?? null;

export const refLabel = (attached) =>
  attached.tag
    ? `${attached.tag}${attached.resolved_version ? ` (v${attached.resolved_version})` : " (no version yet)"}`
    : `v${attached.pinned_version}`;

export const loadedLabel = (load) => {
  if (!load?.total) return "Not reported yet";
  const seen = load.observed ? "Observed" : "Unknown";
  return load.observed
    ? `${seen} · loaded ${load.loaded_in}/${load.total}`
    : seen;
};

export const runSkillsLine = (skills = []) =>
  skills
    .filter((skill) => skill.kind === "skill")
    .map(
      (skill) =>
        `${skill.name} v${skill.version_number} · ${loadedLabel(skill)}`,
    )
    .join(", ");

const TEXT_FILE =
  /\.(md|markdown|txt|json|ya?ml|csv|py|js|ts|sh|toml|ini|cfg|html|xml)$/i;

export const readFolder = async (fileList) => {
  const entries = Array.from(fileList || []);
  const prefix = entries[0]?.webkitRelativePath?.split("/")[0];
  const files = await Promise.all(
    entries.map(async (file) => {
      const path = prefix
        ? file.webkitRelativePath.slice(prefix.length + 1)
        : file.name;
      if (!TEXT_FILE.test(path)) return { path, unreadable: true };
      return { path, content: await file.text() };
    }),
  );
  return {
    folder: prefix || "",
    files: files.filter((file) => !file.unreadable),
    skipped: files.filter((file) => file.unreadable).map((file) => file.path),
  };
};

export const skillTemplate = (name, description) =>
  `---\nname: ${name}\ndescription: ${description}\n---\n\n`;
