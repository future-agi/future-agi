import { useCallback, useRef } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import { serializeFilterListForApi } from "src/api/contracts/filter-contract";
import { useSafeAuthContext } from "src/auth/hooks/use-auth-context";
import { getRequestErrorMessage } from "src/utils/errorUtils";

export const SAVED_VIEWS_KEY = "saved-views";

const FILTER_CONFIG_KEYS = new Set([
  "filters",
  "compare_filters",
  "extra_filters",
  "compare_extra_filters",
]);

const SAVED_VIEW_CONFIG_KEYS = new Set([
  "filters",
  "columns",
  "sort",
  "display",
  "widgets",
  "conversation_id",
  "sub_tab",
  "compare_filters",
  "compare_date_filter",
  "extra_filters",
  "compare_extra_filters",
]);

const CREATE_PAYLOAD_KEYS = new Set([
  "project_id",
  "name",
  "tab_type",
  "visibility",
  "icon",
  "config",
]);

const UPDATE_PAYLOAD_KEYS = new Set(["name", "visibility", "icon", "config"]);

const mapPayloadKeys = (data, allowedKeys) =>
  Object.fromEntries(
    Object.entries(data || {}).filter(([key, value]) => {
      return allowedKeys.has(key) && value !== undefined;
    }),
  );

export const serializeSavedViewConfig = (config = {}) => {
  if (!config || typeof config !== "object" || Array.isArray(config)) {
    throw new Error("Saved view config must be an object.");
  }

  const unknownKeys = Object.keys(config).filter(
    (key) => !SAVED_VIEW_CONFIG_KEYS.has(key),
  );
  if (unknownKeys.length) {
    throw new Error(
      `Unknown saved view config keys: ${unknownKeys.join(", ")}`,
    );
  }

  return Object.fromEntries(
    Object.entries(config).map(([key, value]) => {
      if (FILTER_CONFIG_KEYS.has(key) && value !== null) {
        if (!Array.isArray(value)) {
          throw new Error(`Saved view config "${key}" must be a filter list.`);
        }
        return [key, serializeFilterListForApi(value)];
      }
      return [key, value];
    }),
  );
};

export const buildCreateSavedViewPayload = (data, forcedTabType) => {
  const payload = mapPayloadKeys(data, CREATE_PAYLOAD_KEYS);
  if (forcedTabType) payload.tab_type = forcedTabType;
  if (payload.config !== undefined) {
    payload.config = serializeSavedViewConfig(payload.config);
  }
  return payload;
};

export const buildUpdateSavedViewPayload = (data) => {
  const payload = mapPayloadKeys(data, UPDATE_PAYLOAD_KEYS);
  if (payload.config !== undefined) {
    payload.config = serializeSavedViewConfig(payload.config);
  }
  return payload;
};

// Names the backend would reject for this user (uniqueness is per created_by,
// case-sensitive). Unknown user id → block nothing and let the server arbitrate,
// rather than over-blocking other users' shared names during auth resolution.
export const getOwnViewNames = (views, currentUserId) =>
  (views ?? [])
    .filter(
      (v) =>
        currentUserId && String(v.created_by?.id) === String(currentUserId),
    )
    .map((v) => v.name);

export const DEFAULT_VIEW_NAME = "Default View";

// Each tab type's shared default has its own name. A user's project view
// names are unique across every tab type (the backend's
// (project, created_by, name) rule), so one name for all of them made a
// second tab type's default fail with "already exists", as spans did after
// traces. Traces keeps the original name.
const DEFAULT_VIEW_NAMES = {
  traces: DEFAULT_VIEW_NAME,
  spans: "Default spans view",
  sessions: "Default sessions view",
  users: "Default users view",
};

export const defaultViewNameFor = (tabType) =>
  DEFAULT_VIEW_NAMES[tabType] ?? DEFAULT_VIEW_NAME;

// "Share with project" toasts. The view becomes a project tab every member
// can open; nothing loads it for them, so the words never say "default".
export const sharedWithProjectMessage = (name) =>
  name ? `Shared with the project as ${name}` : "Shared with the project";

export const SHARE_WITH_PROJECT_FAILED =
  "Failed to share the view with the project";

// UI tab strings ("trace"/"spans") → backend tab_type ("traces"/"spans").
export const tabTypeForSelectedTab = (selectedTab) =>
  selectedTab === "spans" ? "spans" : "traces";

// The current user's own default view for a tab_type, or null. Scoped to
// created_by so "Share with project" never adopts (and overwrites) a
// teammate's shared view, and to tab_type so a spans click can't PATCH the
// traces default with spans-shaped config. It matches the tab type's own name
// (defaultViewNameFor); spans also adopts a legacy "Default View" row saved
// before spans had a name of its own.
export const findOwnDefaultView = (views, { tabType, userId }) =>
  (views ?? []).find(
    (v) =>
      (v?.name === defaultViewNameFor(tabType) ||
        (tabType === "spans" && v?.name === DEFAULT_VIEW_NAME)) &&
      v?.tab_type === tabType &&
      userId != null &&
      String(v?.created_by?.id) === String(userId),
  ) ?? null;

