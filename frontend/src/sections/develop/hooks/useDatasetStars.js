// The old per-browser pins become server stars, once (W9-SCOPE §5 A4).
// IT-R5 G2 P8 kept pinned dataset ids in layout prefs extras
// (`<surface>Pins` › `extras.pinned`, newest first). On the first list load
// after W9 each pin is POSTed as a star (oldest first, so the newest pin is
// the newest star), then the key is cleared: the server's star is the one
// ordering from then on, in the list and in every picker.
import { useEffect, useRef } from "react";
import { useLayoutPrefs } from "src/sections/common/ux/oneview/layoutPrefs";
import { putStar } from "src/api/develop/dataset-org";
import { DATASET_LIST_SURFACE } from "../datasetList";

export const PINS_SURFACE = `${DATASET_LIST_SURFACE}Pins`;

/** The pinned ids a layout holds, newest first. Pure. */
export const pinnedIdsOf = (prefs) =>
  Array.isArray(prefs?.extras?.pinned)
    ? prefs.extras.pinned.filter((id) => typeof id === "string" && id)
    : [];

/**
 * Star each pin (oldest first), in sequence; the ids that failed stay
 * pinned so a later load can try again. Pure apart from `star`.
 * @returns {Promise<{starred: string[], kept: string[]}>}
 */
export async function migratePins(pinned, star = putStar) {
  const starred = [];
  const kept = [];
  for (const id of [...pinned].reverse()) {
    try {
      // eslint-disable-next-line no-await-in-loop
      await star(id, true);
      starred.push(id);
    } catch (e) {
      // A deleted dataset (404) has nothing to star: drop its pin too.
      if (e?.response?.status === 404) starred.push(id);
      else kept.push(id);
    }
  }
  return { starred, kept: kept.reverse() };
}

/**
 * Run the migration once per browser and user; `onDone(starredIds)` lets
 * the page refetch. `enabled` false skips it (e.g. before the list loads).
 */
export default function useDatasetStars({ enabled = true, onDone } = {}) {
  const [prefs, patch] = useLayoutPrefs(PINS_SURFACE);
  const running = useRef(false);
  const pinned = pinnedIdsOf(prefs);
  const key = pinned.join("|");
  useEffect(() => {
    if (!enabled || !key || running.current) return;
    running.current = true;
    migratePins(key.split("|")).then(({ starred, kept }) => {
      patch((p) => ({
        ...p,
        extras: { ...(p.extras ?? {}), pinned: kept },
      }));
      running.current = false;
      if (starred.length) onDone?.(starred);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, key]);
}
