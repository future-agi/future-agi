import { useQuery } from "@tanstack/react-query";

import axios, { endpoints } from "src/utils/axios";

export function useRunAnalytics(executionId) {
  return useQuery({
    queryKey: ["simulation-run-analytics-v3", executionId],
    queryFn: () =>
      axios
        .get(endpoints.runResultsV3.analytics(executionId))
        .then((response) => response.data),
    enabled: !!executionId,
    staleTime: 1000 * 60 * 5,
  });
}

export async function exportRunResults(executionId, query = {}) {
  const response = await axios.post(
    endpoints.runResultsV3.export(executionId),
    query,
    { responseType: "blob" },
  );
  const disposition = response.headers?.["content-disposition"] || "";
  const filename =
    disposition.match(/filename="?([^";]+)"?/i)?.[1] ||
    `simulation-run-${executionId}.csv`;
  const url = URL.createObjectURL(response.data);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
