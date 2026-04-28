import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

export const SAVED_VIEWS_KEY = "saved-views";

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

export const useGetSavedViews = (projectId) => {
  return useQuery({
    queryKey: [SAVED_VIEWS_KEY, projectId],
    queryFn: () =>
      axios.get(endpoints.savedViews.list, {
        params: { project_id: projectId },
      }),
    select: (d) => d.data?.result,
    staleTime: 60_000,
    enabled: !!projectId,
  });
};

// Workspace-scoped saved views (no project_id). Scoped per tab_type so the
// users page, future standalone pages, etc. don't collide.
export const useGetWorkspaceSavedViews = (tabType) => {
  return useQuery({
    queryKey: [SAVED_VIEWS_KEY, "workspace", tabType],
    queryFn: () =>
      axios.get(endpoints.savedViews.list, {
        params: { tab_type: tabType },
      }),
    select: (d) => d.data?.result,
    staleTime: 60_000,
    enabled: !!tabType,
  });
};

export const useCreateWorkspaceSavedView = (tabType) => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.savedViews.create, { ...data, tab_type: tabType }),
    onSuccess: (response) => {
      const newView = response?.data?.result;
      if (newView) {
        queryClient.setQueryData(
          [SAVED_VIEWS_KEY, "workspace", tabType],
          (old) => {
            if (!old) return old;
            const currentResult = old.data?.result ?? {};
            const currentList =
              currentResult.custom_views ?? currentResult.customViews ?? [];
            if (currentList.some((v) => v.id === newView.id)) return old;
            const nextList = [...currentList, newView];
            return {
              ...old,
              data: {
                ...old.data,
                result: {
                  ...currentResult,
                  custom_views: nextList,
                  customViews: nextList,
                },
              },
            };
          },
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
      axios.put(endpoints.savedViews.update(id), data),
    onSuccess: (response) => {
      const updated = response?.data?.result;
      if (updated?.id) {
        queryClient.setQueryData(
          [SAVED_VIEWS_KEY, "workspace", tabType],
          (old) => {
            if (!old) return old;
            const currentResult = old.data?.result ?? {};
            const currentList =
              currentResult.custom_views ?? currentResult.customViews ?? [];
            const nextList = currentList.map((v) =>
              v.id === updated.id ? { ...v, ...updated } : v,
            );
            return {
              ...old,
              data: {
                ...old.data,
                result: {
                  ...currentResult,
                  custom_views: nextList,
                  customViews: nextList,
                },
              },
            };
          },
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
    mutationFn: (data) => axios.post(endpoints.savedViews.create, data),
    onSuccess: (response) => {
      const newView = response?.data?.result;
      if (newView) {
        queryClient.setQueryData(
          [SAVED_VIEWS_KEY, projectId],
          (old) => {
            if (!old) return old;
            const currentResult = old.data?.result ?? {};
            const currentList =
              currentResult.custom_views ?? currentResult.customViews ?? [];
            if (currentList.some((v) => v.id === newView.id)) return old;
            const nextList = [...currentList, newView];
            return {
              ...old,
              data: {
                ...old.data,
                result: {
                  ...currentResult,
                  custom_views: nextList,
                  customViews: nextList,
                },
              },
            };
          },
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
      axios.put(endpoints.savedViews.update(id), data, {
        params: { project_id: projectId },
      }),
    onSuccess: (response) => {
      const updated = response?.data?.result;
      if (updated?.id) {
        queryClient.setQueryData([SAVED_VIEWS_KEY, projectId], (old) => {
          if (!old) return old;
          const currentResult = old.data?.result ?? {};
          const currentList =
            currentResult.custom_views ?? currentResult.customViews ?? [];
          const nextList = currentList.map((v) =>
            v.id === updated.id ? { ...v, ...updated } : v,
          );
          return {
            ...old,
            data: {
              ...old.data,
              result: {
                ...currentResult,
                custom_views: nextList,
                customViews: nextList,
              },
            },
          };
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
        if (!old?.data?.result) return old;
        const posMap = Object.fromEntries(
          order.map((item) => [item.id, item.position]),
        );
        const updated = old.data.result.custom_views
          .map((v) => ({
            ...v,
            position: posMap[v.id] ?? v.position,
          }))
          .sort((a, b) => a.position - b.position);
        return {
          ...old,
          data: {
            ...old.data,
            result: { ...old.data.result, custom_views: updated },
          },
        };
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
