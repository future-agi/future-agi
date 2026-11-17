import { useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { useSafeAuthContext } from "src/auth/hooks/use-auth-context";
import axios, { endpoints } from "src/utils/axios";
import {
  SAVED_VIEWS_KEY,
  defaultViewNameFor,
  findOwnDefaultView,
  useCreateSavedView,
  useUpdateSavedView,
} from "src/api/project/saved-views";
import {
  mergeSessionTurnFields,
  normalizeSessionTurnFields,
  sessionTurnFieldError,
} from "./sessionTurnFields";

// The normal saved-view mutations are reused, but this read additionally keys
// the authenticated workspace/user so a scope switch never reuses old fields.
export default function useSessionTurnFields(
  projectId,
  { activeViewId = null } = {},
) {
  const { currentWorkspaceId } = useWorkspace();
  const { user } = useSafeAuthContext();
  const userId = user?.id;
  const enabled = Boolean(projectId && currentWorkspaceId && userId);
  const scopeKey = JSON.stringify([
    currentWorkspaceId,
    userId,
    projectId,
    activeViewId,
  ]);
  const currentScope = useRef(scopeKey);
  currentScope.current = scopeKey;
  const [applied, setApplied] = useState({ scopeKey, fields: null });
  // Reset during render, not a later effect: no frame may expose the old scope.
  if (applied.scopeKey !== scopeKey) setApplied({ scopeKey, fields: null });
  const queryClient = useQueryClient();
  const queryKey = [
    SAVED_VIEWS_KEY,
    projectId,
    "session-turn-fields",
    currentWorkspaceId,
    userId,
  ];
  const query = useQuery({
    queryKey,
    queryFn: async () => {
      const response = await axios.get(endpoints.savedViews.list, {
        params: { project_id: projectId },
      });
      return response.data?.result;
    },
    enabled,
    placeholderData: () => undefined,
    staleTime: 60_000,
  });
  const create = useCreateSavedView(projectId);
  const update = useUpdateSavedView(projectId);
  const views =
    enabled && query.isSuccess ? query.data?.custom_views ?? [] : [];
  const activeView = activeViewId
    ? views.find(
        (view) =>
          String(view.id) === String(activeViewId) &&
          view.tab_type === "sessions",
      )
    : null;
  const ownDefault = findOwnDefaultView(views, { tabType: "sessions", userId });
  const ready =
    enabled && query.isSuccess && (!activeViewId || Boolean(activeView));
  const baseline = activeViewId ? activeView : ownDefault;
  const storedFields = baseline?.config?.display?.sessionTurnFields;
  const fields = useMemo(
    () =>
      applied.scopeKey === scopeKey && applied.fields !== null
        ? applied.fields
        : normalizeSessionTurnFields(storedFields),
    [applied, scopeKey, storedFields],
  );
  const owns = (view) => view && String(view.created_by?.id) === String(userId);
  const target = owns(activeView) ? activeView : ownDefault;

  const save = async (nextFields, currentConfig) => {
    if (!ready || currentScope.current !== scopeKey) {
      throw new Error(
        "Wait for this project's preferences to load, then try again.",
      );
    }
    if (!Array.isArray(nextFields) || nextFields.some(sessionTurnFieldError)) {
      throw new Error("Correct the turn fields before saving.");
    }
    // The complete current snapshot is authoritative, including omitted keys.
    // Only drawer-only saves without a snapshot fall back to the stored config.
    const base = currentConfig ?? target?.config ?? baseline?.config ?? {};
    const config = mergeSessionTurnFields(base, nextFields);
    const response = target
      ? await update.mutateAsync({ id: target.id, config })
      : await create.mutateAsync({
          project_id: projectId,
          name: defaultViewNameFor("sessions"),
          tab_type: "sessions",
          visibility: "personal",
          config,
        });
    if (currentScope.current !== scopeKey) return null;
    const saved = response?.data?.result;
    if (!saved?.id)
      throw new Error("The server did not confirm the save. Try again.");
    queryClient.setQueryData(queryKey, (old) => ({
      ...old,
      custom_views: [
        ...(old?.custom_views ?? []).filter(
          (view) => String(view.id) !== String(saved.id),
        ),
        saved,
      ],
    }));
    setApplied({ scopeKey, fields: normalizeSessionTurnFields(nextFields) });
    return {
      config,
      savedActiveView: Boolean(
        activeViewId && String(saved.id) === String(activeViewId),
      ),
    };
  };

  return {
    projectId,
    scopeKey,
    fields,
    ready,
    save,
    error:
      query.error ||
      (enabled && query.isSuccess && activeViewId && !activeView
        ? new Error(
            "This saved view is unavailable. Reload the views or open the Sessions tab.",
          )
        : null),
    retry: query.refetch,
    saveDescription:
      activeView && !owns(activeView)
        ? "Changes are saved to your default sessions view, not this member's shared view."
        : target?.visibility === "project"
          ? "Changes are saved to your shared sessions view."
          : "Changes are saved to your sessions view for this project.",
  };
}