const appendCustomViewToCache = (currentResult, newView) => {
  if (!currentResult) return currentResult;
  const currentList = currentResult.custom_views ?? [];
  if (currentList.some((v) => v.id === newView.id)) return currentResult;
  return {
    ...currentResult,
    custom_views: [...currentList, newView],
  };
};

const updateCustomViewInCache = (currentResult, updatedView) => {
  if (!currentResult) return currentResult;
  const currentList = currentResult.custom_views ?? [];
  return {
    ...currentResult,
    custom_views: currentList.map((v) =>
      v.id === updatedView.id ? { ...v, ...updatedView } : v,
    ),
  };
};

const reorderCustomViewsInCache = (currentResult, order) => {
  if (!currentResult) return currentResult;
  const positionById = Object.fromEntries(
    order.map((item) => [item.id, item.position]),
  );
  return {
    ...currentResult,
    custom_views: (currentResult.custom_views ?? [])
      .map((view) => ({
        ...view,
        position: positionById[view.id] ?? view.position,
      }))
      .sort((a, b) => a.position - b.position),
  };
};

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

// Several components watch one saved-views read (the tab bar, the view
// dialog, the tracing view), and the app-wide toast (utils/queryErrorHandler)
// reads meta.errorHandled from whichever of them rendered last. This getter
// answers when the read fails, for all of them: true when a watcher's
// `showsFailure` says that component shows the failure on screen.
// project-detail.js keeps its own copy, because tests mock either file on
// its own.
const sharedReadMeta = (queryClient, queryKey, showsFailure) => ({
  showsFailure,
  get errorHandled() {
    const query = queryClient.getQueryCache().find({ queryKey });
    return Boolean(
      query?.observers.some((observer) =>
        observer.options.meta?.showsFailure?.(query.state.error, query),
      ),
    );
  },
});

// `showsFailure(error, query)`: true when the caller shows this failed read
// on screen itself, so it is not toasted as well.
export const useGetSavedViews = (projectId, { showsFailure } = {}) => {
  const queryClient = useQueryClient();
  const queryKey = [SAVED_VIEWS_KEY, projectId];
  return useQuery({
    queryKey,
    queryFn: async () => {
      const res = await axios.get(endpoints.savedViews.list, {
        params: { project_id: projectId },
      });
      return res.data?.result;
    },
    staleTime: 60_000,
    enabled: !!projectId,
    meta: sharedReadMeta(queryClient, queryKey, showsFailure),
  });
};

// Workspace-scoped saved views (no project_id). Scoped per tab_type so the
// users page, future standalone pages, etc. don't collide.
export const useGetWorkspaceSavedViews = (tabType) => {
  return useQuery({
    queryKey: [SAVED_VIEWS_KEY, "workspace", tabType],
    queryFn: async () => {
      const res = await axios.get(endpoints.savedViews.list, {
        params: { tab_type: tabType },
      });
      return res.data?.result;
    },
    staleTime: 60_000,
    enabled: !!tabType,
  });
};

export const useCreateWorkspaceSavedView = (tabType) => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data) =>
      axios.post(
        endpoints.savedViews.create,
        buildCreateSavedViewPayload(data, tabType),
      ),
    onSuccess: (response) => {
      const newView = response?.data?.result;
      if (newView) {
        queryClient.setQueryData(
          [SAVED_VIEWS_KEY, "workspace", tabType],
          (old) => appendCustomViewToCache(old, newView),
        );
      }
      queryClient.invalidateQueries({
        queryKey: [SAVED_VIEWS_KEY, "workspace", tabType],
      });
    },
  });
};

export const useUpdateWorkspaceSavedView = (tabType) => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...data }) =>
      axios.put(
        endpoints.savedViews.update(id),
        buildUpdateSavedViewPayload(data),
      ),
    onSuccess: (response) => {
      const updated = response?.data?.result;
      if (updated?.id) {
        queryClient.setQueryData(
          [SAVED_VIEWS_KEY, "workspace", tabType],
          (old) => updateCustomViewInCache(old, updated),
        );
      }
      queryClient.invalidateQueries({
        queryKey: [SAVED_VIEWS_KEY, "workspace", tabType],
      });
    },
  });
};

export const useDeleteWorkspaceSavedView = (tabType) => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id) => axios.delete(endpoints.savedViews.delete(id)),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: [SAVED_VIEWS_KEY, "workspace", tabType],
      });
    },
  });
};

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

export const useCreateSavedView = (projectId) => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data) =>
      axios.post(
        endpoints.savedViews.create,
        buildCreateSavedViewPayload(data),
      ),
    onSuccess: (response) => {
      const newView = response?.data?.result;
      if (newView) {
        queryClient.setQueryData([SAVED_VIEWS_KEY, projectId], (old) =>
          appendCustomViewToCache(old, newView),
        );
      }
      queryClient.invalidateQueries({
        queryKey: [SAVED_VIEWS_KEY, projectId],
      });
    },
  });
};

