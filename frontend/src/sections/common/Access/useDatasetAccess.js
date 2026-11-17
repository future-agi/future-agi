// The one hook the access gates use (W4-SCOPE §6 A1 "UI gates"):
// useDatasetAccess(datasetId) -> {can: {view, download, edit, run, manage},
// reason(perm), ask, isLoading, view}. While the access view loads, or when
// it can't be read, `can` allows the four dataset permissions (the server's
// guard still refuses, and a door never shows a reason it can't name) and
// withholds manage (no admin control shows before the server says so).
import { useCallback, useMemo } from "react";
import { useDatasetAccessQuery } from "src/api/develop/dataset-access";
import { reasonFor } from "./accessModel";

const OPEN = Object.freeze({
  view: true,
  download: true,
  edit: true,
  run: true,
  manage: false,
});

export default function useDatasetAccess(datasetId) {
  const { data: view, isLoading } = useDatasetAccessQuery(datasetId);
  const can = useMemo(
    () => (view?.me?.perms ? { ...OPEN, ...view.me.perms } : OPEN),
    [view],
  );
  const reason = useCallback((perm) => reasonFor(perm, view), [view]);
  return { can, reason, ask: view?.ask ?? [], isLoading, view: view ?? null };
}
