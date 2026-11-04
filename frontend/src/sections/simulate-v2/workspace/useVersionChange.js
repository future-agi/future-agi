import { useEffect, useState } from "react";
import { CHANGE_TAB, changeOf } from "../_mock/versionBuild";

/*
  One kind of change in a new environment version, for the tab that shows it:
  `live` while it builds (what's written so far), `fresh` once built and not
  yet looked at (labelled "New in vN"). Null for both when nothing changed.
*/
export default function useVersionChange(envState, kind) {
  const build = envState?.versionBuild || null;
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!build) return undefined;
    const t = setInterval(() => setNow(Date.now()), 400);
    return () => clearInterval(t);
  }, [build]);
  const change = changeOf(build, kind, now);
  /* A finished step is new, not building — even while later steps run. */
  if (change?.state === "done")
    return { live: null, fresh: { label: change.label, ids: change.ids } };
  const news = envState?.versionNews;
  const fresh =
    !change && news?.unseen?.includes(CHANGE_TAB[kind])
      ? { label: news.label, ids: news[kind] || [] }
      : null;
  return { live: change, fresh };
}
