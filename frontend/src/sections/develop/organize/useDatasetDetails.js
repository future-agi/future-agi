import { useQuery } from "@tanstack/react-query";
import { useOrganization } from "src/contexts/OrganizationContext";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import axios, { endpoints } from "src/utils/axios";

export function useDatasetDetails(datasetId, enabled = true) {
  const { currentOrganizationId } = useOrganization();
  const { currentWorkspaceId } = useWorkspace();
  return useQuery({
    queryKey: [
      "datasets",
      "details",
      currentOrganizationId,
      currentWorkspaceId,
      datasetId,
    ],
    enabled: Boolean(
      enabled && datasetId && currentOrganizationId && currentWorkspaceId,
    ),
    queryFn: async ({ signal }) => {
      const response = await axios.get(
        endpoints.develop.updateDataset(datasetId),
        { signal },
      );
      return response.data.result;
    },
    staleTime: 0,
    retry: false,
  });
}
