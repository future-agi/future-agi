import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { serializeFilterListForApi } from "src/api/contracts/filter-contract";

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

export const useGetSavedViews = (projectId) => {
  return useQuery({
    queryKey: [SAVED_VIEWS_KEY, projectId],
    queryFn: async () => {
      const res = await axios.get(endpoints.savedViews.list, {
        params: { project_id: projectId },
      });
      return res.data?.result;
    },
    staleTime: 60_000,
    enabled: !!projectId,
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
