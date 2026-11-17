// Helpers over an `omega-hub-check-report/v1` (SPEC §4.5).

export function formatDuration(ms) {
  if (ms === null || ms === undefined) return "";
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

export function allFindings(report) {
  return (report?.steps || []).flatMap((step) =>
    (step.findings || []).map((finding) => ({ ...finding, step: step.id })),
  );
}

export function flagIds(report) {
  return [
    ...new Set(
      allFindings(report)
        .filter((f) => f.severity === "flag" && f.id)
        .map((f) => f.id),
    ),
  ].sort();
}

export function findingsByFile(report) {
  const out = {};
  for (const finding of allFindings(report)) {
    if (!finding.file) continue;
    (out[finding.file] = out[finding.file] || []).push(finding);
  }
  return out;
}

export function notesByFile(notes) {
  const out = {};
  for (const note of notes || []) {
    if (!note?.file) continue;
    (out[note.file] = out[note.file] || []).push(note);
  }
  return out;
}

// Line marks for the file viewer: findings and reviewer notes on one file.
export function marksFor(path, { report, notes } = {}) {
  const findings = (findingsByFile(report)[path] || []).map((f) => ({
    kind: "finding",
    line: f.line,
    rule: f.rule || f.code,
    text: f.message,
  }));
  const reviewer = (notesByFile(notes)[path] || []).map((n) => ({ kind: "note", line: n.line, text: n.note }));
  return [...findings, ...reviewer];
}
