// BP activation-001 expectations copied from the approved A rows.
// Runtime execution of these expectations belongs to the owed cloud Vitest run.
export const bpText = {
  "A1-01": "Recorded version",
  "A1-02":
    '`Recorded ${typeof v === "number" || !String(v).startsWith("v") ? `v${v}` : String(v)}`',
  "A1-03": "Recorded version",
  "A1-04":
    '`Recorded ${typeof row.version === "number" || !String(row.version).startsWith("v") ? `v${row.version}` : String(row.version)}`',
  "A1-05": "Saved eval version",
  "A1-06": "No version selected",
  "A1-07":
    '{`${latestPinShown ? "Latest" : "Saved latest"} (V${versions[0]?.version_number ?? ""})`}',
  "A1-08": "V{v.version_number}",
  "A1-09": " (default)",
  "A1-10": "<VersionBadge version={`Version label: ${shownVersion}`} />",
  "A1-11": "Recorded default",
  "A2-01":
    "The version label does not confirm that all saved settings are used at runtime.",
  "A2-02":
    "The version label does not confirm that all saved settings are used at runtime.",
  "A2-03":
    "The version label does not confirm that all saved settings are used at runtime.",
  "A2-04":
    "The version label does not confirm that all saved settings are used at runtime.",
  "A2-05":
    "The version label does not confirm that all saved settings are used at runtime.",
  "A2-06":
    "The version label does not confirm that all saved settings are used at runtime.",
  "A3-01": "` · recorded v${child.pinned_version_number}`",
  "A3-02":
    "A child can use saved version configuration, current template settings and child overrides.",
  "A4-01": "Submits feedback without creating a new eval version.",
  "A4-02":
    "Submits feedback and requests recalculation for this source; it does not create a new eval version.",
};

export const bpRendered = {
  usage: (v) =>
    `Recorded ${typeof v === "number" || !String(v).startsWith("v") ? `v${v}` : String(v)}`,
  detail: (row) =>
    `Recorded ${typeof row.version === "number" || !String(row.version).startsWith("v") ? `v${row.version}` : String(row.version)}`,
  latest: (latestPinShown, versions) =>
    `${latestPinShown ? "Latest" : "Saved latest"} (V${versions[0]?.version_number ?? ""})`,
  numbered: (v) => `V${v.version_number}`,
  header: (shownVersion) => `Version label: ${shownVersion}`,
  child: (child) => ` · recorded v${child.pinned_version_number}`,
};
