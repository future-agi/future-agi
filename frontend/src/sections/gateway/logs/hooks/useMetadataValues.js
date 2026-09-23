import { useQuery } from "@tanstack/react-query";
import axiosInstance, { endpoints } from "src/utils/axios";

/**
 * Application, service and custom tag values seen in recent request logs,
 * used as the options of the filter panel pickers.
 */
export default function useMetadataValues({ enabled = true } = {}) {
  return useQuery({
    queryKey: ["requestLogMetadataValues"],
    queryFn: async () => {
      const res = await axiosInstance.get(
        endpoints.gateway.requestLogMetadataValues,
      );
      return res.data?.result;
    },
    enabled,
    staleTime: 60000,
  });
}
