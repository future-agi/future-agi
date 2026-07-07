import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

// The endpoint is paginated call rows, but the parent test_execution
// timestamps (started_at, created_at) live at the top level of the
// response, so page=1 / limit=1 is enough for the header lookup.
export const useTestExecutionDetail = (executionId) => {
  const result = useQuery({
    queryKey: ["test-execution-detail", executionId],
    queryFn: () =>
      axios.get(endpoints.testExecutions.list(executionId), {
        params: { page: 1, limit: 1 },
      }),
    select: (d) => d?.data,
    enabled: Boolean(executionId),
  });

  return {
    data: result.data,
    isLoading: result.isLoading,
  };
};

export default useTestExecutionDetail;
