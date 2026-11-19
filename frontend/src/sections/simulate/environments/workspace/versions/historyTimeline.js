import { VERSION_PIECES } from "src/api/simulate-environments/versions";
import { fDate } from "src/utils/format-time";
import { VERSIONS_COPY } from "../workspace.constants";

// The DOM id of a version's row, so a link can scroll to it.
export const historyEntryId = (key) => `history-entry-${key}`;

// The History tab's filter values: every version, or one piece's.
export const HISTORY_KINDS = ["all", "environment", "suite", "agent"];

const timeOf = (version) =>
  version.createdAt ? new Date(version.createdAt).getTime() : null;

// Badge keys, in display order. Their words live in VERSIONS_COPY.history.
const badgesOf = (piece, version, latestId) => {
  const badges = [];
  if (version.isDefault) badges.push(piece === "agent" ? "tested" : "inUse");
  if (version.id === latestId) badges.push("latest");
  if (version.status === "draft") badges.push("draft");
  if (version.status === "discarded") badges.push("discarded");
  return badges;
};

const originLineOf = (version) => {
  if (!version.origin) return null;
  const commit =
    version.origin === "push" && version.source?.commit_sha?.slice(0, 7);
  return [VERSIONS_COPY.origin[version.origin] || version.origin, commit]
    .filter(Boolean)
    .join(" · ");
};

// Every version of the three pieces as one list, newest first; versions with
// no creation time go last, in the order the server listed them.
export function timelineEntries(data) {
  if (!data) return [];
  const entries = VERSION_PIECES.flatMap((piece) => {
    const list = data[piece] || [];
    const newestActive = list
      .filter((one) => one.status === "active")
      .reduce(
        (best, one) =>
          best === null ||
          (timeOf(one) ?? -Infinity) > (timeOf(best) ?? -Infinity)
            ? one
            : best,
        null,
      );
    return list.map((version) => ({
      key: `${piece}:${version.id}`,
      piece,
      version,
      badges: badgesOf(piece, version, newestActive?.id),
      originLine: originLineOf(version),
    }));
  });
  return entries
    .map((entry, index) => ({ entry, index }))
    .sort((a, b) => {
      const ta = timeOf(a.entry.version);
      const tb = timeOf(b.entry.version);
      if (ta === null && tb === null) return a.index - b.index;
      if (ta === null) return 1;
      if (tb === null) return -1;
      return tb - ta || a.index - b.index;
    })
    .map(({ entry }) => entry);
}

export const filterEntries = (entries, kind) =>
  kind === "all" ? entries : entries.filter((entry) => entry.piece === kind);

const dayKey = (date) =>
  `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;

// Consecutive entries grouped under the local day they were made on.
export function groupByDay(entries, now = new Date()) {
  const today = dayKey(now);
  const yesterdayDate = new Date(now);
  yesterdayDate.setDate(now.getDate() - 1);
  const yesterday = dayKey(yesterdayDate);
  const { days } = VERSIONS_COPY.history;

  const groups = [];
  entries.forEach((entry) => {
    const time = timeOf(entry.version);
    const date = time === null ? null : new Date(time);
    const key = date ? dayKey(date) : "earlier";
    let label = days.earlier;
    if (key === today) label = days.today;
    else if (key === yesterday) label = days.yesterday;
    else if (date) label = fDate(date, "MMM d");
    const last = groups[groups.length - 1];
    if (last?.key === key) last.entries.push(entry);
    else groups.push({ key, label, entries: [entry] });
  });
  return groups;
}