export const useUpdateSavedView = (projectId) => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...data }) =>
      axios.put(
        endpoints.savedViews.update(id),
        buildUpdateSavedViewPayload(data),
        {
          params: { project_id: projectId },
        },
      ),
    onSuccess: (response) => {
      const updated = response?.data?.result;
      if (updated?.id) {
        queryClient.setQueryData([SAVED_VIEWS_KEY, projectId], (old) => {
          return updateCustomViewInCache(old, updated);
        });
      }
      queryClient.invalidateQueries({
        queryKey: [SAVED_VIEWS_KEY, projectId],
      });
    },
  });
};

export const useDeleteSavedView = (projectId) => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id) =>
      axios.delete(endpoints.savedViews.delete(id), {
        params: { project_id: projectId },
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: [SAVED_VIEWS_KEY, projectId],
      });
    },
  });
};

export const useDuplicateSavedView = (projectId) => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, name }) =>
      axios.post(
        endpoints.savedViews.duplicate(id),
        { name },
        { params: { project_id: projectId } },
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: [SAVED_VIEWS_KEY, projectId],
      });
    },
  });
};

export const useReorderSavedViews = (projectId) => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data) => axios.post(endpoints.savedViews.reorder, data),
    onMutate: async ({ order }) => {
      await queryClient.cancelQueries({
        queryKey: [SAVED_VIEWS_KEY, projectId],
      });
      const previous = queryClient.getQueryData([SAVED_VIEWS_KEY, projectId]);

      queryClient.setQueryData([SAVED_VIEWS_KEY, projectId], (old) => {
        return reorderCustomViewsInCache(old, order);
      });

      return { previous };
    },
    onError: (_err, _vars, context) => {
      if (context?.previous) {
        queryClient.setQueryData(
          [SAVED_VIEWS_KEY, projectId],
          context.previous,
        );
      }
    },
    onSettled: () => {
      queryClient.invalidateQueries({
        queryKey: [SAVED_VIEWS_KEY, projectId],
      });
    },
  });
};

// "Share with project": shares the view with the project as its tab type's
// default view, a project-visible saved view every member sees as a tab.
// On a saved-view tab that view becomes project-visible with the current
// config; otherwise the user's own default for the tab type is updated, or a
// new one is created. The returned function takes:
//   tabType      backend tab_type: "traces", "spans", "sessions" or "users"
//   config       the view's current saved-view config
//   activeViewId the open saved view's id, or null on a fixed tab
//   onSaved      optional (savedView, { created }) after a success
// A click is ignored while a share is in flight (a second create would fail
// with "already exists" after the first succeeds), and on a fixed tab until
// the saved views and the user have loaded, since create vs update depends
// on them.
export const useSetProjectDefaultView = (projectId) => {
  const { user } = useSafeAuthContext();
  const { data: savedViewsData } = useGetSavedViews(projectId);
  const { mutate: createSavedView } = useCreateSavedView(projectId);
  const { mutate: updateSavedView } = useUpdateSavedView(projectId);
  const pendingRef = useRef(false);
  const userId = user?.id;

  return useCallback(
    ({ tabType, config, activeViewId, onSaved }) => {
      if (pendingRef.current) return;
      if (!activeViewId && (!savedViewsData || userId == null)) return;
      pendingRef.current = true;

      const onSettled = () => {
        pendingRef.current = false;
      };
      const onError = (err) =>
        enqueueSnackbar(
          getRequestErrorMessage(err, SHARE_WITH_PROJECT_FAILED),
          { variant: "error" },
        );
      const onSuccess = (created) => (response) => {
        const view = response?.data?.result;
        enqueueSnackbar(sharedWithProjectMessage(view?.name), {
          variant: "success",
        });
        onSaved?.(view, { created });
      };

      const name = defaultViewNameFor(tabType);
      const ownDefault = findOwnDefaultView(savedViewsData?.custom_views, {
        tabType,
        userId,
      });
      if (activeViewId || ownDefault) {
        const update = {
          id: activeViewId || ownDefault.id,
          visibility: "project",
          config,
        };
        // A legacy spans default still named "Default View" takes its own
        // name, which frees "Default View" for the Traces default (a
        // person's view names are unique across tab types).
        if (!activeViewId && ownDefault.name !== name) update.name = name;
        updateSavedView(update, {
          onSuccess: onSuccess(false),
          onError,
          onSettled,
        });
        return;
      }
      createSavedView(
        {
          project_id: projectId,
          name,
          tab_type: tabType,
          visibility: "project",
          config,
        },
        { onSuccess: onSuccess(true), onError, onSettled },
      );
    },
    [projectId, userId, savedViewsData, createSavedView, updateSavedView],
  );
};
