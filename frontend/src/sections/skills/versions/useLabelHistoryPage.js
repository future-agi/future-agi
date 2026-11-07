import { useQuery } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { endpoints } from "src/api/skills/endpoints";
import { skillsKeys } from "src/api/skills/keys";
import { mapLabelEvent, mapPage } from "src/api/skills/mappers";

export default function useLabelHistoryPage(id, label, page, pageSize = 25) {
  return useQuery({
    queryKey: [...skillsKeys.labelHistory(id, label), { page, pageSize }],
    queryFn: async ({ signal }) => {
      const response = await axios.get(endpoints.labelHistory(id), {
        params: { label: label || undefined, page, page_size: pageSize },
        signal,
      });
      return mapPage(response.data.result, mapLabelEvent);
    },
    enabled: Boolean(id),
    meta: { errorHandled: true },
  });
}
