import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useOrganization } from "src/contexts/OrganizationContext";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import axios from "src/utils/axios";

export const FOLDERS_URL = "/model-hub/dataset-folders/";

export const folderUrl = (id) => `${FOLDERS_URL}${id}/`;

export function folderErrorSentence(error) {
  const body = error?.response?.data ?? error;
  const detail =
    body?.result ??
    body?.detail ??
    body?.path ??
    body?.dataset_ids ??
    body?.folder_id;
  const messages = (value) => {
    if (typeof value === "string") return [value];
    if (Array.isArray(value)) return value.flatMap(messages);
    if (value && typeof value === "object")
      return Object.values(value).flatMap(messages);
    return [];
  };
  return (
    messages(detail).join(" ") ||
    error?.message ||
    "The folder request failed. Try again."
  );
}

export async function moveDatasetsToFolder(datasets, folderId) {
  const response = await axios.post(`${FOLDERS_URL}move-datasets/`, {
    dataset_ids: datasets.map((row) => row.id),
    folder_id: folderId,
  });
  return response.data.result;
}

export function useRealFolderInventory(enabled) {
  const { currentOrganizationId } = useOrganization();
  const { currentWorkspaceId } = useWorkspace();
  return useQuery({
    queryKey: [
      "datasets",
      "list",
      "folders",
      currentOrganizationId,
      currentWorkspaceId,
    ],
    enabled: Boolean(enabled && currentOrganizationId && currentWorkspaceId),
    queryFn: async ({ signal }) => {
      const response = await axios.get(FOLDERS_URL, { signal });
      return response.data.result;
    },
    staleTime: 0,
    retry: false,
  });
}

export function useRefreshFolders() {
  const client = useQueryClient();
  return () => client.invalidateQueries({ queryKey: ["datasets"] });
}

export async function moveDatasetsToPath(datasets, path) {
  const response = await axios.get(FOLDERS_URL);
  const folder = response.data.result.folders.find((f) => f.path === path);
  if (path && !folder)
    throw new Error("That folder no longer exists. Refresh and try again.");
  await moveDatasetsToFolder(datasets, folder?.id ?? null);
}
