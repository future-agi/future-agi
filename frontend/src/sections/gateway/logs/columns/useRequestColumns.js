import { useCallback, useMemo, useState } from "react";
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

export const STORAGE_NOTICE = "Preferences could not be saved";

function identitySegment(value) {
  if (value === null || value === undefined || value === "") return null;
  return String(value);
}

function queryStatus(query) {
  if (query?.status === "success" || query?.status === "error") {
    return query.status;
  }
  if (query?.isError) return "error";
  if (query?.isSuccess) return "success";
  return "pending";
}

/**
 * Column preferences for the Request Logs table (TH-7041).
 *
 * - identity: authenticated user id + current organization id; storage is not
 *   consulted until both are known (J4).
 * - declarations: the existing org-scoped `useCustomProperties()` query; custom
 *   columns render only while it is successful (J6).
 * - persistence: browser localStorage, ids only (R13-R16).
 */
export default function useRequestColumns({ viewId = COLUMN_VIEW_ID } = {}) {
  const { user } = useAuthContext();
  const { currentOrganizationId, isReady } = useOrganization();
  const declarationsQuery = useCustomProperties();

  const userId = identitySegment(user?.id);
  const orgId = identitySegment(currentOrganizationId);
  const storageKey = useMemo(
    () => (isReady ? buildKey(userId, orgId, viewId) : null),
    [isReady, userId, orgId, viewId],
  );

  // Read synchronously when the key changes so no render ever shows another
  // identity's configuration (AC4). Edits are tracked per key in state.
  const stored = useMemo(
    () => (storageKey ? readConfig(storageKey).config : null),
    [storageKey],
  );
  const [edits, setEdits] = useState(null);
  const [storageNotice, setStorageNotice] = useState(null);

  const config =
    edits && edits.key === storageKey ? edits.config : stored || DEFAULT_CONFIG;

  const declarationStatus = queryStatus(declarationsQuery);
  const declarations = useMemo(() => {
    const items = Array.isArray(declarationsQuery?.data)
      ? declarationsQuery.data
      : [];
    // Defensive org filter: never offer another organization's names (R31).
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
        status: orgId ? declarationStatus : "pending",
      }),
    [config, declarations, declarationStatus, orgId],
  );

  const unavailableCustomCount = useMemo(
    () =>
      declarationStatus === "success"
        ? 0
        : config.columns.filter((c) => isCustomColumnId(c.id)).length,
    [config, declarationStatus],
  );

  const commit = useCallback(
    (next) => {
      setEdits({ key: storageKey, config: next });
      if (!storageKey) return;
      const result = writeConfig(storageKey, next);
      setStorageNotice(result.ok ? null : STORAGE_NOTICE);
    },
    [storageKey],
  );

  const toggle = useCallback(
    (id) => commit(toggleColumn(config, id)),
    [commit, config],
  );

  const move = useCallback(
    (id, delta) => commit(moveColumn(config, id, delta)),
    [commit, config],
  );

  const remove = useCallback(
    (id) => commit(removeColumn(config, id)),
    [commit, config],
  );

  const reset = useCallback(() => {
    setEdits({ key: storageKey, config: DEFAULT_CONFIG });
    if (!storageKey) return;
    const result = removeConfig(storageKey);
    setStorageNotice(result.ok ? null : STORAGE_NOTICE);
  }, [storageKey]);

  const retryDeclarations = useCallback(() => {
    if (typeof declarationsQuery?.refetch === "function") {
      declarationsQuery.refetch();
    }
  }, [declarationsQuery]);

  const dismissStorageNotice = useCallback(() => setStorageNotice(null), []);

  return {
    columns: resolved.columns,
    pickerEntries: resolved.entries,
    stale: resolved.stale,
    visibleCount: resolved.visibleCount,
    totalCount: resolved.totalCount,
    config,
    storageKey,
    storageNotice,
    declarationStatus,
    declarationError: declarationsQuery?.error ?? null,
    unavailableCustomCount,
    toggle,
    move,
    remove,
    reset,
    retryDeclarations,
    dismissStorageNotice,
  };
}
