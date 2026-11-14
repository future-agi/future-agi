import { useMutation, useQuery } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { endpoints } from "src/utils/axios";

/** Fetch a fresh authenticated attachment without caching file contents. */
export const useDownloadKnowledgeBaseFile = () =>
  useMutation({
    mutationFn: async ({ kbId, fileId }) => {
      const response = await axios.get(endpoints.knowledge.downloadFile(kbId), {
        params: { file_id: fileId },
        responseType: "blob",
      });
      return response.data;
    },
    retry: false,
  });

export const useKnowledgeBaseList = (search_text, options, newPayload = {}) => {
  const payload = {};
  const queryKey = ["knowledge-base"];

  if (search_text?.length) {
    payload.search = search_text;
    queryKey.push(search_text);
  }

  const addToPayloadAndKey = (key, value) => {
    if (value !== undefined && value !== null) {
      payload[key] = value;
      queryKey.push(key);
    }
  };

  Object.entries(newPayload).forEach(([key, value]) => {
    addToPayloadAndKey(key, value);
  });

  return useQuery({
    queryKey,
    queryFn: () =>
      axios.get(endpoints.knowledge.list, {
        params: {
          ...payload,
        },
      }),
    select: (d) => d?.data?.result?.table_data,
    staleTime: 1 * 60 * 1000, // 1 min stale time
    ...options,
  });
};
