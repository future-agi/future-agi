// W1-6 (notify E06): where a run came from, as its details print it. The
// experiment's first runs came from where they were created (the create's
// `metadata.source`: ui, sdk, …); a later run of a config names what started
// it and who (`trigger {by, at, action}`): `Rerun · failed rows by Maya Chen
// · Oct 23, 10:42`. W2 adds schedule, ci and event origins. Pure.
import { fDateTime } from "src/utils/format-time";

const ACTION_WORDS = Object.freeze({
  rerun: "Rerun",
  rerun_all: "Rerun · all rows",
  rerun_failed: "Rerun · failed rows",
  run_evaluations: "Re-score",
  edit_eval: "Eval edited",
  failed: "Rerun · failed rows",
  changed: "Rerun · changed configs",
  rescore: "Re-score",
  add_eval: "Eval added",
  edit: "Edit experiment",
  error_policy: "Errored tries re-counted",
});

// How a create source reads after "Created from" (serial merge M5, S3
// `sdk` / `ci` read as words, not codes).
const SOURCE_WORDS = Object.freeze({
  ui: "the app",
  sdk: "the SDK",
  api: "the API",
  ci: "CI",
  schedule: "a schedule",
});

const nameOf = (by) =>
  typeof by === "string" ? by : by?.name ?? by?.display_name ?? null;

/**
 * @param {object} run  R1 run (`origin`, `trigger`, `attempt`, `metadata`,
 *   `iteration.scope`)
 * @returns {string}
 */
export function originLine(run) {
  const t = run?.trigger;
  if (run?.origin && t) {
    const what =
      ACTION_WORDS[t.action] ??
      ACTION_WORDS[run?.iteration?.scope] ??
      ACTION_WORDS[run.origin] ??
      String(run.origin);
    return [
      `${what}${nameOf(t.by) ? ` by ${nameOf(t.by)}` : ""}`,
      t.at ? fDateTime(t.at) : null,
    ]
      .filter(Boolean)
      .join(" · ");
  }
  const source = run?.metadata?.source ?? run?.source?.kind ?? "ui";
  return `Created from ${SOURCE_WORDS[source] ?? source}`;
}
