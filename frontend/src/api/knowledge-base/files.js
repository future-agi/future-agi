import { useQuery } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { endpoints } from "src/utils/axios";

// The knowledge base list every picker reads. It comes from the picker route,
// which returns every knowledge base of the organization as {id, name}, with
// no pages, and accepts search and status (status keeps only the knowledge
// bases that finished indexing). The table route (endpoints.knowledge.list)
// returns 10 rows unless asked for more and refuses status with a 400, so a
// picker reading it showed only the 10 newest, or nothing at all.
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
      axios.get(endpoints.knowledge.pickerList, {
        params: {
          ...payload,
        },
      }),
    select: (d) => d?.data?.result?.table_data,
    staleTime: 1 * 60 * 1000, // 1 min stale time
    ...options,
  });
};
