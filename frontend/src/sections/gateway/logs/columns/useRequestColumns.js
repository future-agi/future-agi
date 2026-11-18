import { useCallback, useEffect, useMemo, useState } from "react";
import { enqueueSnackbar } from "notistack";
import { useAuthContext } from "src/auth/hooks/use-auth-context";
import { useOrganization } from "src/contexts/OrganizationContext";
import { useCustomProperties } from "../../custom-properties/hooks/useCustomProperties";
import {
  COLUMN_VIEW_ID,
  DEFAULT_CONFIG,
  isCustomColumnId,
  moveColumn,
  removeColumn,
  resolveColumns,
  toggleColumn,
} from "./columnModel";
import {
  buildKey,
  readConfig,
  removeConfig,
  writeConfig,
} from "./columnPrefsStorage";

const STORAGE_NOTICE = "Preferences could not be saved";

function identitySegment(value) {
  if (value === null || value === undefined || value === "") return null;
  return String(value);
}

/**
 * Column preferences for the Request Logs table.
 *
 * - identity: authenticated user id + current organization id; storage is not
 *   consulted until both are known.
 * - declarations: use the current organization's loaded query data, including
 *   cached data during a failed refresh. The picker's Retry row still reports
 *   the query error; an initial failure never exposes saved custom names.
 * - persistence: browser localStorage, ids only.
 */
export default function useRequestColumns({ enabled = true } = {}) {
  const { user } = useAuthContext();
  const { currentOrganizationId, isReady } = useOrganization();
  const declarationsQuery = useCustomProperties(
    {},
    { enabled, meta: { errorHandled: true } },
  );

  const userId = identitySegment(user?.id);
  const orgId = identitySegment(currentOrganizationId);
  const storageKey = useMemo(
    () => (isReady ? buildKey(userId, orgId, COLUMN_VIEW_ID) : null),
    [isReady, userId, orgId],
  );

  // Read synchronously when the key changes so no render ever shows another
  // identity's configuration. Edits are tracked per key in state.
  const stored = useMemo(
    () => (storageKey ? readConfig(storageKey).config : null),
    [storageKey],
  );
  const [edits, setEdits] = useState(null);

  const config =
    edits && edits.key === storageKey ? edits.config : stored || DEFAULT_CONFIG;

  // Another tab saved or reset this layout: adopt it in place of this tab's
  // edits, so the latest save wins and the next edit here does not drop it.
  useEffect(() => {
    if (!storageKey) return undefined;
    const handleStorage = (event) => {
      if (event.key !== storageKey) return;
      setEdits({
        key: storageKey,
        config: readConfig(storageKey).config || DEFAULT_CONFIG,
      });
    };
    window.addEventListener("storage", handleStorage);
    return () => window.removeEventListener("storage", handleStorage);
  }, [storageKey]);

  const declarationStatus = declarationsQuery.status;
  const declarationsAvailable =
    Boolean(isReady && orgId) &&
    Array.isArray(declarationsQuery.data) &&
    !declarationsQuery.isPlaceholderData;
  const declarations = useMemo(() => {
    const items = Array.isArray(declarationsQuery?.data)
      ? declarationsQuery.data
      : [];
    // Defensive org filter: never offer another organization's names.
    return items.filter(
      (item) =>
        item &&
        (item.organization === null ||
          item.organization === undefined ||
          String(item.organization) === orgId),
    );
  }, [declarationsQuery?.data, orgId]);

  const resolved = useMemo(
    () =>
      resolveColumns({
        config,
        declarations,
        status: declarationsAvailable ? "success" : "pending",
      }),
    [config, declarations, declarationsAvailable],
  );

  const unavailableCustomCount = useMemo(
    () =>
      declarationsAvailable
        ? 0
        : config.columns.filter((c) => isCustomColumnId(c.id)).length,
    [config, declarationsAvailable],
  );

  const commit = useCallback(
    (next) => {
      setEdits({ key: storageKey, config: next });
      if (!storageKey) return;
      if (!writeConfig(storageKey, next).ok) {
        enqueueSnackbar(STORAGE_NOTICE, { variant: "warning" });
      }
    },
    [storageKey],
  );

  const toggle = useCallback(
    (id) => commit(toggleColumn(config, id)),
    [commit, config],
  );

  const move = useCallback(
    (id, delta) => commit(moveColumn(config, id, delta, resolved.movableIds)),
    [commit, config, resolved.movableIds],
  );

  const remove = useCallback(
    (id) => commit(removeColumn(config, id)),
    [commit, config],
  );

  const reset = useCallback(() => {
    setEdits({ key: storageKey, config: DEFAULT_CONFIG });
    if (!storageKey) return;
    if (!removeConfig(storageKey).ok) {
      enqueueSnackbar(STORAGE_NOTICE, { variant: "warning" });
    }
  }, [storageKey]);

  const retryDeclarations = useCallback(
    () => declarationsQuery.refetch(),
    [declarationsQuery],
  );

  return {
    columns: resolved.columns,
    pickerEntries: resolved.entries,
    stale: resolved.stale,
    visibleCount: resolved.visibleCount,
    totalCount: resolved.totalCount,
    atCap: resolved.atCap,
    config,
    storageKey,
    declarationStatus,
    declarationsAvailable,
    declarationsFetching: declarationsQuery.isFetching,
    reservedDeclared: resolved.reservedDeclared,
    unavailableCustomCount,
    toggle,
    move,
    remove,
    reset,
    retryDeclarations,
  };
}
