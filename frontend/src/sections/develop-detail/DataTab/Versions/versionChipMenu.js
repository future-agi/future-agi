// The version chip's menu (one view, A1-10). Pure; tested.
//
//   Version history          H
//   Compare two versions…
//   ──────────────
//   v2 · latest · prod-failures     (viewing → checked)
//   v1 · Oct 14
//   … the last five versions, newest first

import { fmtTimeRelative } from "src/sections/common/format/fmt";

export const CHIP_MENU_VERSIONS = 5;

const NOTE_MAX = 80;
/** A version's note, one line, clipped. */
export const noteOf = (v) => {
  const t = String(v?.description ?? "")
    .replace(/\s+/g, " ")
    .trim();
  if (!t) return null;
  return t.length > NOTE_MAX ? `${t.slice(0, NOTE_MAX - 1)}…` : t;
};

/** `v2 · latest · prod-failures`, `v1 · golden-v3`. */
export function versionMenuLabel(v) {
  return [
    `v${v.number}`,
    v.is_latest ? "latest" : null,
    v.tags?.length ? v.tags.join(", ") : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

/**
 * @param {{versions?: object[], viewingNumber?: number,
 *   latestNumber?: number|null, historyOpen?: boolean, onHistory: Function,
 *   onCompare: Function, onView: (n: number) => void, now?: number}} args
 * @returns {object[]} ItemsMenu items
 */
export function versionChipMenuItems({
  versions,
  viewingNumber,
  historyOpen = false,
  onHistory,
  onCompare,
  onView,
  now,
}) {
  const list = [...(versions ?? [])].sort((a, b) => b.number - a.number);
  const recent = list.slice(0, CHIP_MENU_VERSIONS);
  const canCompare = list.length >= 2;
  return [
    {
      id: "history",
      label: historyOpen ? "Hide version history" : "Version history",
      icon: "mdi:history",
      shortcut: "history",
      run: onHistory,
    },
    {
      id: "compare",
      label: "Compare two versions…",
      icon: "mdi:compare-horizontal",
      disabled: !canCompare,
      reason: canCompare ? undefined : "Compare needs at least two versions",
      run: onCompare,
    },
    ...(recent.length ? [{ divider: true }] : []),
    ...recent.map((v) => ({
      id: `v${v.number}`,
      label: versionMenuLabel(v),
      secondary: [
        v.created_at ? fmtTimeRelative(v.created_at, { now }) : null,
        v.created_by?.name ?? null,
        // v2.1 g8 (corrections:provenance): the version's note says where
        // its changes came from (`1 correction from review “review” by Maya`).
        noteOf(v),
      ]
        .filter(Boolean)
        .join(" · "),
      checked: v.number === viewingNumber,
      run: () => onView(v.number),
    })),
  ];
}
