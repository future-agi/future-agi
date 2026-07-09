import { useQuery, keepPreviousData } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

export const useGetProjectDetails = (
  projectId,
  enabled = true,
  keepPrevious = false,
) => {
  return useQuery({
    queryKey: ["project-detail", projectId],
    queryFn: () =>
      axios.get(endpoints.project.projectExperimentDetail(projectId)),
    select: (d) => d?.data?.result,
    enabled: enabled,
    staleTime: 1 * 60 * 1000, // 1 min stale time
    // Opt-in: hold last value across refetch so `source` never flickers
    // undefined. Off by default — callers whose projectId is user-switchable
    // rely on undefined-while-fetching to gate project-kind resolution.
    ...(keepPrevious ? { placeholderData: keepPreviousData } : {}),
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
