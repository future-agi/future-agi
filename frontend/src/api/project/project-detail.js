import {
  useQuery,
  useQueryClient,
  keepPreviousData,
} from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

// Several components watch one project read, and the app-wide toast
// (utils/queryErrorHandler) reads meta.errorHandled from whichever of them
// rendered last. This getter answers when the read fails, for all of them:
// true when a watcher's `showsFailure` says that component shows the failure
// on screen. saved-views.js keeps its own copy, because tests mock either
// file on its own.
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

export const useGetProjectDetails = (
  projectId,
  enabled = true,
  keepPrevious = false,
  // `showsFailure(error, query)`: true when the caller shows this failed
  // read on screen itself, so it is not toasted as well.
  { showsFailure } = {},
) => {
  const queryClient = useQueryClient();
  const queryKey = ["project-detail", projectId];
  return useQuery({
    queryKey,
    queryFn: () =>
      axios.get(endpoints.project.projectExperimentDetail(projectId)),
    select: (d) => d?.data?.result,
    enabled: enabled,
    staleTime: 1 * 60 * 1000, // 1 min stale time
    // Opt-in: hold last value across refetch so `source` never flickers
    // undefined. Off by default — callers whose projectId is user-switchable
    // rely on undefined-while-fetching to gate project-kind resolution.
    ...(keepPrevious ? { placeholderData: keepPreviousData } : {}),
    meta: sharedReadMeta(queryClient, queryKey, showsFailure),
  });
};

export const useGetProjectVersionDetail = (runId) => {
  return useQuery({
    queryKey: ["project-detail", "projectVersion", runId],
    queryFn: () => axios.get(endpoints.project.getProjectVersion(runId)),
    select: (d) => d.data,
    staleTime: 1 * 60 * 1000, // 1 min stale time
  });
};

export const useGetProjectEvalConfigs = (projectId, options = {}) => {
  return useQuery({
    queryKey: ["project-detail", "eval-configs", projectId],
    queryFn: () =>
      axios.get(endpoints.project.getEvalConfigs, {
        params: { project_id: projectId },
      }),
    select: (d) => d.data?.result,
    enabled: Boolean(projectId),
    ...options,
  });
};
